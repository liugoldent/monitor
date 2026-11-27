"""Broker-independent H signal rules shared by recording and account executors.

Directions are +1 (long) and -1 (short). Notification quantities never determine
an account's order size; each account owns its broker connection and order state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


ALLOWED_SENDER_USERNAME = "taiwan_mxf_bot"
H2_REDUCTION_MAX_MESSAGE_AGE = 45
H_POSITION_PATTERN = re.compile(r"(?P<side>多|空)\s*(?P<quantity>\d+)\s*口")
H_BS_PATTERN = re.compile(
    r"[（(]\s*B\s*=\s*(?P<buy>\d+)\s+S\s*=\s*(?P<sell>\d+)\s*[)）]"
)


H2_NOTICE_PATTERN = re.compile(
    r"^\s*浩克2V3[ \t]+交易訊號通知[ \t]*[（(][ \t]*B[ \t]*=[ \t]*(?P<buy>[01])"
    r"[ \t]+S[ \t]*=[ \t]*(?P<sell>[01])[ \t]*[)）][ \t]*\r?$",
    re.MULTILINE,
)


def h2_direction(text: str, *, allow_flat=False) -> int | None:
    if not isinstance(text, str):
        return None
    matches = list(H2_NOTICE_PATTERN.finditer(text))
    if len(matches) != 1:
        return None
    direction = int(matches[0]['buy']) - int(matches[0]['sell'])
    if direction or (allow_flat and matches[0]['buy'] == matches[0]['sell'] == '0'):
        return direction
    return None


def parse_h2_receipt(event, *, allow_flat=False):
    """Validate the sender, signal and timestamps for the H2 journal."""
    if not isinstance(event, dict) or event.get('event') != 'received' or event.get('route') != 'h2':
        return None
    sender = event.get('sender_username')
    if not isinstance(sender, str) or sender.casefold() != ALLOWED_SENDER_USERNAME:
        return None
    if not all(type(event.get(k)) is int for k in ('chat_id', 'message_id')):
        return None
    direction = h2_direction(event.get('text'), allow_flat=allow_flat)
    if direction is None:
        return None
    try:
        received = datetime.fromisoformat(event['received_at'])
        sent = datetime.fromisoformat(event['telegram_message_at'])
    except (KeyError, TypeError, ValueError):
        return None
    if received.tzinfo is None or sent.tzinfo is None:
        return None
    return f"h2-tg:{event['chat_id']}:{event['message_id']}", direction, received, sent


def parse_h2_signal(event, now=None, *, allow_flat=False, max_message_age=30):
    """Accept only a fresh, trusted Telegram H2 receipt."""
    receipt = parse_h2_receipt(event, allow_flat=allow_flat)
    if receipt is None:
        return None
    key, direction, received, sent = receipt
    current = now or datetime.now(timezone.utc)
    # Message dates are second-resolution and the relay may process a delayed update.
    if not (0 <= (current - received).total_seconds() <= 30
            and 0 <= (current - sent).total_seconds() <= max_message_age):
        return None
    return key, direction, received.timestamp()


def parse_h2_dual_signal(event, now=None, *, allow_flat=False, max_message_age=30):
    """Read a normalized H2 signal without refreshing its upstream timestamps."""
    if (not isinstance(event, dict) or event.get('event') != 'received'
            or event.get('route') != 'h2' or event.get('source') != 'h2_dual_source'):
        return None
    key, direction = event.get('signal_key'), event.get('direction')
    directions = (-1, 0, 1) if allow_flat else (-1, 1)
    if (not isinstance(key, str) or not re.fullmatch(r'h2-dual:[1-9]\d*', key)
            or type(direction) is not int or direction not in directions):
        return None
    try:
        sent = datetime.fromisoformat(event['received_at'])
        if event.get('source_kind') == 'concord_sqlite':
            received, max_message_age = sent, 30
        elif event.get('source_kind') == 'telegram':
            received = datetime.fromisoformat(event['relay_received_at'])
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    current = now or datetime.now(timezone.utc)
    if (sent.tzinfo is None or received.tzinfo is None or not sent <= received <= current
            or (current - sent).total_seconds() > max_message_age
            or (current - received).total_seconds() > 30):
        return None
    return key, direction, sent.timestamp()


def is_h_signal(text: str) -> bool:
    return isinstance(text, str) and "浩克3" in text and "訊號通知" in text


def parse_h_direction(text: str) -> int | None:
    """Accept one unambiguous legacy or B/S direction, independent of size."""
    if not is_h_signal(text):
        return None
    positions = list(H_POSITION_PATTERN.finditer(text))
    counters = list(H_BS_PATTERN.finditer(text))
    if len(positions) > 1 or len(counters) > 1:
        return None
    directions = []
    if positions:
        match = positions[0]
        if int(match.group("quantity")) < 1:
            return None
        directions.append(1 if match.group("side") == "多" else -1)
    if counters:
        match = counters[0]
        buy = int(match.group("buy")) > 0
        sell = int(match.group("sell")) > 0
        if buy == sell:
            return None
        directions.append(1 if buy else -1)
    if not directions or len(set(directions)) != 1:
        return None
    return directions[0]


def parse_h_flat_direction(text: str) -> int | None:
    """Explicit zero-position notices; a malformed direction is never an exit."""
    if not isinstance(text, str) or not is_h_signal(text):
        return None
    counters = list(H_BS_PATTERN.finditer(text))
    positions = list(H_POSITION_PATTERN.finditer(text))
    flat = re.search(r'部位為\s*[:：]\s*(?:空手|無部位|0\s*口)', text)
    zero = (len(counters) == 1
            and int(counters[0]['buy']) == int(counters[0]['sell']) == 0)
    if positions or len(counters) > 1 or (counters and not zero):
        return None
    return 0 if zero or flat else None


def parse_h_event_direction(event: dict[str, Any]) -> int | None:
    """Accept the trusted bot or the dedicated, validated dual-source journal."""
    if event.get('source') == 'h3_dual_source':
        if not is_dual_source_event(event):
            return None
        return parse_h_direction(event.get('text', ''))
    sender = event.get("sender_username")
    if (event.get("event") != "received" or event.get("route") != "h"
            or not isinstance(sender, str)
            or sender.casefold() != ALLOWED_SENDER_USERNAME):
        return None
    return parse_h_direction(event.get("text", ""))


def is_dual_source_event(event: dict[str, Any]) -> bool:
    if (event.get('event') != 'received' or event.get('route') != 'h'
            or event.get('source') != 'h3_dual_source'
            or not isinstance(event.get('signal_key'), str)
            or not re.fullmatch(r'h3-dual:[1-9]\d*', event['signal_key'])
            or type(event.get('direction')) is not int or event['direction'] not in (-1, 0, 1)):
        return False
    reference = event.get('source_reference')
    if not isinstance(reference, dict):
        return False
    kind = event.get('source_kind')
    keys = ('rowid', 'signal_id') if kind == 'concord_sqlite' else ('chat_id', 'message_id')
    if kind not in ('concord_sqlite', 'telegram') or not all(type(reference.get(k)) is int for k in keys):
        return False
    direction = event['direction']
    return event.get('text') == f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})'


def normalize_h_record_message(text: str) -> str:
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return text
    start, end = match.span("quantity")
    return f"{text[:start]}1{text[end:]}"
