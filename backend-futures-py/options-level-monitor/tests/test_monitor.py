from __future__ import annotations

import sys
import unittest
import csv
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import AnalysisResult, FuturesQuote, LevelCandidate, MarketSnapshot  # noqa: E402
from monitor import (  # noqa: E402
    CLEAR_SCREEN_MARKER,
    clear_terminal,
    next_session_open,
    save_direction_record,
)


TAIPEI_TZ = ZoneInfo("Asia/Taipei")


class SessionScheduleTests(unittest.TestCase):
    def at(self, hour: int, minute: int) -> datetime:
        return datetime(2026, 9, 4, hour, minute, tzinfo=TAIPEI_TZ)

    def test_morning_break_boundaries(self) -> None:
        self.assertIsNone(next_session_open(self.at(4, 59)))
        self.assertEqual(next_session_open(self.at(5, 0)), self.at(8, 45))
        self.assertEqual(next_session_open(self.at(8, 44)), self.at(8, 45))
        self.assertIsNone(next_session_open(self.at(8, 45)))

    def test_afternoon_break_boundaries(self) -> None:
        self.assertIsNone(next_session_open(self.at(13, 44)))
        self.assertEqual(next_session_open(self.at(13, 45)), self.at(15, 0))
        self.assertEqual(next_session_open(self.at(14, 59)), self.at(15, 0))
        self.assertIsNone(next_session_open(self.at(15, 0)))


class ClearScreenTests(unittest.TestCase):
    def test_clear_terminal_emits_watcher_marker(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            clear_terminal()
        self.assertEqual(output.getvalue(), f"{CLEAR_SCREEN_MARKER}\n")


class DirectionCsvTests(unittest.TestCase):
    def snapshot(self, captured_at: datetime, last: float, volume: int) -> MarketSnapshot:
        return MarketSnapshot(
            captured_at=captured_at.isoformat(timespec="seconds"),
            futures=FuturesQuote(
                bid=last - 1,
                ask=last + 1,
                last=last,
                change=25,
                change_percent=0.05,
                volume=volume,
                open=last - 20,
                high=last + 50,
                low=last - 50,
                basis=10,
                reference=last - 25,
                open_interest=100_000,
                quote_time=captured_at.strftime("%H:%M:%S"),
                trade_date="2026/09/04",
            ),
            options=(),
            option_trade_date="2026/09/04",
            option_symbol="WTX2U6",
        )

    def result(self, score: float | None) -> AnalysisResult:
        support = LevelCandidate(46500, 46510, 80, 1200, 30, 100, "support")
        resistance = LevelCandidate(47000, 47010, 75, 1100, 25, 400, "resistance")
        return AnalysisResult(
            option_center=46690,
            atm_strike=46700,
            expected_move=500,
            expected_low=46200,
            expected_high=47200,
            direction_score=score,
            direction_label="溫和偏多" if score is not None else "資料累積中",
            supports=(support,),
            resistances=(resistance,),
        )

    def test_appends_header_once_and_records_direction_context(self) -> None:
        first_at = datetime(2026, 9, 4, 21, 0, tzinfo=TAIPEI_TZ)
        first = self.snapshot(first_at, 46700, 1000)
        second = self.snapshot(first_at + timedelta(seconds=60), 46720, 1035)
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "direction.csv"
            save_direction_record(first, self.result(None), None, path)
            save_direction_record(second, self.result(32.5), first, path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["direction_code"], "warming_up")
        self.assertEqual(rows[1]["direction_code"], "mild_bull")
        self.assertEqual(rows[1]["interval_seconds"], "60.0")
        self.assertEqual(rows[1]["futures_change_1m"], "20")
        self.assertEqual(rows[1]["futures_volume_change_1m"], "35")
        self.assertEqual(rows[1]["support_1_level"], "46510")
        self.assertEqual(rows[1]["resistance_1_oi"], "1100")


if __name__ == "__main__":
    unittest.main()
