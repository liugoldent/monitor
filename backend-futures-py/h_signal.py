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


def parse_h_event_direction(event: dict[str, Any]) -> int | None:
    """Account executors consume only received H events from the trusted bot."""
    sender = event.get("sender_username")
    if (event.get("event") != "received" or event.get("route") != "h"
            or not isinstance(sender, str)
            or sender.casefold() != ALLOWED_SENDER_USERNAME):
        return None
    return parse_h_direction(event.get("text", ""))


def normalize_h_record_message(text: str) -> str:
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return text
    start, end = match.span("quantity")
    return f"{text[:start]}1{text[end:]}"
