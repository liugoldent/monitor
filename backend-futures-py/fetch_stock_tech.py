import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
STOCK_LIST_PATH = BASE_DIR / "static" / "twStock.json"
OUTPUT_DIR = BASE_DIR / "stockTech"
ETF_DB_NAME = "Investment"
ETF_COLLECTIONS = [
    ("etf_00981A", "00981A"),
    ("etf_00982A", "00982A"),
    ("etf_00991A", "00991A"),
    ("etf_00992A", "00992A"),
]
MA_WINDOWS = (5, 10, 20, 60)
TZ = ZoneInfo("Asia/Taipei")
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}
CURL_USER_AGENT = "Mozilla/5.0"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for line in path.read_text().splitlines():
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


def load_stock_list() -> dict[str, dict[str, Any]]:
    if not STOCK_LIST_PATH.exists():
        return {}
    with STOCK_LIST_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {}


def normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


def get_etf_common_holdings(min_count: int | None = None) -> dict[str, dict[str, Any]]:
    client = MongoClient(require_env("MONGO_URI"))
    db = client[ETF_DB_NAME]
    threshold = min_count or max(len(ETF_COLLECTIONS) - 1, 1)
    holdings: dict[str, dict[str, Any]] = {}
    latest_times: dict[str, str] = {}

    for collection_name, etf_symbol in ETF_COLLECTIONS:
        doc = db[collection_name].find_one({"_id": "latest"})
        if not doc:
            continue

        latest_times[etf_symbol] = str(doc.get("time") or "")
        data = doc.get("data", [])
        if not isinstance(data, list):
            continue

        seen_in_etf: set[str] = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("code"))
            if not code or code in seen_in_etf:
                continue

            seen_in_etf.add(code)
            item = holdings.setdefault(
                code,
                {
                    "code": code,
                    "name": str(row.get("name") or "").strip(),
                    "holding_etf_count": 0,
                    "etfs": [],
                },
            )
            if not item.get("name"):
                item["name"] = str(row.get("name") or "").strip()
            item["holding_etf_count"] += 1
            item["etfs"].append(
                {
                    "etf": etf_symbol,
                    "holding_count": row.get("holding_count", ""),
                    "weight": row.get("weight", ""),
                    "source_time": latest_times.get(etf_symbol, ""),
                }
            )

    eligible = {
        code: item
        for code, item in holdings.items()
        if int(item.get("holding_etf_count") or 0) >= threshold
    }
    return dict(sorted(eligible.items()))


def yahoo_symbol_for(code: str, stock_list: dict[str, dict[str, Any]]) -> str:
    stock_info = stock_list.get(code, {})
    market = str(stock_info.get("market") or "").lower()
    suffix = ".TWO" if market == "tpex" else ".TW"
    return f"{code}{suffix}"


def fetch_yahoo_chart(symbol: str) -> dict[str, Any]:
    url = YAHOO_CHART_URL.format(symbol=quote(symbol, safe="."))
    params = {"range": "6mo", "interval": "1d"}
    try:
        response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 429:
            raise
        payload = fetch_yahoo_chart_with_curl(url)

    error = payload.get("chart", {}).get("error")
    if error:
        raise RuntimeError(f"Yahoo chart error for {symbol}: {error}")
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo chart has no result for {symbol}")
    return result[0]


def fetch_yahoo_chart_with_curl(url: str) -> dict[str, Any]:
    curl_url = f"{url}?range=6mo&interval=1d"
    completed = subprocess.run(
        [
            "curl",
            "-sS",
            "--fail",
            "--retry",
            "2",
            "-A",
            CURL_USER_AGENT,
            curl_url,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout)


def date_from_timestamp(timestamp: int | float) -> str:
    return datetime.fromtimestamp(timestamp, TZ).strftime("%Y-%m-%d")


def calculate_technical(chart: dict[str, Any]) -> dict[str, Any]:
    timestamps = chart.get("timestamp") or []
    quote_data = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    volumes = quote_data.get("volume") or []

    rows = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        rows.append(
            {
                "date": date_from_timestamp(timestamp),
                "open": opens[index] if index < len(opens) else None,
                "high": highs[index] if index < len(highs) else None,
                "low": lows[index] if index < len(lows) else None,
                "close": close,
                "volume": volumes[index] if index < len(volumes) else None,
            }
        )

    if len(rows) < max(MA_WINDOWS):
        raise RuntimeError(f"Not enough daily bars: got {len(rows)}")

    last = rows[-1]
    moving_averages = {
        f"ma{window}": round(
            sum(float(row["close"]) for row in rows[-window:]) / window,
            2,
        )
        for window in MA_WINDOWS
    }
    market_time = (chart.get("meta") or {}).get("regularMarketTime")

    return {
        "date": last["date"],
        "close": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "volume": last["volume"],
        **moving_averages,
        "market_time": date_from_timestamp(market_time) if market_time else "",
        "bars_used": len(rows),
    }


def build_payload(min_count: int | None = None, sleep_seconds: float = 0.35) -> dict[str, Any]:
    stock_list = load_stock_list()
    holdings = get_etf_common_holdings(min_count=min_count)
    generated_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    threshold = min_count or max(len(ETF_COLLECTIONS) - 1, 1)
    items = []
    errors = []

    for code, holding in holdings.items():
        if code not in stock_list:
            continue
        symbol = yahoo_symbol_for(code, stock_list)
        try:
            chart = fetch_yahoo_chart(symbol)
            tech = calculate_technical(chart)
            stock_info = stock_list.get(code, {})
            items.append(
                {
                    **holding,
                    "name": holding.get("name") or stock_info.get("name", ""),
                    "market": stock_info.get("market", ""),
                    "yahoo_symbol": symbol,
                    "technical": tech,
                }
            )
        except Exception as exc:
            errors.append({"code": code, "yahoo_symbol": symbol, "error": str(exc)})
        if sleep_seconds:
            time.sleep(sleep_seconds)

    trading_dates = sorted(
        {
            str(item.get("technical", {}).get("date") or "")
            for item in items
            if item.get("technical", {}).get("date")
        }
    )
    output_date = trading_dates[-1] if trading_dates else datetime.now(TZ).strftime("%Y-%m-%d")

    return {
        "date": output_date,
        "generated_at": generated_at,
        "source": "Yahoo Finance chart API",
        "etf_mode": f"{len(ETF_COLLECTIONS)} ETF 中至少 {threshold} 檔持有",
        "etf_collections": [name for name, _ in ETF_COLLECTIONS],
        "count": len(items),
        "data": items,
        "errors": errors,
    }


def write_payload(payload: dict[str, Any], output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{payload['date']}.json"
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Yahoo MA data for ETF common holdings.")
    parser.add_argument("--min-count", type=int, default=None, help="Minimum ETF holding count.")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds to wait between Yahoo requests.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    load_env_file()
    payload = build_payload(min_count=args.min_count, sleep_seconds=args.sleep)
    output_path = write_payload(payload, args.output_dir)
    print(f"Saved {payload['count']} stocks to {output_path}")
    if payload["errors"]:
        print(f"Errors: {len(payload['errors'])}")


if __name__ == "__main__":
    main()
