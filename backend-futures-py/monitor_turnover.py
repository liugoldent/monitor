import os
import time
from pathlib import Path
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
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
DB_NAME = "yahoo_turnover"
TZ = ZoneInfo("Asia/Taipei")

def get_realtime_turnover():
    # 設定 Chrome 選項
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 不開啟瀏覽器視窗
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # 初始化 WebDriver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    url = "https://tw.stock.yahoo.com/rank/turnover"
    
    try:
        print(f"正在抓取資料：{url} ...")
        driver.get(url)
        
        # 等待頁面加載完成（視網路情況調整秒數）
        time.sleep(3)

        # 找到表格內容
        # Yahoo 的排行榜通常在一個列表容器中，這裡直接抓取整個清單
        rows = driver.find_elements(By.CSS_SELECTOR, r'li.List\(n\)')
        
        data_list = []
        
        for row in rows:
            # 提取各欄位資料，Yahoo 結構可能會變動，以下是針對目前的 CSS 類別
            try:
                # 這裡使用相對路徑提取
                name_code = row.find_element(By.CSS_SELECTOR, r'.Lh\(20px\)').text
                price = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[0].text
                change = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[1].text
                change_percent = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[2].text
                volume = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[3].text
                turnover = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[4].text
                
                data_list.append({
                    "股票名稱/代碼": name_code.replace('\n', ' '),
                    "成交價": price,
                    "漲跌": change,
                    "幅度": change_percent,
                    "成交量(張)": volume,
                    "成交值(億)": turnover
                })
            except:
                continue # 略過標頭或其他非資料行

        # 轉成 Pandas DataFrame
        df = pd.DataFrame(data_list)
        return df

    except Exception as e:
        print(f"發生錯誤: {e}")
        return None
    finally:
        driver.quit()


def get_collection_name(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def is_market_open(now: datetime) -> bool:
    weekday = now.weekday()  # Mon=0 ... Sun=6
    current_time = now.time()
    day_session = dt_time(9, 0) <= current_time <= dt_time(13, 30)
    return weekday <= 4 and day_session


def sleep_until_next_minute() -> None:
    now = datetime.now(TZ)
    sleep_seconds = 60 - now.second
    if sleep_seconds <= 0:
        sleep_seconds = 60
    time.sleep(sleep_seconds)


def upsert_turnover(df: pd.DataFrame, collection_name: str, now: datetime) -> None:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[collection_name]

    top_df = df.head(100)
    if top_df.empty:
        print("⚠️ 沒有資料可寫入。")
        return

    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for idx, (_, row) in enumerate(top_df.iterrows(), start=1):
        name_code = str(row.get("股票名稱/代碼", "")).replace("\n", " ").strip()
        close_price = str(row.get("成交價", "")).strip()
        records.append({
            "no": idx,
            "name": name_code,
            "close": close_price,
            "time": timestamp,
        })

    payload = {
        "_id": "latest",
        "data": records,
    }
    collection.replace_one({"_id": "latest"}, payload, upsert=True)
    print(f"✅ 已覆蓋 {collection_name} 最新資料，共 {len(records)} 筆")


def main() -> None:
    while True:
        now = datetime.now(TZ)
        if now.second == 0 and is_market_open(now):
            try:
                df_result = get_realtime_turnover()
                if df_result is not None and not df_result.empty:
                    collection_name = get_collection_name(now)
                    upsert_turnover(df_result, collection_name, now)
                else:
                    print("未能抓取到資料。")
            except Exception as exc:
                print(f"❌ 抓取或寫入失敗: {exc}")
        sleep_until_next_minute()


if __name__ == "__main__":
    print("=== 即時成交值排行榜監控 ===")
    main()
