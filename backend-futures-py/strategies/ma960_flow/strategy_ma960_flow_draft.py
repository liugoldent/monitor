"""MA960 + MXF flow continuation signal draft.

This draft only emits CSV/state/Discord alerts. It is intentionally not wired
into `webhook_server.py` yet.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
import sys
from threading import RLock

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


TV_DOC_DIR = BASE_DIR / "tv_doc"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
OUTPUT_DIR = Path(__file__).resolve().parent
STATE_PATH = OUTPUT_DIR / "ma960_flow_state.json"
ALERT_PATH = OUTPUT_DIR / "ma960_flow_alert.csv"
send_ma960_flow_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))

MAX_DIST_ABOVE_MA960 = 60.0
MIN_MA960_SLOPE_15 = 0.0
REQUIRE_TREND_GOLD = False
ALERT_COOLDOWN_MINUTES = 30

ALERT_HEADER = [
    "timestamp",
    "action",
    "setup",
    "side",
    "close",
    "ma960",
    "dist_to_ma960",
    "ma960_slope_15",
    "tx_bvav",
    "mtx_bvav",
    "mtx_tbta",
    "mtx_bvav_avg",
    "trend",
    "reason",
]

LOCK = RLock()


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_alert(row: list[object]) -> None:
    exists = ALERT_PATH.exists()
    with ALERT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(ALERT_HEADER)
        writer.writerow(row)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _latest_market_time(row: dict[str, str]) -> str:
    return str(row.get("TradingView Time") or row.get("Record Time") or "").strip()


def _latest_mxf_by_time(time_value: str) -> dict[str, str] | None:
    target_minute = time_value[:16]
    latest: dict[str, str] | None = None
    for row in _read_csv_rows(MXF_VALUE_CSV_PATH):
        if str(row.get("time") or "")[:16] <= target_minute:
            latest = row
    return latest


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _ma960_slope_15(rows: list[dict[str, str]]) -> float | None:
    if len(rows) < 16:
        return None
    latest = to_float(rows[-1].get("MA_960"))
    previous = to_float(rows[-16].get("MA_960"))
    if latest is None or previous is None:
        return None
    return latest - previous


def _signal_setup(latest_1m: dict[str, str], mxf: dict[str, str], slope_15: float | None) -> tuple[str | None, str]:
    close = to_float(latest_1m.get("Close"))
    ma960 = to_float(latest_1m.get("MA_960"))
    tx_bvav = to_float(mxf.get("tx_bvav"))
    mtx_bvav = to_float(mxf.get("mtx_bvav"))
    mtx_tbta = to_float(mxf.get("mtx_tbta"))
    trend = str(mxf.get("trend") or "").strip().lower()
    if close is None or ma960 is None:
        return None, "missing close/ma960"
    if tx_bvav is None or mtx_bvav is None or mtx_tbta is None:
        return None, "missing mxf flow"
    if slope_15 is None or slope_15 <= MIN_MA960_SLOPE_15:
        return None, "ma960 is not rising"
    dist = close - ma960
    if dist < 0 or dist > MAX_DIST_ABOVE_MA960:
        return None, "price is not near above ma960"
    if tx_bvav <= 0 or mtx_bvav <= 0:
        return None, "big money is not long"
    if REQUIRE_TREND_GOLD and trend != "gold":
        return None, "dealer is not above 23-period average"
    if mtx_tbta < 0:
        return "super_long", "big money long, retail short, price rides rising ma960"
    if mtx_tbta > 0:
        return "shakeout_long", "big money and retail both long, price rides rising ma960"
    return None, "retail is flat"


def _cooldown_ok(state: dict, setup: str, market_time: str) -> bool:
    latest_key = f"last_{setup}_market_time"
    last_time = _parse_dt(str(state.get(latest_key) or ""))
    current_time = _parse_dt(market_time)
    if last_time is None or current_time is None:
        return True
    return (current_time - last_time).total_seconds() >= ALERT_COOLDOWN_MINUTES * 60


def _send_signal(signal: dict) -> None:
    message = (
        "策略=MA960籌碼順勢(strategy_ma960_flow_draft)；"
        f"{signal['setup']} 訊號：{signal['side']}，"
        f"價格={signal['close']}，MA960={signal['ma960']}，"
        f"距離960={signal['dist_to_ma960']:.1f}，"
        f"MA960斜率15={signal['ma960_slope_15']:.1f}，"
        f"坦克={signal['tx_bvav']}，游擊={signal['mtx_bvav']}，"
        f"炮灰={signal['mtx_tbta']}，游擊均={signal['mtx_bvav_avg']}，"
        f"原因={signal['reason']}"
    )
    send_ma960_flow_message(message)


def evaluate_ma960_flow_strategy() -> dict | None:
    """Return and log a long continuation alert when MA960 + MXF flow aligns."""
    with LOCK:
        one_min_rows = _read_csv_rows(WEBHOOK_1M_CSV_PATH)
        if not one_min_rows:
            return None
        latest_1m = one_min_rows[-1]
        market_time = _latest_market_time(latest_1m)
        if not market_time:
            return None
        mxf = _latest_mxf_by_time(market_time)
        if not mxf:
            return None

        slope_15 = _ma960_slope_15(one_min_rows)
        setup, reason = _signal_setup(latest_1m, mxf, slope_15)
        if setup is None:
            return None
        state = _read_state()
        if not _cooldown_ok(state, setup, market_time):
            return None

        close = float(to_float(latest_1m.get("Close")) or 0)
        ma960 = float(to_float(latest_1m.get("MA_960")) or 0)
        signal = {
            "action": "alert",
            "setup": setup,
            "side": "bull",
            "close": close,
            "ma960": ma960,
            "dist_to_ma960": close - ma960,
            "ma960_slope_15": float(slope_15 or 0),
            "tx_bvav": to_float(mxf.get("tx_bvav")),
            "mtx_bvav": to_float(mxf.get("mtx_bvav")),
            "mtx_tbta": to_float(mxf.get("mtx_tbta")),
            "mtx_bvav_avg": to_float(mxf.get("mtx_bvav_avg")),
            "trend": str(mxf.get("trend") or ""),
            "reason": reason,
        }
        _append_alert([
            now_str(),
            signal["action"],
            setup,
            signal["side"],
            signal["close"],
            signal["ma960"],
            round(signal["dist_to_ma960"], 1),
            round(signal["ma960_slope_15"], 1),
            signal["tx_bvav"],
            signal["mtx_bvav"],
            signal["mtx_tbta"],
            signal["mtx_bvav_avg"],
            signal["trend"],
            reason,
        ])
        state[f"last_{setup}_market_time"] = market_time
        state["last_signal"] = signal
        _write_state(state)
        _send_signal(signal)
        return signal


if __name__ == "__main__":
    signal = evaluate_ma960_flow_strategy()
    if signal:
        print(json.dumps(signal, ensure_ascii=False, indent=2))
