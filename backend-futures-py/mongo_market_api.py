import os
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

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
TURNOVER_DB_NAME = "yahoo_turnover"
TURNOVER_TECH_DB_NAME = "yahoo_turnover_tech"
MXF_DB_NAME = "mxf_futures"
ETF_DB_NAME = "Investment"
ETF_COLLECTIONS = [
    ("etf_00981A", "00981A"),
    ("etf_00982A", "00982A"),
    ("etf_00991A", "00991A"),
    ("etf_00992A", "00992A"),
]
ETF_COMMON_TECH_COLLECTION = "etf_Initiative_tech"
TZ = ZoneInfo("Asia/Taipei")

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


def fetch_latest_turnover(date_str: str | None) -> dict:
    collection_name = get_collection_name(date_str)
    collection = mongo_client[TURNOVER_DB_NAME][collection_name]
    doc = collection.find_one({"_id": "latest"})
    if not doc:
        return {}

    return {
        "data": doc.get("data", []),
        "time": doc.get("time"),
    }


def _get_latest_collection_name(db) -> str | None:
    candidates = [
        name
        for name in db.list_collection_names()
        if len(name) == 10 and name[4] == "-" and name[7] == "-"
    ]
    return max(candidates) if candidates else None


def fetch_latest_turnover_tech(date_str: str | None) -> dict:
    db = mongo_client[TURNOVER_TECH_DB_NAME]
    collection_name = get_collection_name(date_str) if date_str else None
    collection = db[collection_name] if collection_name else None
    docs = list(collection.find({}, {"_id": 0})) if collection_name else []

    if not docs:
        fallback_name = _get_latest_collection_name(db)
        if not fallback_name:
            return {}
        docs = list(db[fallback_name].find({}, {"_id": 0}))
        return {"data": docs, "collection_name": fallback_name}

    return {"data": docs, "collection_name": collection_name}


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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
            if parsed.path == "/api/turnover":
                payload = fetch_latest_turnover(date_str)
                self._send_json(200, payload)
                return
            if parsed.path == "/api/turnover_tech":
                payload = fetch_latest_turnover_tech(date_str)
                self._send_json(200, payload)
                return
            if parsed.path == "/api/mxf":
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
            if parsed.path == "/api/etf_common_holdings":
                payload = fetch_etf_common_holdings()
                self._send_json(200, payload)
                return
            self._send_json(404, {"error": "Not found"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def main() -> None:
    host = os.getenv("MARKET_API_HOST", "0.0.0.0")
    port = int(os.getenv("MARKET_API_PORT", "5050"))
    server = HTTPServer((host, port), MarketApiHandler)
    print(f"Market API listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
