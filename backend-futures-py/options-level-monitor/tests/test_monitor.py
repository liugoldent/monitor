from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitor import (  # noqa: E402
    CLEAR_SCREEN_MARKER,
    clear_terminal,
    next_session_open,
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


if __name__ == "__main__":
    unittest.main()
