from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Result, daily_net_points, max_consecutive_negative  # noqa: E402
from strategy import PriceBar  # noqa: E402


def bar(value: str) -> PriceBar:
    timestamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return PriceBar(timestamp, timestamp, 100.0, 100.0)


class DailyAttributionTests(unittest.TestCase):
    def test_preceding_night_is_attributed_to_next_open_date(self):
        bars = [
            bar("2026-09-01 15:00:00"),
            bar("2026-09-02 08:45:00"),
            bar("2026-09-02 13:44:00"),
        ]
        result = Result(
            equity_curve=[
                (bars[0].bar_time, 20.0, 1),
                (bars[1].bar_time, 40.0, 1),
                (bars[2].bar_time, 60.0, 2),
            ]
        )
        self.assertEqual(
            daily_net_points(
                result,
                bars,
                start=bars[0].bar_time,
                end=bars[-1].bar_time,
                one_way_cost=2.0,
            ),
            [(bars[1].bar_time.date(), 56.0)],
        )

    def test_partial_final_night_is_not_reported(self):
        bars = [
            bar("2026-09-02 08:45:00"),
            bar("2026-09-02 13:44:00"),
            bar("2026-09-02 15:00:00"),
        ]
        result = Result(
            equity_curve=[
                (bars[0].bar_time, 10.0, 0),
                (bars[1].bar_time, 20.0, 0),
                (bars[2].bar_time, 30.0, 0),
            ]
        )
        self.assertEqual(
            daily_net_points(
                result,
                bars,
                start=bars[0].bar_time,
                end=bars[-1].bar_time,
                one_way_cost=0.0,
            ),
            [(bars[0].bar_time.date(), 20.0)],
        )

    def test_counts_consecutive_negative_days(self):
        self.assertEqual(max_consecutive_negative([1, -1, -2, 0, -1]), 2)


if __name__ == "__main__":
    unittest.main()
