import os
import csv
from pathlib import Path
import requests
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from pymongo import MongoClient

DISCORD_MXF_ALERT_WEBHOOK_ENV = "DISCORD_MXF_ALERT_WEBHOOK_URL"
DISCORD_MXF_VALUE_WEBHOOK_ENV = "DISCORD_MXF_VALUE_WEBHOOK_URL"
LAST_ALERT_STATE: str | None = None
LAST_ALIVE_SENT_SLOT: tuple[str, int] | None = None
H_TRADE_CSV_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_trade.csv"
WEBHOOK_DATA_1MIN_PATH = Path(__file__).resolve().parent / "tv_doc" / "webhook_data_1min.csv"
MTX_BVAV_AVG_WINDOW = 23
MXF_CSV_HEADER = ["time", "tx_bvav", "mtx_bvav", "mtx_tbta", "mtx_bvav_avg", "signal", "trend"]

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

WEBHOOK_URL = os.getenv(DISCORD_MXF_ALERT_WEBHOOK_ENV, "").strip()
MXF_VALUE_WEBHOOK_URL = os.getenv(DISCORD_MXF_VALUE_WEBHOOK_ENV, "").strip()
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


def _format_int(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(round(value)))


def _format_display_int(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "-"
    return _format_int(number)


def _get_signal(tx_bvav: float | None, mtx_bvav: float | None) -> str:
    if tx_bvav is None or mtx_bvav is None:
        return "none"
    if tx_bvav > 0 and mtx_bvav > 0:
        return "bull"
    if tx_bvav < 0 and mtx_bvav < 0:
        return "bear"
    return "none"


def _get_trend(mtx_bvav: float | None, mtx_bvav_avg: float | None) -> str:
    if mtx_bvav is None or mtx_bvav_avg is None:
        return "none"
    if mtx_bvav > mtx_bvav_avg:
        return "gold"
    if mtx_bvav < mtx_bvav_avg:
        return "death"
    return "none"


def _format_trend_label(trend: str) -> str:
    if trend == "gold":
        return "金叉"
    if trend == "death":
        return "死叉"
    return "無"


def _format_recommendation(signal: str) -> str:
    if signal == "bull":
        return "做多"
    if signal == "bear":
        return "做空"
    return "觀望"


def _read_mtx_bvav_history() -> list[float]:
    if not CSV_PATH.exists():
        return []

    values: list[float] = []
    with CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = _to_float(row.get("mtx_bvav"))
            if value is not None:
                values.append(value)
    return values


def _calculate_mtx_bvav_avg(current_value: float | None) -> float | None:
    if current_value is None:
        return None

    history = _read_mtx_bvav_history()
    window = history[-(MTX_BVAV_AVG_WINDOW - 1):]
    window.append(current_value)
    if len(window) < MTX_BVAV_AVG_WINDOW:
        return None
    return sum(window) / len(window)


def _ensure_mxf_csv_header() -> None:
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(MXF_CSV_HEADER)
        return

    try:
        with CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except Exception:
        return

    if not rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(MXF_CSV_HEADER)
        return

    current_header = rows[0]
    if current_header == MXF_CSV_HEADER:
        return

    data_rows = rows[1:]
    normalized_rows: list[list[str]] = []
    for row in data_rows:
        record = {
            name: row[index] if index < len(row) else ""
            for index, name in enumerate(current_header)
        }
        tx_num = _to_float(record.get("tx_bvav"))
        mtx_num = _to_float(record.get("mtx_bvav"))
        avg_num = _to_float(record.get("mtx_bvav_avg"))
        signal = record.get("signal") or _get_signal(tx_num, mtx_num)
        trend = record.get("trend") or _get_trend(mtx_num, avg_num)
        normalized_rows.append([
            record.get("time", ""),
            record.get("tx_bvav", ""),
            record.get("mtx_bvav", ""),
            record.get("mtx_tbta", ""),
            record.get("mtx_bvav_avg", ""),
            signal,
            trend,
        ])

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MXF_CSV_HEADER)
        writer.writerows(normalized_rows)


def append_tradeinfo_csv(payload: object, now: datetime) -> dict[str, object] | None:
    docs = normalize_documents(payload)
    if not docs:
        return None

    doc = docs[0]
    tx_bvav = _to_float(doc.get("tx_bvav"))
    mtx_tbta = _to_float(doc.get("mtx_tbta"))
    mtx_bvav = _to_float(doc.get("mtx_bvav"))
    mtx_bvav_avg = _calculate_mtx_bvav_avg(mtx_bvav)
    signal = _get_signal(tx_bvav, mtx_bvav)
    trend = _get_trend(mtx_bvav, mtx_bvav_avg)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_mxf_csv_header()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            timestamp,
            _format_int(tx_bvav),
            _format_int(mtx_bvav),
            _format_int(mtx_tbta),
            _format_int(mtx_bvav_avg),
            signal,
            trend,
        ])

    return {
        "time": timestamp,
        "tx_bvav": tx_bvav,
        "mtx_tbta": mtx_tbta,
        "mtx_bvav": mtx_bvav,
        "mtx_bvav_avg": mtx_bvav_avg,
        "signal": signal,
        "trend": trend,
    }


def build_mxf_value_discord_message(snapshot: dict[str, object], now: datetime) -> str:
    timestamp = str(snapshot.get("time") or now.strftime("%Y-%m-%d %H:%M:%S"))
    return " / ".join([
        f"日期: {timestamp}",
        f"外資: {_format_display_int(snapshot.get('tx_bvav'))}",
        f"散戶: {_format_display_int(snapshot.get('mtx_tbta'))}",
        f"游擊: {_format_display_int(snapshot.get('mtx_bvav'))}",
    ])


def send_discord_message(message: str, webhook_url: str | None = None) -> None:
    target_url = WEBHOOK_URL if webhook_url is None else webhook_url.strip()
    if not target_url:
        raise RuntimeError(
            f"Missing Discord webhook URL: {DISCORD_MXF_ALERT_WEBHOOK_ENV}"
            f" or {DISCORD_MXF_VALUE_WEBHOOK_ENV}"
        )

    response = requests.post(target_url, json={"content": message}, timeout=20)
    response.raise_for_status()


def read_latest_trade_side() -> str | None:
    if not H_TRADE_CSV_PATH.exists():
        return None

    latest_side: str | None = None
    with H_TRADE_CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            side = (row.get("side") or "").strip().lower()
            if side in {"bear", "bull"}:
                latest_side = side
    return latest_side


def _read_latest_webhook_rows(limit: int = 2) -> list[dict]:
    if not WEBHOOK_DATA_1MIN_PATH.exists():
        return []

    try:
        with WEBHOOK_DATA_1MIN_PATH.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []

    return rows[-limit:] if len(rows) >= limit else rows


def check_mtx_bvav_alert() -> None:
    global LAST_ALERT_STATE

    latest_side = read_latest_trade_side()
    latest_rows = _read_latest_webhook_rows(2)
    if len(latest_rows) < 2:
        LAST_ALERT_STATE = None
        return

    prev_row, curr_row = latest_rows[-2], latest_rows[-1]
    prev_close = _to_float(prev_row.get("Close"))
    curr_close = _to_float(curr_row.get("Close"))
    prev_ma_p200 = _to_float(prev_row.get("MA_P200"))
    curr_ma_p200 = _to_float(curr_row.get("MA_P200"))
    prev_ma_n200 = _to_float(prev_row.get("MA_N200"))
    curr_ma_n200 = _to_float(curr_row.get("MA_N200"))

    if latest_side == "bear":
        if (
            prev_close is not None
            and curr_close is not None
            and prev_ma_p200 is not None
            and curr_ma_p200 is not None
            and prev_close > prev_ma_p200
            and curr_close < curr_ma_p200
        ):
            alert_state = "short"
            message = (
                "shortCycle 空單加碼訊號："
                f"close 由 {prev_close} 跌破 MA_P200 {curr_ma_p200}"
            )
        else:
            LAST_ALERT_STATE = None
            return
    elif latest_side == "bull":
        if (
            prev_close is not None
            and curr_close is not None
            and prev_ma_n200 is not None
            and curr_ma_n200 is not None
            and prev_close < prev_ma_n200
            and curr_close > curr_ma_n200
        ):
            alert_state = "long"
            message = (
                "shortCycle 多單加碼訊號："
                f"close 由 {prev_close} 穿越 MA_N200 {curr_ma_n200}"
            )
        else:
            LAST_ALERT_STATE = None
            return
    else:
        LAST_ALERT_STATE = None
        return

    if LAST_ALERT_STATE == alert_state:
        return

    try:
        send_discord_message(message)
        LAST_ALERT_STATE = alert_state
        print(f"📣 已送出 Discord 通知: {message}")
    except Exception as exc:
        print(f"❌ 發送 Discord 通知失敗: {exc}")


def check_service_alive_alert(now: datetime) -> None:
    global LAST_ALIVE_SENT_SLOT

    if now.minute not in {0, 30}:
        return

    half_hour = 0 if now.minute == 0 else 1
    slot = (now.strftime("%Y-%m-%d"), now.hour, half_hour)
    if LAST_ALIVE_SENT_SLOT == slot:
        return

    minute_label = "00" if half_hour == 0 else "30"
    message = f"服務還啟動著 - {now.strftime('%Y-%m-%d %H')}:{minute_label}"
    try:
        send_discord_message(message)
        LAST_ALIVE_SENT_SLOT = slot
        print(f"📣 已送出服務存活通知: {message}")
    except Exception as exc:
        print(f"❌ 發送服務存活通知失敗: {exc}")


def get_collection_name(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def is_market_open(now: datetime) -> bool:
    weekday = now.weekday()  # Mon=0 ... Sun=6
    current_time = now.time()

    # 日盤：週一到週五 08:45~13:45
    day_session = weekday <= 4 and dt_time(8, 45) <= current_time <= dt_time(13, 45)

    # 夜盤：週一到週五 15:00~23:59:59
    night_session = weekday <= 4 and dt_time(15, 0) <= current_time <= dt_time(23, 59, 59)

    # 凌晨盤：週一到週六 00:00~05:00
    early_session = weekday <= 5 and dt_time(0, 0) <= current_time <= dt_time(5, 0)

    return day_session or night_session or early_session


def sleep_until_next_minute() -> None:
    now = datetime.now(TZ)
    sleep_seconds = 60 - now.second
    if sleep_seconds <= 0:
        sleep_seconds = 60
    time.sleep(sleep_seconds)


def main() -> None:
    while True:
        now = datetime.now(TZ)
        try:
            # check_mtx_bvav_alert()
            check_service_alive_alert(now)
        except Exception as exc:
            print(f"❌ 檢查通知狀態失敗: {exc}")

        if is_market_open(now):
            try:
                payload = fetch_tradeinfo()
                collection_name = get_collection_name(now)
                insert_tradeinfo(payload, collection_name, now)
                check_service_alive_alert(now)
            except Exception as exc:
                print(f"❌ 打 API 或寫入失敗: {exc}")
        sleep_until_next_minute()


if __name__ == "__main__":
    print('=== 坦克、炮灰、游擊手資料監控 ===')
    main()
