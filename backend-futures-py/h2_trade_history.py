"""Maintain the single-contract H2 signal history used for drawdown analysis."""

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from h_signal import parse_h2_receipt


FIELDS = ("timestamp", "action", "side", "price", "pnl", "quantity")
TAIPEI = timezone(timedelta(hours=8))
SIDES = {-1: "bear", 1: "bull"}


def _stamp(value):
    parsed = datetime.fromisoformat(value.replace("/", "-"))
    return parsed.astimezone(TAIPEI) if parsed.tzinfo else parsed.replace(tzinfo=TAIPEI)


def _price(value):
    try:
        price = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"無效價格 {value!r}") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError(f"無效價格 {value!r}")
    return price


def _trade(at, action, side, price, pnl=""):
    return dict(zip(FIELDS, (at.replace(tzinfo=None).isoformat(sep=" "), action, side,
                             format(price, "f"), pnl, 1)))


def closed_tradingview_trades(path):
    """Import completed pairs. An unmatched export entry needs separate verification."""
    trades = []
    entry = None
    last_at = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, strict=True):
            if row["交易No"] == "0" and not row["類型"]:
                continue
            action, side = row["類型"].split()
            side = {"Long": "bull", "Short": "bear"}[side]
            at = _stamp(f"{row['日期']} {row['時間']}")
            price = _price(row["價格"])
            if Decimal(row["數量"]) != 1 or (last_at is not None and at < last_at):
                raise ValueError("H2 匯出的時間或口數無效")
            last_at = at
            if action == "Entry" and entry is None:
                entry = (row["No"], at, side, price)
            elif action == "Exit" and entry and (row["No"], side) == (entry[0], entry[2]):
                pnl = (price - entry[3]) * (10 if side == "bull" else -10)
                trades.extend((_trade(entry[1], "enter", side, entry[3]),
                               _trade(at, "exiting", side, price, format(pnl, "f"))))
                entry = None
            else:
                raise ValueError(f"H2 匯出交易 {row['No']} 無法配對")
    return trades


def write_trades(path, trades):
    """Publish the full CSV atomically, including both rows of a reversal."""
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(trades)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _signals(path, after):
    latest_message = {}
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            event = json.loads(raw)
            receipt = parse_h2_receipt(event, allow_flat=True)
            if receipt is None:
                continue
            _, target, received, sent = receipt
            chat_id, message_id = event["chat_id"], event["message_id"]
            if received < sent or message_id <= latest_message.get(chat_id, -1):
                continue
            latest_message[chat_id] = message_id
            if after is None or received > after:
                yield received.astimezone(TAIPEI), SIDES.get(target)


def _quotes(path, signal_times):
    earliest = min(signal_times) - timedelta(seconds=120)
    latest = max(signal_times)
    quotes = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!" or row.get("Timeframe") != "1":
                continue
            try:
                recorded = _stamp(row["Record Time"])
                if earliest <= recorded <= latest:
                    bar = _stamp(row["TradingView Time"])
                    if 0 <= (recorded - bar).total_seconds() <= 120:
                        quotes.append((recorded, bar, _price(row["Close"])))
            except (TypeError, ValueError):
                continue
    return sorted(quotes, key=lambda quote: quote[0], reverse=True)


def replay_h2_signals(trade_path, event_path, market_path):
    """Replay trusted receipts since the latest persisted trade without looking ahead."""
    if not event_path.exists():
        return 0
    try:
        handle = trade_path.open(encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        trades = []
    else:
        with handle:
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != FIELDS:
                raise ValueError("H2 交易欄位無效")
            trades = list(reader)
    last = trades[-1] if trades else None
    open_side = last["side"] if last and last["action"] == "enter" else None
    entry_price = _price(last["price"]) if open_side else None
    after = _stamp(last["timestamp"]) if last else None
    signals = sorted(_signals(event_path, after), key=lambda signal: signal[0])
    if not signals:
        return 0
    quotes = None
    added = []
    for at, target in signals:
        if target == open_side:
            continue
        if quotes is None:
            quotes = _quotes(market_path, [stamp for stamp, _ in signals])
        price = next((close for recorded, bar, close in quotes
                      if recorded <= at and (at - bar).total_seconds() <= 120), None)
        if price is None:
            if added:
                write_trades(trade_path, trades + added)
            raise ValueError(f"H2 {at.isoformat()} 缺少訊號前 120 秒內已收到的 MXF1! 一分鐘行情")
        if open_side:
            pnl = (price - entry_price) * (10 if open_side == "bull" else -10)
            added.append(_trade(at, "exiting", open_side, price, format(pnl, "f")))
        if target:
            added.append(_trade(at, "enter", target, price))
        open_side, entry_price = target, price
    if added:
        write_trades(trade_path, trades + added)
    return len(added)
