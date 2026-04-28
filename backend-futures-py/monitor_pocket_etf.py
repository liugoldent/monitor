import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


DB_NAME = "Investment"
ETF_TARGETS = [
    ("00981A", "https://www.pocket.tw/etf/tw/00981A/fundholding/", "etf_00981A"),
    ("00982A", "https://www.pocket.tw/etf/tw/00982A/fundholding/", "etf_00982A"),
    ("00991A", "https://www.pocket.tw/etf/tw/00991A/fundholding/", "etf_00991A"),
    ("00992A", "https://www.pocket.tw/etf/tw/00992A/fundholding/", "etf_00992A"),
]
TZ = ZoneInfo("Asia/Taipei")


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


def _normalize_cell(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _find_header_index(normalized_cells: list[str], aliases: list[str]) -> int | None:
    for idx, cell in enumerate(normalized_cells):
        if any(alias in cell for alias in aliases):
            return idx
    return None


def fetch_holdings(url: str) -> list[dict]:
    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)

        WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table"))
        )

        holdings: list[dict] = []
        tables = driver.find_elements(By.TAG_NAME, "table")
        for table in tables:
            rows = table.find_elements(By.CSS_SELECTOR, "tr")
            if not rows:
                continue

            header_index = None
            code_idx = None
            name_idx = None
            holding_count_idx = None
            weight_idx = None
            for idx, row in enumerate(rows):
                cells = row.find_elements(By.CSS_SELECTOR, "th, td")
                normalized = [_normalize_cell(cell.text) for cell in cells]
                if code_idx is None:
                    code_idx = _find_header_index(normalized, ["代號", "代碼"])
                if name_idx is None:
                    name_idx = _find_header_index(normalized, ["名稱"])
                if holding_count_idx is None:
                    holding_count_idx = _find_header_index(normalized, ["持有數", "持有股數", "持有張數", "持有量"])
                if weight_idx is None:
                    weight_idx = _find_header_index(normalized, ["權重", "比重"])
                if code_idx is not None and name_idx is not None:
                    header_index = idx
                    break

            if header_index is None:
                continue

            required_idx = max(
                idx for idx in [code_idx, name_idx, holding_count_idx, weight_idx] if idx is not None
            )
            for row in rows[header_index + 1 :]:
                cells = row.find_elements(By.CSS_SELECTOR, "td")
                if len(cells) <= required_idx:
                    continue
                code = _normalize_cell(cells[code_idx].text)
                name = _normalize_cell(cells[name_idx].text)
                if not code or not name:
                    continue
                if "代號" in code or "名稱" in name:
                    continue
                holding_count = _normalize_cell(cells[holding_count_idx].text) if holding_count_idx is not None and len(cells) > holding_count_idx else ""
                weight = _normalize_cell(cells[weight_idx].text) if weight_idx is not None and len(cells) > weight_idx else ""
                holdings.append({
                    "code": code,
                    "name": name,
                    "holding_count": holding_count,
                    "weight": weight,
                })

            if holdings:
                break

        if not holdings:
            print("⚠️ 未找到持股明細，請確認網站結構是否變動。")
        return holdings
    except Exception as exc:
        print(f"❌ 抓取持股明細失敗: {exc}")
        return []
    finally:
        if driver is not None:
            driver.quit()


def upsert_holdings(collection_name: str, source_url: str, data: list[dict], now: datetime) -> None:
    if not data:
        print("⚠️ 沒有資料可寫入。")
        return

    mongo_uri = require_env("MONGO_URI")
    client = MongoClient(mongo_uri)
    db = client[DB_NAME]
    collection = db[collection_name]

    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "time": timestamp,
        "source": source_url,
        "data": data,
    }

    # 保留最新資料給現有 API 使用
    latest_payload = {
        "_id": "latest",
        "time": timestamp,
        "source": source_url,
        "data": data,
    }
    collection.replace_one({"_id": "latest"}, latest_payload, upsert=True)

    # 每次執行都新增一筆歷史資料，不覆蓋舊紀錄
    history_payload = {
        "_id": f"{timestamp}-{uuid.uuid4().hex}",
        **payload,
    }
    collection.insert_one(history_payload)
    print(f"✅ 已新增 {collection_name} 持股明細快照，時間 {timestamp} 共 {len(data)} 筆")


def run_once() -> None:
    now = datetime.now(TZ)
    for symbol, url, collection_name in ETF_TARGETS:
        print(f"📌 下載 {symbol} 持股明細...")
        holdings = fetch_holdings(url)
        upsert_holdings(collection_name, url, holdings, now)


def next_run_time(now: datetime) -> datetime:
    candidate = now.replace(hour=21, minute=30, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def main() -> None:
    while True:
        try:
            now = datetime.now(TZ)
            next_run = next_run_time(now)
            sleep_seconds = max(0, (next_run - now).total_seconds())
            if sleep_seconds:
                print(f"⏳ Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                remaining = int(sleep_seconds)
                while remaining > 0:
                    minutes_left = (remaining + 59) // 60
                    print(f"⏳ Countdown: {minutes_left} minute(s) remaining")
                    sleep_chunk = 60 if remaining > 60 else remaining
                    time.sleep(sleep_chunk)
                    remaining -= sleep_chunk
            run_once()
        except Exception as exc:
            print(f"❌ 執行失敗: {exc}")
            time.sleep(30)


if __name__ == "__main__":
    load_env_file()
    print("=== Pocket ETF 00981A 持股明細監控 ===")
    main()
