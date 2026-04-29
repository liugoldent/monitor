import os
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import re
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient, DESCENDING


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
DB_NAME = "stock_futures"
MXF_DB_NAME = "mxf_futures"
ETF_DB_NAME = "Investment"
ETF_COLLECTIONS = [
    ("etf_00981A", "00981A"),
    ("etf_00982A", "00982A"),
    ("etf_00991A", "00991A"),
    ("etf_00992A", "00992A"),
]
ETF_COMMON_TECH_COLLECTION = "etf_Initiative_tech"
FUTURE_INDEX_DB_NAME = "FutureIndex"
FUTURE_INDEX_COLLECTION = "index"
TZ = ZoneInfo("Asia/Taipei")
DEFAULT_DISCORD_WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
PRICE_UP_JSON_PATH = Path(__file__).resolve().parent / "tv_doc" / "priceUp.json"

mongo_client = MongoClient(MONGO_URI)

from openai import OpenAI
from auto_trade_IntradayOdd import place_intraday_odd_lot
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def get_collection_name(date_str: str | None) -> str:
    if date_str:
        return date_str
    return datetime.now(TZ).strftime("%Y-%m-%d")


def fetch_latest_payload(date_str: str | None) -> dict:
    collection_name = get_collection_name(date_str)
    collection = mongo_client[DB_NAME][collection_name]
    doc = collection.find_one(sort=[("time", DESCENDING), ("_id", DESCENDING)])
    if not doc:
        return {}

    doc.pop("_id", None)
    doc.pop("time", None)
    return doc


def _get_latest_collection_name(db) -> str | None:
    candidates = [
        name
        for name in db.list_collection_names()
        if len(name) == 10 and name[4] == "-" and name[7] == "-"
    ]
    return max(candidates) if candidates else None


def fetch_latest_mxf(date_str: str | None) -> dict:
    collection_name = get_collection_name(date_str)
    collection = mongo_client[MXF_DB_NAME][collection_name]
    doc = collection.find_one(sort=[("time", DESCENDING), ("_id", DESCENDING)])
    if not doc:
        return {}

    return {
        "tx_bvav": doc.get("tx_bvav"),
        "mtx_tbta": doc.get("mtx_tbta"),
        "mtx_bvav": doc.get("mtx_bvav"),
        "time": doc.get("time"),
    }


def _get_mxf_signal(tx_bvav: float | None, mtx_bvav: float | None, mtx_tbta: float | None) -> str:
    if tx_bvav is None or mtx_bvav is None or mtx_tbta is None:
        return "none"
    if tx_bvav > 0 and mtx_bvav > 0 and mtx_tbta < 0:
        return "bull"
    if tx_bvav < 0 and mtx_bvav < 0 and mtx_tbta > 0:
        return "bear"
    return "none"


def fetch_mxf_series(date_str: str | None) -> dict:
    db = mongo_client[MXF_DB_NAME]
    collection_name = get_collection_name(date_str) if date_str else None
    docs = []
    if collection_name:
        docs = list(db[collection_name].find({}, {"_id": 0}).sort("time", 1))

    if not docs:
        fallback_name = _get_latest_collection_name(db)
        if not fallback_name:
            return {}
        docs = list(db[fallback_name].find({}, {"_id": 0}).sort("time", 1))
        collection_name = fallback_name

    data = []
    for doc in docs:
        tx_bvav = doc.get("tx_bvav")
        mtx_bvav = doc.get("mtx_bvav")
        mtx_tbta = doc.get("mtx_tbta")
        data.append({
            "time": doc.get("time"),
            "tx_bvav": tx_bvav,
            "mtx_bvav": mtx_bvav,
            "mtx_tbta": mtx_tbta,
            "signal": _get_mxf_signal(tx_bvav, mtx_bvav, mtx_tbta),
        })

    return {"data": data, "collection_name": collection_name}


def fetch_etf_holdings_counts() -> dict:
    db = mongo_client[ETF_DB_NAME]
    code_counts: dict[str, int] = {}
    code_etfs: dict[str, list[str]] = {}
    latest_time = None

    for collection_name, etf_symbol in ETF_COLLECTIONS:
        doc = db[collection_name].find_one({"_id": "latest"})
        if not doc:
            continue
        if not latest_time and doc.get("time"):
            latest_time = doc.get("time")
        data = doc.get("data", [])
        for row in data:
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            code_counts[code] = code_counts.get(code, 0) + 1
            code_etfs.setdefault(code, []).append(etf_symbol)

    merged: dict[str, dict] = {}
    for code, count in code_counts.items():
        etfs = code_etfs.get(code, [])
        merged[code] = {"count": count, "etfs": etfs}

    return {"data": merged, "time": latest_time}


def fetch_etf_common_holdings() -> dict:
    db = mongo_client[ETF_DB_NAME]
    common_codes = None
    code_name_map: dict[str, str] = {}
    latest_time = None

    for collection_name, _ in ETF_COLLECTIONS:
        doc = db[collection_name].find_one({"_id": "latest"})
        if not doc:
            common_codes = set()
            continue
        if not latest_time and doc.get("time"):
            latest_time = doc.get("time")
        rows = doc.get("data", [])
        codes = set()
        for row in rows:
            code = str(row.get("code", "")).strip()
            name = str(row.get("name", "")).strip()
            if not code:
                continue
            codes.add(code)
            if name and code not in code_name_map:
                code_name_map[code] = name
        if common_codes is None:
            common_codes = codes
        else:
            common_codes &= codes

    if not common_codes:
        return {"data": [], "time": latest_time}

    data = [
        {"code": code, "name": code_name_map.get(code, "")}
        for code in sorted(common_codes)
    ]
    return {"data": data, "time": latest_time}


def fetch_etf_common_holdings_tech() -> dict:
    db = mongo_client[ETF_DB_NAME]
    doc = db[ETF_COMMON_TECH_COLLECTION].find_one({"_id": "latest"})
    if not doc:
        return {}
    return {
        "data": doc.get("data", []),
        "time": doc.get("time"),
    }


def fetch_future_index_tech() -> dict:
    db = mongo_client[FUTURE_INDEX_DB_NAME]
    doc = db[FUTURE_INDEX_COLLECTION].find_one({"_id": "latest"})
    if not doc:
        return {}
    return {
        "data": doc.get("data", []),
        "time": doc.get("time"),
    }


def _parse_holding_count(value: object) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _doc_date(value: object) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _find_latest_doc_for_date(collection, target_date: str) -> dict | None:
    cursor = collection.find(
        {"time": {"$lte": f"{target_date} 23:59:59"}},
        {"_id": 0},
    ).sort([("time", DESCENDING), ("_id", DESCENDING)]).limit(1)
    return next(iter(cursor), None)


def _find_previous_doc_before_date(collection, latest_date: str) -> dict | None:
    cursor = collection.find(
        {"time": {"$lt": f"{latest_date} 00:00:00"}},
        {"_id": 0},
    ).sort([("time", DESCENDING), ("_id", DESCENDING)]).limit(1)
    return next(iter(cursor), None)


def _format_count(value: int) -> str:
    return f"{value:,}"


def _normalize_price_up_codes(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(code).strip() for code in value if str(code).strip()}
    if isinstance(value, dict):
        if "codes" in value and isinstance(value["codes"], list):
            return {str(code).strip() for code in value["codes"] if str(code).strip()}
        return {str(code).strip() for code in value.keys() if str(code).strip()}
    return set()


def _load_price_up_index() -> dict[str, set[str]]:
    if not PRICE_UP_JSON_PATH.exists():
        return {}

    try:
        raw_data = json.loads(PRICE_UP_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    index: dict[str, set[str]] = {}
    if isinstance(raw_data, dict):
        for date_key, value in raw_data.items():
            date_text = str(date_key).strip()
            if not date_text:
                continue
            codes = _normalize_price_up_codes(value)
            if codes:
                index[date_text] = codes
    return index


def _build_price_up_lookup() -> dict[str, str]:
    index = _load_price_up_index()
    lookup: dict[str, str] = {}
    for date_key, codes in index.items():
        for code in codes:
            current = lookup.get(code)
            if not current or date_key > current:
                lookup[code] = date_key
    return lookup


def _find_price_up_date(code: str) -> str:
    target_code = str(code).strip()
    if not target_code:
        return ""

    return _build_price_up_lookup().get(target_code, "")


def _format_price_up_suffix(code: str) -> str:
    matched_date = _find_price_up_date(code)
    if not matched_date:
        return ""
    return f" （報價：{matched_date}）"


def _annotate_price_up_in_message(message: str) -> str:
    if not message:
        return message

    price_up_lookup = _build_price_up_lookup()
    if not price_up_lookup:
        return message

    annotated_lines: list[str] = []
    line_pattern = re.compile(r"^(\s*(?:\d+\.\s*)?)([A-Za-z0-9_]+)(.*)$")

    for line in message.splitlines():
        if "（報價：" in line:
            annotated_lines.append(line)
            continue

        match = line_pattern.match(line)
        if not match:
            annotated_lines.append(line)
            continue

        prefix, code, rest = match.groups()
        matched_date = price_up_lookup.get(code.strip(), "")
        if matched_date:
            annotated_lines.append(f"{prefix}{code}{rest} （報價：{matched_date}）")
        else:
            annotated_lines.append(line)

    return "\n".join(annotated_lines)


def _normalize_etf_names(raw_values: list[str]) -> list[str]:
    allowed = {name for name, _ in ETF_COLLECTIONS}
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        name = str(value).strip()
        if name not in allowed or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def fetch_etf_holding_changes(date_str: str | None, etf_names: list[str]) -> dict:
    db = mongo_client[ETF_DB_NAME]
    target_date = date_str or datetime.now(TZ).strftime("%Y-%m-%d")
    selected_etfs = _normalize_etf_names(etf_names)
    if not selected_etfs:
        selected_etfs = ["etf_00981A"]
    price_up_lookup = _build_price_up_lookup()

    per_etf_payload: list[dict] = []
    latest_date = ""
    previous_date = ""

    for etf_name in selected_etfs:
        collection = db[etf_name]
        latest_doc = _find_latest_doc_for_date(collection, target_date)
        if not latest_doc:
            continue

        current_latest_date = _doc_date(latest_doc.get("time"))
        previous_doc = _find_previous_doc_before_date(collection, current_latest_date) if current_latest_date else None
        if not previous_doc:
            continue

        latest_rows = latest_doc.get("data", []) if isinstance(latest_doc.get("data", []), list) else []
        previous_rows = previous_doc.get("data", []) if isinstance(previous_doc.get("data", []), list) else []

        latest_map: dict[str, dict] = {}
        previous_map: dict[str, dict] = {}

        for row in latest_rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            latest_map[code] = row

        for row in previous_rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            previous_map[code] = row

        per_etf_payload.append({
            "etf": etf_name,
            "latest_date": current_latest_date,
            "previous_date": _doc_date(previous_doc.get("time")),
            "latest_map": latest_map,
            "previous_map": previous_map,
        })

        if not latest_date:
            latest_date = current_latest_date
        if not previous_date:
            previous_date = _doc_date(previous_doc.get("time"))

    if not per_etf_payload:
        return {
            "data": [],
            "latest_date": "",
            "previous_date": "",
        }

    common_codes = None
    for payload in per_etf_payload:
        latest_codes = set(payload["latest_map"].keys())
        common_codes = latest_codes if common_codes is None else common_codes & latest_codes

    if not common_codes:
        return {
            "data": [],
            "latest_date": latest_date,
            "previous_date": previous_date,
            "selected_etfs": selected_etfs,
        }

    changes: list[dict] = []
    for code in sorted(common_codes):
        etf_details: list[dict] = []
        total_latest = 0
        total_previous = 0
        total_delta = 0
        display_name = ""

        for payload in per_etf_payload:
            etf_name = payload["etf"]
            latest_map = payload["latest_map"]
            previous_map = payload["previous_map"]

            latest_row = latest_map.get(code, {})
            previous_row = previous_map.get(code, {})
            latest_count = _parse_holding_count(latest_row.get("holding_count"))
            previous_count = _parse_holding_count(previous_row.get("holding_count"))
            delta = latest_count - previous_count
            status = (
                "新增" if previous_count == 0 and latest_count > 0
                else "減少" if latest_count == 0 and previous_count > 0
                else "增加" if delta > 0
                else "減少"
            )

            if not display_name:
                display_name = str(latest_row.get("name") or previous_row.get("name") or "").strip()

            total_latest += latest_count
            total_previous += previous_count
            total_delta += delta

            etf_details.append({
                "etf": etf_name,
                "latest_holding_count": latest_count,
                "previous_holding_count": previous_count,
                "delta": delta,
                "weight": latest_row.get("weight", previous_row.get("weight", "")),
                "status": "持平" if delta == 0 else status,
            })

        changes.append({
            "code": code,
            "name": display_name,
            "latest_holding_count": total_latest,
            "previous_holding_count": total_previous,
            "delta": total_delta,
            "status": "持平" if total_delta == 0 else ("增加" if total_delta > 0 else "減少"),
            "etfs": etf_details,
            "price_up_date": price_up_lookup.get(code, ""),
        })

    changes.sort(key=lambda item: (abs(item["delta"]), item["delta"]), reverse=True)

    return {
        "data": changes,
        "latest_date": latest_date,
        "previous_date": previous_date,
        "selected_etfs": selected_etfs,
    }


def _build_etf_discord_message(date_str: str | None, etf_names: list[str]) -> str:
    payload = fetch_etf_holding_changes(date_str, etf_names)
    selected_etfs = payload.get("selected_etfs", [])
    latest_date = payload.get("latest_date") or (date_str or "")
    items = payload.get("data", [])
    increase_count = sum(1 for item in items if int(item.get("delta", 0)) > 0)
    flat_count = sum(1 for item in items if int(item.get("delta", 0)) == 0)
    decrease_count = sum(1 for item in items if int(item.get("delta", 0)) < 0)

    lines = [
        f"ETF掃描 {latest_date}",
        f"ETF: {' + '.join([etf.replace('etf_', '') for etf in selected_etfs]) or '-'}",
    ]

    if not items:
        lines.append("結果: 無交集")
        return "\n".join(lines)

    lines.append(f"交集 {len(items)} 筆｜增 {increase_count} / 持平 {flat_count} / 減 {decrease_count}")
    lines.append("交集結果:")

    for index, item in enumerate(items, start=1):
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        delta = int(item.get("delta", 0))
        sign = "+" if delta > 0 else ""
        lines.append(f"{index}. {code} {name} / {sign}{_format_count(delta)}{_format_price_up_suffix(code)}")

    return "\n".join(lines)


def send_discord_message(message: str, webhook_url: str | None = None) -> None:
    target_url = (webhook_url or DEFAULT_DISCORD_WEBHOOK_URL).strip()
    if not target_url:
        raise ValueError("Missing Discord webhook URL")

    response = requests.post(target_url, json={"content": message}, timeout=20)
    response.raise_for_status()


def fetch_etf_common_holdings() -> dict:
    db = mongo_client[ETF_DB_NAME]
    common_codes = None
    code_name_map: dict[str, str] = {}
    latest_time = None

    for collection_name, _ in ETF_COLLECTIONS:
        doc = db[collection_name].find_one({"_id": "latest"})
        if not doc:
            common_codes = set()
            continue
        if not latest_time and doc.get("time"):
            latest_time = doc.get("time")
        rows = doc.get("data", [])
        codes = set()
        for row in rows:
            code = str(row.get("code", "")).strip()
            name = str(row.get("name", "")).strip()
            if not code:
                continue
            codes.add(code)
            if name and code not in code_name_map:
                code_name_map[code] = name
        if common_codes is None:
            common_codes = codes
        else:
            common_codes &= codes

    if not common_codes:
        return {"data": [], "time": latest_time}

    data = [
        {"code": code, "name": code_name_map.get(code, "")}
        for code in sorted(common_codes)
    ]
    return {"data": data, "time": latest_time}


class MarketApiHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/odd_lot_trade":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length)
                payload = json.loads(post_data.decode("utf-8"))

                code = str(payload.get("code", "")).strip()
                action = str(payload.get("action", "")).strip()
                price = payload.get("price")
                quantity = payload.get("quantity")

                if not code or action.lower() not in ("buy", "sell"):
                    self._send_json(400, {"error": "Invalid code or action"})
                    return
                if price is None or quantity is None:
                    self._send_json(400, {"error": "Missing price or quantity"})
                    return

                trade = place_intraday_odd_lot(action, code, float(price), int(quantity))
                self._send_json(200, {"status": "ok", "trade": trade})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if self.path == "/api/chat_llm":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                payload = json.loads(post_data.decode('utf-8'))
                
                if not openai_client:
                    self._send_json(500, {"error": "OpenAI API key not configured"})
                    return

                stock_name = payload.get("stock_name", "Unknown Stock")
                question = payload.get("question", "Please analyze this stock.")
                stock_data_context = payload.get("context", "")

                prompt = f"""
                    You are a professional stock analyst.
                    User is asking about stock: {stock_name}
                    Question: {question}

                    Here is some technical data context:
                    {stock_data_context}

                    Please provide a concise and professional analysis in Traditional Chinese (Taiwan).
                    """
                response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are a helpful financial assistant."},
                        {"role": "user", "content": prompt}
                    ]
                )
                
                answer = response.choices[0].message.content
                self._send_json(200, {"answer": answer})
            except Exception as e:
                print(f"Error in chat_llm: {e}")
                self._send_json(500, {"error": str(e)})
            return

        if self.path == "/api/etf_holding_changes/share":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
                payload = json.loads(post_data.decode("utf-8"))

                date_str = str(payload.get("date", "")).strip() or None
                raw_etfs = payload.get("etfs", [])
                webhook_url = str(payload.get("webhook_url", "")).strip() or None
                custom_message = str(payload.get("custom_message", "")).strip()
                if isinstance(raw_etfs, str):
                    etfs = [item.strip() for item in raw_etfs.split(",") if item.strip()]
                elif isinstance(raw_etfs, list):
                    etfs = [str(item).strip() for item in raw_etfs if str(item).strip()]
                else:
                    etfs = []

                message = _annotate_price_up_in_message(custom_message) if custom_message else _build_etf_discord_message(date_str, etfs)
                send_discord_message(message, webhook_url)
                self._send_json(200, {"status": "ok", "message": message})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return
        
        self._send_json(404, {"error": "Not found"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        date_str = query.get("date", [None])[0]
        try:
            if parsed.path == "/api/stkfut_tradeinfo":
                payload = fetch_latest_payload(date_str)
                self._send_json(200, payload)
                return
            if parsed.path == "/api/mxf":
                if query.get("all", ["0"])[0] == "1":
                    payload = fetch_mxf_series(date_str)
                else:
                    payload = fetch_latest_mxf(date_str)
                self._send_json(200, payload)
                return
            if parsed.path == "/api/etf_holdings_counts":
                payload = fetch_etf_holdings_counts()
                self._send_json(200, payload)
                return
            if parsed.path == "/api/etf_common_holdings":
                payload = fetch_etf_common_holdings()
                self._send_json(200, payload)
                return
            if parsed.path == "/api/etf_common_holdings_tech":
                payload = fetch_etf_common_holdings_tech()
                self._send_json(200, payload)
                return
            if parsed.path == "/api/future_index_tech":
                payload = fetch_future_index_tech()
                self._send_json(200, payload)
                return
            if parsed.path == "/api/etf_holding_changes":
                etfs = query.get("etfs", [""])[0].split(",")
                payload = fetch_etf_holding_changes(date_str, etfs)
                self._send_json(200, payload)
                return
            if parsed.path == "/api/etf_common_holdings":
                payload = fetch_etf_common_holdings()
                self._send_json(200, payload)
                return
            self._send_json(404, {"error": "Not found"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def main() -> None:
    host = os.getenv("MARKET_API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("MARKET_API_PORT", "5050")))
    server = HTTPServer((host, port), MarketApiHandler)
    print(f"Market API listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
