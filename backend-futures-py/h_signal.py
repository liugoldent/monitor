"""Broker-independent H signal rules shared by recording and account executors.

Directions are +1 (long) and -1 (short). Notification quantities never determine
an account's order size; each account owns its broker connection and order state.
"""
from __future__ import annotations

import re
from typing import Any


ALLOWED_SENDER_USERNAME = "taiwan_mxf_bot"
H_POSITION_PATTERN = re.compile(r"(?P<side>多|空)\s*(?P<quantity>\d+)\s*口")
H_BS_PATTERN = re.compile(
    r"[（(]\s*B\s*=\s*(?P<buy>\d+)\s+S\s*=\s*(?P<sell>\d+)\s*[)）]"
)


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
