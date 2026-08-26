import csv
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TextIO
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from signalops.schemas import SignalEvent

SOURCE_TIMEZONE = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class PreparedSignalEvent:
    event: SignalEvent
    fingerprint: str


@dataclass(frozen=True)
class ImportIssue:
    row_number: int
    reason: str


@dataclass(frozen=True)
class ImportBatch:
    events: list[PreparedSignalEvent]
    issues: list[ImportIssue]


def account_reference(account: str, salt: str) -> str | None:
    normalized = account.strip()
    if not normalized:
        return None
    if not salt:
        raise ValueError("含有帳號資料時必須提供 account salt")
    digest = hashlib.sha256(f"{salt}:{normalized}".encode()).hexdigest()
    return f"acct_{digest[:16]}"


def parse_source_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SOURCE_TIMEZONE)
    return parsed.astimezone(UTC)


def event_fingerprint(values: list[str]) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _parse_row(row: dict[str, str], account_salt: str) -> PreparedSignalEvent:
    received_value = (row.get("received_at") or "").strip()
    message_value = (row.get("message_time") or "").strip()
    if not received_value and not message_value:
        raise ValueError("received_at 與 message_time 都缺失")

    received_at = parse_source_time(received_value or message_value)
    occurred_at = parse_source_time(message_value or received_value)
    strategy_code = (row.get("strategy_code") or "").strip()
    raw_strategy_code = (row.get("raw_strategy_code") or "").strip()
    strategy_name = (row.get("strategy_name") or strategy_code).strip()
    previous_position = int(Decimal(row["previous_position"]))
    new_position = int(Decimal(row["new_position"]))
    quantity = Decimal(row["quantity"])
    reference_value = (row.get("reference_price") or "").strip()
    reference_price = Decimal(reference_value) if reference_value else None

    fingerprint = event_fingerprint(
        [
            occurred_at.isoformat(),
            received_at.isoformat(),
            strategy_code,
            str(previous_position),
            str(new_position),
            (row.get("action") or "").strip(),
            str(quantity),
        ]
    )
    attributes = {}
    if raw_strategy_code and raw_strategy_code != strategy_code:
        attributes["producer_strategy_code"] = raw_strategy_code

    event = SignalEvent(
        id=uuid5(NAMESPACE_URL, f"signalops:{fingerprint}"),
        occurred_at=occurred_at,
        received_at=received_at,
        source="legacy_csv",
        instrument=(row.get("instrument") or "MXF").strip() or "MXF",
        strategy_code=strategy_code,
        strategy_name=strategy_name,
        account_ref=account_reference(row.get("account") or "", account_salt),
        previous_position=previous_position,
        new_position=new_position,
        action=(row.get("action") or "").strip(),
        side=(row.get("side") or "").strip(),
        quantity=quantity,
        reference_price=reference_price,
        attributes=attributes,
    )
    return PreparedSignalEvent(event=event, fingerprint=fingerprint)


def read_legacy_csv(source: Path | TextIO, account_salt: str) -> ImportBatch:
    should_close = isinstance(source, Path)
    stream = source.open(encoding="utf-8-sig", newline="") if should_close else source
    events: list[PreparedSignalEvent] = []
    issues: list[ImportIssue] = []

    try:
        for row_number, row in enumerate(csv.DictReader(stream), start=2):
            try:
                events.append(_parse_row(row, account_salt))
            except (KeyError, ValueError, ArithmeticError, ValidationError) as exc:
                issues.append(ImportIssue(row_number=row_number, reason=str(exc)))
    finally:
        if should_close:
            stream.close()

    return ImportBatch(events=events, issues=issues)
