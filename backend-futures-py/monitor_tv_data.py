import os
import json
import time
import platform
import re
from datetime import datetime
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
import pandas as pd
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pymongo import MongoClient, UpdateOne


options = Options()

# service = Service()  # 自動找到 chromedriver
# 以環境變數調整 Chrome 啟動方式。
# 預設（未設環境變數時）：GUI + 連接 127.0.0.1:9222（與原本流程相同，適合你先開啟 remote debug 的 Chrome 再跑腳本）
# Dockerfile 會將 CHROME_HEADLESS 設為 true，容器內則走 headless Chromium。
chrome_headless_env = os.getenv("CHROME_HEADLESS")  # "true"/"false"
chrome_use_debugger = os.getenv("CHROME_USE_DEBUGGER", "true").lower() == "true"
chrome_debugger = os.getenv("CHROME_DEBUGGER_ADDRESS")
chrome_user_data_dir = os.getenv("CHROME_USER_DATA_DIR")
chrome_profile = os.getenv("CHROME_PROFILE", "Default")

# 若未指定 headless，預設 False（沿用你原本 GUI + remote debug 的使用習慣）
use_headless = chrome_headless_env.lower() == "true" if chrome_headless_env else False

# 非 headless 才預設使用 debugger，地址未指定時 fallback 127.0.0.1:9222
if not use_headless and chrome_use_debugger:
    options.debugger_address = chrome_debugger or "127.0.0.1:9222"

# headless 模式（容器內預設開啟）
if use_headless:
    options.add_argument("--headless=new")

options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")

# 預設 user-data-dir: 若未指定且在 macOS，沿用原本資料夾
if not chrome_user_data_dir and platform.system() == "Darwin":
    chrome_user_data_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")

if chrome_user_data_dir:
    options.add_argument(f"--user-data-dir={chrome_user_data_dir}")
    options.add_argument(f"--profile-directory={chrome_profile}")

driver = webdriver.Chrome(options=options)

# ---------- 1. 讀取股票清單 ----------
def load_json(fp: str):
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)

stock_list = load_json('./static/twStock.json')


def load_env_file(path: str) -> None:
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


ENV_PATH = Path(__file__).resolve().parent / ".env"
load_env_file(str(ENV_PATH))
MONGO_URI = require_env("MONGO_URI")
TURNOVER_DB_NAME = "yahoo_turnover"
WANTGOO_DB_NAME = "yahoo_turnover_tech"


def _get_latest_turnover_collection_name(db) -> str | None:
    candidates = [
        name
        for name in db.list_collection_names()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name)
    ]
    return max(candidates) if candidates else None


def _current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _upsert_yahoo_turnover_items(collection_name: str, items: list[dict]) -> None:
    if not items:
        return

    client = MongoClient(MONGO_URI)
    db = client[WANTGOO_DB_NAME]
    collection = db[collection_name]
    collection.delete_many({})
    timestamp = _current_timestamp()
    operations = []

    for item in items:
        code = str(item.get("code", "")).strip()
        if not code:
            continue
        payload = {
            "no": item.get("no"),
            "code": code,
            "name": item.get("name", ""),
            "close": item.get("close", ""),
            "tv_updated_time": timestamp,
        }
        operations.append(UpdateOne({"code": code}, {"$set": payload}, upsert=True))

    if operations:
        collection.bulk_write(operations, ordered=False)


def update_wantgoo_doc_by_code(date: str, symbol: str, payload: dict) -> None:
    client = MongoClient(MONGO_URI)
    db = client[WANTGOO_DB_NAME]
    collection = db[date]
    payload = {**payload, "tv_updated_time": _current_timestamp()}
    collection.update_one({"code": symbol}, {"$set": payload}, upsert=True)


def get_yahoo_turnover():
    client = MongoClient(MONGO_URI)
    db = client[TURNOVER_DB_NAME]
    collection_name = _get_latest_turnover_collection_name(db)
    if not collection_name:
        return pd.DataFrame(), ""

    doc = db[collection_name].find_one({"_id": "latest"})
    if not doc or not doc.get("data"):
        return pd.DataFrame(), collection_name

    data_items = doc.get("data", [])
    _upsert_yahoo_turnover_items(collection_name, data_items)

    rows = []
    for item in data_items:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code:
            continue
        rows.append({"symbol": code, "name": name})

    return pd.DataFrame(rows), collection_name


# ---------- 主程式 ----------
def get_tv_dataT():
    all_docs, latest_date = get_yahoo_turnover()
    if latest_date:
        date = latest_date
    if all_docs is None or all_docs.empty:
        print("❌ 找不到任何文件")
        return []
    start_idx = 0
    end_idx = len(all_docs)

    # https://tw.tradingview.com/chart/rABGcFih/?symbol=TWSE%3A2454
    # https://tw.tradingview.com/chart/rABGcFih/?symbol=TPEX%3A8069
    # 1. 取得 data 欄位（如果沒有就回傳空 list）
    for idx, doc in all_docs.iloc[start_idx:end_idx].iterrows():
        url = ''

        # _id = doc["_id"]
        symbol = doc.get("symbol")
        name = doc.get("name")
        print(name, symbol)

        info = stock_list.get(symbol)  # 取不到會回傳 None
        if not info:
            print(f"⚠️ 找不到 {symbol}，略過")
            continue

        if stock_list[symbol]["market"] == 'tpex':
            url = f"https://tw.tradingview.com/chart/rABGcFih/?symbol=TPEX%3A{symbol}"

        if stock_list[symbol]["market"] == 'twse':
            url = f"https://tw.tradingview.com/chart/rABGcFih/?symbol=TWSE%3A{symbol}"

        driver.get(url)

        # ma UpperAll
        ma_UpperAll_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[3]/div[2]/div/div[4]/div"
        )
        ma_UpperAll_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, ma_UpperAll_Xpath))
        )
        ma_UpperAll_text = ma_UpperAll_.text.replace(',', '')
        print('get Ma Upper All', ma_UpperAll_text)

        # 2D SQZMOM Stronger
        sqzmom_stronger_value_2DXpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[3]/div[2]/div/div[1]/div/div[2]/div[2]/div[2]/div/div[3]/div"
        )
        sqzmom_stronger_value_2d = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, sqzmom_stronger_value_2DXpath))
        )
        sqzmom_stronger_value_2d_text = sqzmom_stronger_value_2d.text.replace(',', '')
        print('get SQZMOM_stronger 2D Finish', sqzmom_stronger_value_2d_text)

        # heikin Ashi
        heikin_Ashi_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[5]/div[2]/div/div[1]/div/div[2]/div[2]/div[2]/div/div[5]/div"
        )
        heikin_Ashi_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, heikin_Ashi_Xpath))
        )
        heikin_Ashi_raw = heikin_Ashi_.text.replace(',', '').strip()
        heikin_Ashi_text = "1" if heikin_Ashi_raw == "∅" else "0"
        print('get Heikin Ashi Finish', heikin_Ashi_text)


        update_wantgoo_doc_by_code(
            date,
            symbol,
            {
                "ma_UpperAll": ma_UpperAll_text,
                "sqzmom_stronger_2d": sqzmom_stronger_value_2d_text,
                "heikin_Ashi": heikin_Ashi_text,
            }
        )
        time.sleep(1)
        print(f"✅ 已更新 {idx} {name} ({symbol}) 的 TradingView 資料")

if __name__ == "__main__":
    get_tv_dataT()
