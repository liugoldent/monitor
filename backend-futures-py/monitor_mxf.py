import os
import csv
from pathlib import Path
import requests
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from pymongo import MongoClient


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


load_env_file()

MONGO_URI = require_env("MONGO_URI")
API_URL = "https://market-data-api.futures-ai.com/chip960_tradeinfo/"
DB_NAME = "mxf_futures"
TZ = ZoneInfo("Asia/Taipei")
CSV_PATH = Path(__file__).resolve().parent / "tv_doc" / "mxf_value.csv"


def fetch_tradeinfo() -> object:
    response = requests.get(API_URL, timeout=20)
    response.raise_for_status()
    return response.json()


def normalize_documents(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return [payload]

    return [{"value": payload}]


def insert_tradeinfo(payload: object, collection_name: str, now: datetime) -> None:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[collection_name]

    docs = normalize_documents(payload)
    if not docs:
        print("⚠️ 沒有資料可插入。")
        return
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for doc in docs:
        doc.setdefault("time", timestamp)

    if len(docs) == 1:
        collection.insert_one(docs[0])
        fetch_time = now.strftime("%y-%m-%d %H-%M")
        print(f"{fetch_time} ✅ 成功插入 1 筆資料到集合 {collection_name}")
    else:
        collection.insert_many(docs)
        print(f"✅ 成功插入 {len(docs)} 筆資料到集合 {collection_name}")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _get_signal(tx_bvav: float | None, mtx_bvav: float | None, mtx_tbta: float | None) -> str:
    if tx_bvav is None or mtx_bvav is None or mtx_tbta is None:
        return "none"
    if tx_bvav > 0 and mtx_bvav > 0 and mtx_tbta < 0:
        return "bull"
    if tx_bvav < 0 and mtx_bvav < 0 and mtx_tbta > 0:
        return "bear"
    return "none"


def append_tradeinfo_csv(payload: object, now: datetime) -> None:
    docs = normalize_documents(payload)
    if not docs:
        return

    doc = docs[0]
    tx_bvav = _to_float(doc.get("tx_bvav"))
    mtx_bvav = _to_float(doc.get("mtx_bvav"))
    mtx_tbta = _to_float(doc.get("mtx_tbta"))
    signal = _get_signal(tx_bvav, mtx_bvav, mtx_tbta)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_PATH.exists()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not file_exists:
            writer.writerow(["time", "tx_bvav", "mtx_bvav", "mtx_tbta", "signal"])
        writer.writerow([timestamp, tx_bvav, mtx_bvav, mtx_tbta, signal])


def get_collection_name(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def is_market_open(now: datetime) -> bool:
    weekday = now.weekday()  # Mon=0 ... Sun=6
    current_time = now.time()

    day_session = dt_time(8, 45) <= current_time <= dt_time(13, 45)
    night_session = dt_time(15, 0) <= current_time <= dt_time(23, 59, 59)
    early_session = dt_time(0, 0) <= current_time <= dt_time(5, 0)

    if weekday <= 4:
        return day_session or night_session
    if weekday == 5:
        return early_session
    return False


def sleep_until_next_minute() -> None:
    now = datetime.now(TZ)
    sleep_seconds = 60 - now.second
    if sleep_seconds <= 0:
        sleep_seconds = 60
    time.sleep(sleep_seconds)


def main() -> None:
    while True:
        now = datetime.now(TZ)
        if now.second == 0 and is_market_open(now):
            try:
                payload = fetch_tradeinfo()
                collection_name = get_collection_name(now)
                insert_tradeinfo(payload, collection_name, now)
                append_tradeinfo_csv(payload, now)
            except Exception as exc:
                print(f"❌ 打 API 或寫入失敗: {exc}")
        sleep_until_next_minute()


if __name__ == "__main__":
    print('=== 坦克、炮灰、游擊手資料監控 ===')
    main()
