import os
import json
import time
import platform
import re
from datetime import datetime, timedelta
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

if not chrome_use_debugger and chrome_user_data_dir:
    options.add_argument(f"--user-data-dir={chrome_user_data_dir}")
    options.add_argument(f"--profile-directory={chrome_profile}")

driver = None


def _get_driver() -> webdriver.Chrome:
    global driver
    if driver is not None:
        return driver
    driver = webdriver.Chrome(options=options)
    return driver


def _reset_driver() -> webdriver.Chrome:
    global driver
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass
    driver = webdriver.Chrome(options=options)
    return driver

# ---------- 1. 讀取股票清單 ----------
def load_json(fp: str):
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)

stock_list = load_json('./static/twStock.json')
index_list = load_json('./static/indexAndFuture.json')


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
ETF_DB_NAME = "Investment"
ETF_COLLECTIONS = [
    "etf_00981A",
    "etf_00982A",
    "etf_00991A",
    "etf_00992A",
]
ETF_COMMON_TECH_COLLECTION = "etf_Initiative_tech"


def _get_latest_turnover_collection_name(db) -> str | None:
    candidates = [
        name
        for name in db.list_collection_names()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name)
    ]
    return max(candidates) if candidates else None


def _current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_deviation(price: float | None, ma_value: float | None) -> str:
    if price is None or ma_value in (None, 0):
        return ""
    deviation = (price - ma_value) / ma_value * 100
    return f"{deviation:+.2f}%"


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
            "volume": item.get("volume", ""),
            "close": item.get("close", ""),
            "high": item.get("high", ""),
            "low": item.get("low", ""),
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


def get_etf_common_holdings():
    client = MongoClient(MONGO_URI)
    db = client[ETF_DB_NAME]
    code_name_map: dict[str, str] = {}
    code_counts: dict[str, int] = {}

    for collection_name in ETF_COLLECTIONS:
        doc = db[collection_name].find_one({"_id": "latest"})
        if not doc or not doc.get("data"):
            continue
        for row in doc.get("data", []):
            code = str(row.get("code", "")).strip()
            name = str(row.get("name", "")).strip()
            if not code:
                continue
            code_counts[code] = code_counts.get(code, 0) + 1
            if name and code not in code_name_map:
                code_name_map[code] = name

    threshold = max(len(ETF_COLLECTIONS) - 1, 1)
    eligible = [code for code, count in code_counts.items() if count >= threshold]
    if not eligible:
        return []

    return [
        {"symbol": code, "name": code_name_map.get(code, "")}
        for code in sorted(eligible)
    ]


def _get_tradingview_url(symbol: str) -> str | None:
    info = stock_list.get(symbol)
    if not info:
        return None
    if info.get("market") == "tpex":
        return f"https://tw.tradingview.com/chart/rABGcFih/?symbol=TPEX%3A{symbol}"
    if info.get("market") == "twse":
        return f"https://tw.tradingview.com/chart/rABGcFih/?symbol=TWSE%3A{symbol}"
    return None


def _fetch_tradingview_metrics(symbol: str) -> dict:
    url = _get_tradingview_url(symbol)
    if not url:
        return {}
    return _fetch_tradingview_metrics_by_url(url)


def _fetch_tradingview_metrics_by_url(url: str) -> dict:
    driver = _get_driver()
    try:
        driver.get(url)
    except Exception as exc:
        message = str(exc).lower()
        if "invalid session id" in message or "disconnected" in message:
            driver = _reset_driver()
            driver.get(url)
        else:
            raise

    ma_UpperAll_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[4]/div"
    )
    ma_UpperAll_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, ma_UpperAll_Xpath))
    )
    ma_UpperAll_text = ma_UpperAll_.text.replace(',', '')

    volumeCombo_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[5]/div"
    )
    volumeCombo_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, volumeCombo_Xpath))
    )
    volumeCombo_text = volumeCombo_.text.replace(',', '')

    sqzmom_stronger_value_2DXpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[3]/div[2]/div/div[1]/div/div[2]/div[2]/div[2]/div/div[3]/div"
    )
    sqzmom_stronger_value_2d = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, sqzmom_stronger_value_2DXpath))
    )
    sqzmom_stronger_value_2d_text = sqzmom_stronger_value_2d.text.replace(',', '')

    heikin_Ashi_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[5]/div[2]/div/div[1]/div/div[2]/div[2]/div[2]/div/div[5]/div"
    )
    heikin_Ashi_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, heikin_Ashi_Xpath))
    )
    heikin_Ashi_raw = heikin_Ashi_.text.replace(',', '').strip()
    heikin_Ashi_text = "1" if heikin_Ashi_raw == "∅" else "0"

    ma10_1D_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[2]/div"
    )
    ma10_1D_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, ma10_1D_Xpath))
    )
    ma10_1D_text = ma10_1D_.text.replace(',', '')

    ma5_1D_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[1]/div"
    )
    ma5_1D_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, ma5_1D_Xpath))
    )
    ma5_1D_text = ma5_1D_.text.replace(',', '')

    ma20_1D_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[3]/div"
    )
    ma20_1D_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, ma20_1D_Xpath))
    )
    ma20_1D_text = ma20_1D_.text.replace(',', '')

    close_1D_Xpath = (
        "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[1]/div[1]/div[2]/div/div[5]/div[2]"
    )
    close_1D_ = WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.XPATH, close_1D_Xpath))
    )
    close_1D_text = close_1D_.text.replace(',', '')

    return {
        "ma_UpperAll": ma_UpperAll_text,
        "volumeCombo": volumeCombo_text,
        "sqzmom_stronger_2d": sqzmom_stronger_value_2d_text,
        "heikin_Ashi": heikin_Ashi_text,
        "ma5_1d": ma5_1D_text,
        "ma10_1d": ma10_1D_text,
        "ma20_1d": ma20_1D_text,
        "close": close_1D_text,
    }


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
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[4]/div"
        )
        ma_UpperAll_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, ma_UpperAll_Xpath))
        )
        ma_UpperAll_text = ma_UpperAll_.text.replace(',', '')
        print('get Ma Upper All', ma_UpperAll_text)

        volumeCombo_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[5]/div"
        )
        volumeCombo_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, volumeCombo_Xpath))
        )
        volumeCombo_text = volumeCombo_.text.replace(',', '')
        print('get Volume Combo', volumeCombo_text)

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

        # ma 10 (1d)
        ma10_1D_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[2]/div"
        )
        ma10_1D_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, ma10_1D_Xpath))
        )
        ma10_1D_text = ma10_1D_.text.replace(',', '')
        print('get Ma 10 (1d) Finish', ma10_1D_text)

        # ma 5 (1d)
        ma5_1D_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[1]/div"
        )
        ma5_1D_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, ma5_1D_Xpath))
        )
        ma5_1D_text = ma5_1D_.text.replace(',', '')
        print('get Ma 5 (1d) Finish', ma5_1D_text)

        # ma 10 (1d)
        ma20_1D_Xpath = (
            "/html/body/div[2]/div/div[5]/div[1]/div[1]/div/div[2]/div[1]/div[2]/div/div[1]/div[2]/div[2]/div[2]/div[2]/div/div[3]/div"
        )
        ma20_1D_ = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, ma20_1D_Xpath))
        )
        ma20_1D_text = ma20_1D_.text.replace(',', '')
        print('get Ma 20 (1d) Finish', ma20_1D_text)


        update_wantgoo_doc_by_code(
            date,
            symbol,
            {
                "ma_UpperAll": ma_UpperAll_text,
                "volumeCombo": volumeCombo_text,
                "sqzmom_stronger_2d": sqzmom_stronger_value_2d_text,
                "heikin_Ashi": heikin_Ashi_text,
                "ma5_1d": ma5_1D_text,
                "ma10_1d": ma10_1D_text,
                "ma20_1d": ma20_1D_text,
            }
        )
        time.sleep(1)
        print(f"✅ 已更新 {idx} {name} ({symbol}) 的 TradingView 資料")


def get_tv_data_etf_common() -> None:
    holdings = get_etf_common_holdings()
    if not holdings:
        print("❌ 找不到 ETF 共同持股")
        return

    items: list[dict] = []
    timestamp = _current_timestamp()

    for idx, doc in enumerate(holdings, start=1):
        symbol = doc.get("symbol")
        name = doc.get("name", "")
        print(name, symbol)

        metrics = _fetch_tradingview_metrics(symbol)
        if not metrics:
            print(f"⚠️ 找不到 {symbol}，略過")
            continue

        price_value = _safe_float(metrics.get("close"))
        ma5_value = _safe_float(metrics.get("ma5_1d"))
        ma10_value = _safe_float(metrics.get("ma10_1d"))
        ma20_value = _safe_float(metrics.get("ma20_1d"))

        payload = {
            "no": idx,
            "code": symbol,
            "name": name,
            "close": metrics.get("close", ""),
            "volumeCombo": metrics.get("volumeCombo", ""),
            "sqzmom_stronger_2d": metrics.get("sqzmom_stronger_2d", ""),
            "heikin_Ashi": metrics.get("heikin_Ashi", ""),
            "ma5_1d": metrics.get("ma5_1d", ""),
            "ma10_1d": metrics.get("ma10_1d", ""),
            "ma20_1d": metrics.get("ma20_1d", ""),
            "ma5_dev": _format_deviation(price_value, ma5_value),
            "ma10_dev": _format_deviation(price_value, ma10_value),
            "ma20_dev": _format_deviation(price_value, ma20_value),
            "tv_updated_time": timestamp,
        }
        items.append(payload)
        time.sleep(1)

    client = MongoClient(MONGO_URI)
    collection = client[ETF_DB_NAME][ETF_COMMON_TECH_COLLECTION]
    payload = {
        "_id": "latest",
        "time": timestamp,
        "data": items,
    }
    collection.replace_one({"_id": "latest"}, payload, upsert=True)
    print(f"✅ ETF 共同持股 TradingView 資料已更新，共 {len(items)} 筆")


def get_tv_data_index_tw_code() -> None:
    if not index_list:
        print("❌ 找不到 indexAndFuture.json 內容")
        return

    items: list[dict] = []
    timestamp = _current_timestamp()

    for idx, (key, info) in enumerate(index_list.items(), start=1):
        tw_code = str(info.get("tw_code", "")).strip()
        if not tw_code:
            continue
        url = str(info.get("url", "")).strip()
        if not url:
            print(f"⚠️ {key} 缺少 url，略過")
            continue
        name = str(info.get("ch_name", "")).strip()

        metrics = _fetch_tradingview_metrics_by_url(url)
        if not metrics:
            print(f"⚠️ {key} 無法取得 TradingView 資料，略過")
            continue

        price_value = _safe_float(metrics.get("close"))
        ma5_value = _safe_float(metrics.get("ma5_1d"))
        ma10_value = _safe_float(metrics.get("ma10_1d"))
        ma20_value = _safe_float(metrics.get("ma20_1d"))

        payload = {
            "no": idx,
            "code": tw_code,
            "name": name,
            "close": metrics.get("close", ""),
            "volumeCombo": metrics.get("volumeCombo", ""),
            "sqzmom_stronger_2d": metrics.get("sqzmom_stronger_2d", ""),
            "heikin_Ashi": metrics.get("heikin_Ashi", ""),
            "ma5_1d": metrics.get("ma5_1d", ""),
            "ma10_1d": metrics.get("ma10_1d", ""),
            "ma20_1d": metrics.get("ma20_1d", ""),
            "ma5_dev": _format_deviation(price_value, ma5_value),
            "ma10_dev": _format_deviation(price_value, ma10_value),
            "ma20_dev": _format_deviation(price_value, ma20_value),
            "tv_updated_time": timestamp,
        }
        items.append(payload)
        time.sleep(1)

    client = MongoClient(MONGO_URI)
    collection = client["FutureIndex"]["index"]
    payload = {
        "_id": "latest",
        "time": timestamp,
        "data": items,
    }
    collection.replace_one({"_id": "latest"}, payload, upsert=True)
    print(f"✅ 指數 TradingView 資料已更新，共 {len(items)} 筆")

if __name__ == "__main__":
    START_MINUTES = 9 * 60 + 30
    END_MINUTES = 13 * 60 + 30
    DAILY_RUN = (21, 15)

    def _next_weekday(start: datetime) -> datetime:
        day = start
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    def _next_daytime_run(now: datetime) -> datetime:
        if now.weekday() >= 5:
            next_day = _next_weekday(now + timedelta(days=1))
            return next_day.replace(hour=START_MINUTES // 60, minute=START_MINUTES % 60, second=0, microsecond=0)

        current_minutes = now.hour * 60 + now.minute
        if current_minutes < START_MINUTES:
            return now.replace(hour=START_MINUTES // 60, minute=START_MINUTES % 60, second=0, microsecond=0)

        if current_minutes >= END_MINUTES:
            next_day = _next_weekday(now + timedelta(days=1))
            return next_day.replace(hour=START_MINUTES // 60, minute=START_MINUTES % 60, second=0, microsecond=0)

        next_minutes = ((current_minutes // 30) + 1) * 30
        if next_minutes > END_MINUTES:
            next_day = _next_weekday(now + timedelta(days=1))
            return next_day.replace(hour=START_MINUTES // 60, minute=START_MINUTES % 60, second=0, microsecond=0)

        return now.replace(hour=next_minutes // 60, minute=next_minutes % 60, second=0, microsecond=0)

    def _next_run_time(now: datetime) -> datetime:
        daily_target = now.replace(hour=DAILY_RUN[0], minute=DAILY_RUN[1], second=0, microsecond=0)
        if daily_target <= now:
            daily_target = (now + timedelta(days=1)).replace(
                hour=DAILY_RUN[0], minute=DAILY_RUN[1], second=0, microsecond=0
            )

        daytime_target = _next_daytime_run(now)
        return min(daily_target, daytime_target)

    while True:
        now = datetime.now()
        next_run = _next_run_time(now)
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
        get_tv_data_etf_common()
        get_tv_data_index_tw_code()
