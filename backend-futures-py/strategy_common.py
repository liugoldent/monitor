"""Shared helpers for strategy modules.

Keep strategy-specific business logic out of `webhook_server.py`.
This module contains only reusable parsing, CSV, and Discord helpers.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

try:
    from auto_trade_shortCycle import send_discord_message as _base_shortcycle_send_discord_message
except Exception:  # pragma: no cover - fallback for environments without optional trade deps
    _base_shortcycle_send_discord_message = None

TZ = ZoneInfo("Asia/Taipei")


def to_float(value: Any) -> float | None:
    """Convert a CSV or webhook value to float, returning None on failure."""
    if value is None:
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned:
            return None
        return float(cleaned)
    except ValueError:
        return None


def read_last_n_rows(path: str, count: int) -> list[dict]:
    """Return the last `count` rows from a CSV as dictionaries."""
    if count <= 0 or not os.path.isfile(path):
        return []

    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []

    if count >= len(rows):
        return rows
    return rows[-count:]


def format_mxf_number(value: Any) -> str:
    """Format MXF numeric fields without trailing decimals when possible."""
    number = to_float(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def get_latest_mxf_snapshot(mxf_value_csv_path: str) -> dict[str, str]:
    """Read the newest non-empty MXF snapshot from `mxf_value.csv`."""
    if not os.path.isfile(mxf_value_csv_path):
        return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}

    try:
        with open(mxf_value_csv_path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}

    for row in reversed(rows):
        snapshot = {
            "tx_bvav": str(row.get("tx_bvav", "")).strip(),
            "mtx_bvav": str(row.get("mtx_bvav", "")).strip(),
            "mtx_bvav_avg": str(row.get("mtx_bvav_avg", "")).strip(),
        }
        if any(snapshot.values()):
            return snapshot

    return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}


def append_mxf_context(message: str, mxf_value_csv_path: str) -> str:
    """Append the latest MXF snapshot to a Discord message body."""
    snapshot = get_latest_mxf_snapshot(mxf_value_csv_path)
    if not any(snapshot.values()):
        return message

    context_lines = [
        "MXF最新:",
        f"坦克(tx_bvav): {format_mxf_number(snapshot['tx_bvav']) or '-'}",
        f"游擊隊(mtx_bvav): {format_mxf_number(snapshot['mtx_bvav']) or '-'}",
        f"游擊平均(mtx_bvav_avg): {format_mxf_number(snapshot['mtx_bvav_avg']) or '-'}",
    ]
    if not message:
        return "\n".join(context_lines)
    return f"{message}\n" + "\n".join(context_lines)


def build_shortcycle_send_discord_message(mxf_value_csv_path: str):
    """Return a Discord sender that automatically appends MXF context."""
    def _send(content: str) -> None:
        message = append_mxf_context(content, mxf_value_csv_path)
        if _base_shortcycle_send_discord_message is None:
            print(message)
            return
        _base_shortcycle_send_discord_message(message)

    return _send


def ensure_csv_header(path: str, header: list[str]) -> None:
    """Ensure a CSV file exists with the expected header order."""
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        return

    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except Exception:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        return

    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        return

    if rows[0] != header:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows[1:])


def append_csv_row(path: str, row: list[object], header: list[str] | None = None) -> None:
    """Append a single row to a CSV file, creating the file if needed."""
    if header is not None:
        ensure_csv_header(path, header)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(row)


def now_str() -> str:
    """Current time in Asia/Taipei as a string."""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
