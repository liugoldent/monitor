"""Mirror chart inputs to MongoDB without changing the existing CSV format."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

from pymongo import MongoClient, UpdateOne

DB_NAME = "tx_trading_system"
COLLECTIONS = {
    "webhook_data_1min.csv": "candles_1m",
    "mxf_value.csv": "chips_1m",
    "six_strategy_signal_events.csv": "strategy_signals",
    "ef_position_events.csv": "ef_position_events",
    "h_position_events.csv": "h_position_events",
    "h_trade.csv": "h_trades",
    "h2_trade.csv": "h2_trades",
}

_client = None
_last_prune_at = 0.0
_last_prune_source_date = None


def _mongo_uri() -> str:
    uri = os.getenv("MONGO_URI", "").strip()
    if uri:
        return uri
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MONGO_URI="):
                return line.partition("=")[2].strip().strip('"\'')
    raise RuntimeError("MONGO_URI is not configured")


def _collection(source: str):
    global _client
    if _client is None:
        _client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=3000)
    return _client[DB_NAME][COLLECTIONS[source]]


def document_id(source: str, row: dict) -> str:
    """Use source timestamps or event keys so webhook retries stay idempotent."""
    if source == "webhook_data_1min.csv":
        identity = [row.get("Symbol"), row.get("Timeframe"), row.get("TradingView Time")]
    elif source == "mxf_value.csv":
        identity = [row.get("time")]
    elif source == "six_strategy_signal_events.csv" and row.get("source_order") is not None:
        identity = [row.get("source_order")]
    elif source in {"h_trade.csv", "h2_trade.csv"}:
        identity = [row.get("timestamp"), row.get("action"), row.get("side")]
    elif row.get("event_key"):
        identity = [row.get("event_key")]
    else:
        identity = [row.get(key) for key in (
            "received_at", "message_time", "strategy_code", "previous_position",
            "new_position", "signal",
        )]
    value = json.dumps([source, identity], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prepare_document(source: str, row: dict) -> dict:
    """Keep the CSV field names while adding queryable date and stable identity."""
    document = {key: value for key, value in row.items() if key != "_id"}
    time_fields = {
        "webhook_data_1min.csv": ("TradingView Time",),
        "mxf_value.csv": ("time",),
        "six_strategy_signal_events.csv": ("received_at", "message_time"),
        "ef_position_events.csv": ("received_at",),
        "h_position_events.csv": ("received_at",),
        "h_trade.csv": ("timestamp",),
        "h2_trade.csv": ("timestamp",),
    }
    source_time = next((document.get(key) for key in time_fields[source] if document.get(key)), None)
    if source_time:
        document["date"] = str(source_time)[:10]
    document["_id"] = document_id(source, document)
    return document


def mirror_row(source: str, row: dict) -> None:
    """Upsert a source row; let callers choose how a MongoDB outage is handled."""
    if source not in COLLECTIONS:
        return
    document = prepare_document(source, row)
    _collection(source).update_one(
        {"_id": document["_id"]},
        {"$set": {key: value for key, value in document.items() if key != "_id"}},
        upsert=True,
    )
    _maybe_prune(document.get("date"))


def _maybe_prune(source_date: str | None) -> None:
    global _last_prune_at, _last_prune_source_date
    now = time.monotonic()
    if source_date == _last_prune_source_date and now - _last_prune_at < 3600:
        return
    prune_old_chart_data()
    _last_prune_at = now
    _last_prune_source_date = source_date


def prune_old_chart_data() -> str | None:
    """Retain 10 weekday trading dates, including their Saturday night-session tail."""
    values = _collection("webhook_data_1min.csv").distinct("date")
    trading = []
    for value in values:
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        if parsed.weekday() < 5:
            trading.append(parsed)
    trading = sorted(set(trading))
    if len(trading) < 10:
        return None
    cutoff = trading[-10].isoformat()
    for source in ("webhook_data_1min.csv", "mxf_value.csv"):
        _collection(source).delete_many({"date": {"$lt": cutoff}})
    return cutoff


def mirror_batch(source: str, rows: list[dict]) -> None:
    if not rows:
        return
    documents = {}
    for row in rows:
        document = prepare_document(source, row)
        documents[document["_id"]] = document
    operations = []
    for document in documents.values():
        operations.append(UpdateOne(
            {"_id": document["_id"]},
            {"$set": {key: value for key, value in document.items() if key != "_id"}},
            upsert=True,
        ))
    _collection(source).bulk_write(operations, ordered=False)


def mirror_csv_file(source: str, path: Path, expected_fields: tuple[str, ...], batch_size: int = 500) -> int:
    """Reconcile an atomically rewritten CSV, including after earlier MongoDB failures."""
    if not path.exists():
        return 0
    count = 0
    batch = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, strict=True)
        if tuple(reader.fieldnames or ()) != expected_fields:
            raise ValueError(f"Invalid {source} columns")
        for row in reader:
            batch.append(row)
            if len(batch) >= batch_size:
                mirror_batch(source, batch)
                count += len(batch)
                batch = []
    if batch:
        mirror_batch(source, batch)
        count += len(batch)
    return count


def ensure_query_indexes() -> None:
    _collection("webhook_data_1min.csv").create_index([("date", 1), ("TradingView Time", -1)])
    _collection("mxf_value.csv").create_index([("date", 1), ("time", -1)])
    _collection("six_strategy_signal_events.csv").create_index("source_order")
