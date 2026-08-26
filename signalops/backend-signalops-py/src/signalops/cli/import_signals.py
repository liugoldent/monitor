import argparse
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert

from signalops.db import SessionLocal
from signalops.ingestion.legacy_csv import read_legacy_csv
from signalops.models import OutboxEventModel, SignalEventModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="匯入並匿名化舊版策略訊號事件。")
    parser.add_argument("csv_path", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    account_salt = os.getenv("SIGNALOPS_ACCOUNT_SALT", "")
    if not account_salt:
        raise SystemExit("匯入前必須設定 SIGNALOPS_ACCOUNT_SALT")

    batch = read_legacy_csv(args.csv_path, account_salt=account_salt)
    inserted = 0
    with SessionLocal.begin() as session:
        for prepared in batch.events:
            values = prepared.event.model_dump()
            values["fingerprint"] = prepared.fingerprint
            inserted_id = session.scalar(
                insert(SignalEventModel)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[SignalEventModel.fingerprint])
                .returning(SignalEventModel.id)
            )
            if inserted_id is not None:
                inserted += 1
                session.add(
                    OutboxEventModel(
                        id=uuid4(),
                        aggregate_type="signal_event",
                        aggregate_id=str(prepared.event.id),
                        event_type="signal.event.v1",
                        payload=prepared.event.model_dump(mode="json"),
                        occurred_at=prepared.event.occurred_at,
                    )
                )

    print(f"已驗證={len(batch.events)} 已新增={inserted} 已略過={len(batch.issues)}")
    for issue in batch.issues[:20]:
        print(f"第 {issue.row_number} 列：{issue.reason}")
    if len(batch.issues) > 20:
        print(f"……另有 {len(batch.issues) - 20} 個問題")


if __name__ == "__main__":
    main()
