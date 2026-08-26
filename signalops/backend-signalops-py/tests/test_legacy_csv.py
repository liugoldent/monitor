from io import StringIO

from signalops.ingestion.legacy_csv import account_reference, read_legacy_csv

CSV_HEADER = (
    "received_at,message_time,account,strategy_code,raw_strategy_code,"
    "strategy_name,previous_position,new_position,action,side,quantity,signal\n"
)


def test_import_sanitizes_account_and_drops_raw_signal() -> None:
    source = StringIO(
        CSV_HEADER + "2026-06-25 04:30:06,2026-06-25 04:30:05,test-account-42,CFCPW3m,"
        "CFCPW3m,Strategy 3,0,1,enter,bull,1,private raw message\n"
    )

    batch = read_legacy_csv(source, account_salt="test-only-salt")

    assert batch.issues == []
    assert len(batch.events) == 1
    event = batch.events[0].event
    assert event.account_ref == account_reference("test-account-42", "test-only-salt")
    assert "test-account-42" not in event.model_dump_json()
    assert "private raw message" not in event.model_dump_json()
    assert event.occurred_at.isoformat() == "2026-06-24T20:30:05+00:00"


def test_import_reports_missing_timestamp_without_stopping_batch() -> None:
    source = StringIO(
        CSV_HEADER
        + ",,,OLD,OLD,Old Strategy,-1,0,exit,bear,1,old\n"
        + "2026-06-25 04:30:06,2026-06-25 04:30:05,,NEW,NEW,"
        "New Strategy,0,-1,enter,bear,1,new\n"
    )

    batch = read_legacy_csv(source, account_salt="test-only-salt")

    assert len(batch.events) == 1
    assert len(batch.issues) == 1
    assert batch.issues[0].row_number == 2
    assert "都缺失" in batch.issues[0].reason


def test_same_source_row_produces_same_event_identity() -> None:
    row = (
        CSV_HEADER + "2026-06-25 04:30:06,2026-06-25 04:30:05,,S1,S1,"
        "Strategy,1,-1,reverse,bear,1,message\n"
    )

    first = read_legacy_csv(StringIO(row), account_salt="salt").events[0]
    second = read_legacy_csv(StringIO(row), account_salt="salt").events[0]

    assert first.event.id == second.event.id
    assert first.fingerprint == second.fingerprint
