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
MXF_DB_NAME = "mxf_futures"
TZ = ZoneInfo("Asia/Taipei")

mongo_client = MongoClient(MONGO_URI)


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
            if parsed.path == "/api/mxf":
                payload = fetch_latest_mxf(date_str)
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
