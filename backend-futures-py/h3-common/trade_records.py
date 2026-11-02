"""Record the first contract's actual fill price for per-contract research."""
import csv
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

FIELDS = ["recorded_at", "trigger", "trade_id", "side", "quantity",
          "entry_time", "last_fill_time", "entry_price", "fill_quantity",
          "fill_status", "fills"]


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def entry_record(result, trigger, recorded_at):
    trade = getattr(result, "trade", None)
    status = value(trade, "status")
    fills = []
    for deal in value(status, "deals", []) or []:
        try:
            price = float(value(deal, "price"))
            quantity = int(value(deal, "quantity"))
            timestamp = float(value(deal, "ts"))
            if not math.isfinite(price) or price <= 0 or quantity <= 0 or timestamp <= 0:
                continue
            time = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Taipei")).isoformat(timespec="microseconds")
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        fills.append(dict(time=time, price=price, quantity=quantity))
    fills.sort(key=lambda fill: fill["time"])
    total = sum(fill["quantity"] for fill in fills)
    complete = total == result.quantity and total > 0
    return dict(recorded_at=recorded_at, trigger=trigger,
                trade_id=value(value(trade, "order"), "id", ""),
                side=result.side or "", quantity=result.quantity,
                entry_time=fills[0]["time"] if complete else "",
                last_fill_time=fills[-1]["time"] if complete else "",
                entry_price=fills[0]["price"] if complete else "",
                fill_quantity=total, fill_status="complete" if complete else "missing_fill_details",
                fills=fills)


def append_entry(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if header:
            writer.writeheader()
        writer.writerow(dict(row, fills=json.dumps(row["fills"], ensure_ascii=False)))
