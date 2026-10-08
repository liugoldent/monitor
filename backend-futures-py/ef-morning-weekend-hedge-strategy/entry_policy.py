"""Clamp entry gates and full-close evidence; all decision times are Taipei."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ENTRY_CUTOFF = time(22)
COOLDOWN = timedelta(minutes=60)
VERSION = "json-positions-v9-cutoff22-cooldown60"
TERMINAL = {"filled", "cancelled", "failed"}


def entry_target(requested: int, actual: int, received_at: datetime,
                 last_exit_at: datetime | None, pending_exit: bool = False) -> tuple[int, str]:
    """Gate opening/increasing exposure; holding, reducing and exiting stay possible.

    An atomic reversal may open its new side if the *previous* cooldown permits
    it, matching the researched policy. A blocked reversal becomes an exit.
    """
    if requested == 0 or (actual * requested > 0 and abs(requested) <= abs(actual)):
        return requested, ""
    reason = ""
    if not time(8, 45) <= received_at.time() < ENTRY_CUTOFF:
        reason = "22:00截止後不開新倉（午夜不重開）"
    elif pending_exit:
        reason = "前次平倉成交尚未確認，暫停新進場"
    elif last_exit_at is not None and received_at < last_exit_at + COOLDOWN:
        reason = f"平倉後冷卻至 {last_exit_at + COOLDOWN:%Y-%m-%d %H:%M:%S}"
    if not reason:
        return requested, ""
    # Never flatten a valid same-side holding merely because entry is blocked.
    return (actual if actual * requested > 0 else 0), reason


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def full_close_time(trade, watch: dict, now: datetime) -> datetime | None:
    """Timestamp of the fill that closes the old side, including partial reversals.

    Acknowledgements and partial exits are never full-close evidence. Broker deal
    `ts` is Unix seconds (Shioaji); reject invalid, future and pre-order records.
    """
    fills, seen = [], set()
    for deal in value(value(trade, "status"), "deals", []) or []:
        try:
            quantity = value(deal, "quantity")
            if isinstance(quantity, bool) or int(quantity) != quantity or quantity <= 0:
                continue
            stamp = datetime.fromtimestamp(float(value(deal, "ts")), ZoneInfo("Asia/Taipei"))
            stamp = stamp.replace(tzinfo=None)
            if not datetime.fromisoformat(watch["at"]) <= stamp <= now:
                continue
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        seq = value(deal, "seq")
        if seq and seq in seen:
            continue
        if seq:
            seen.add(seq)
        fills.append((stamp, int(quantity)))
    total = 0
    for stamp, quantity in sorted(fills):
        total += quantity
        if total >= abs(watch["before"]):
            return stamp
    return None
