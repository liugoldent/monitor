from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


STRATEGY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STRATEGY_DIR))

from backtest import _trade_exit
from strategy import (
    BreakRetestEngine,
    Config,
    DailyBar,
    EngineState,
    PriceBar,
    daily_regimes,
    delayed_execution_index,
    find_breakout_signal,
    find_retest_entry,
    merge_daily_history,
    opening_range,
    state_from_dict,
    state_to_dict,
    trading_day_for,
)


def bar(
    text: str,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    record_delay_seconds: int = 61,
) -> PriceBar:
    timestamp = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return PriceBar(
        timestamp,
        timestamp + timedelta(seconds=record_delay_seconds),
        open_,
        high,
        low,
        close,
    )


class StrategyTests(unittest.TestCase):
    def opening_engine(self) -> BreakRetestEngine:
        engine = BreakRetestEngine(
            Config(
                opening_minutes=30,
                minimum_opening_bars=30,
                retest_points=5,
                limit_penetration_points=1,
                stop_points=10,
                target_points=10,
            )
        )
        regime = (1, 120.0, 110.0)
        start = datetime(2026, 8, 20, 8, 45)
        for offset in range(30):
            timestamp = start + timedelta(minutes=offset)
            engine.process_bar(
                PriceBar(timestamp, timestamp + timedelta(minutes=1), 100, 105, 95, 100),
                regime,
            )
        return engine

    def test_friday_night_belongs_to_monday_trading_day(self) -> None:
        self.assertEqual(
            trading_day_for(datetime(2026, 8, 28, 15, 0)),
            date(2026, 8, 31),
        )
        self.assertEqual(
            trading_day_for(datetime(2026, 8, 29, 2, 0)),
            date(2026, 8, 31),
        )

    def test_daily_regime_uses_only_previous_completed_days(self) -> None:
        rows = [
            DailyBar(date(2026, 1, day), 100, 100, 100, float(day))
            for day in range(1, 22)
        ]
        first = daily_regimes(rows, 20)[date(2026, 1, 21)]
        changed_current_day = list(rows)
        changed_current_day[20] = DailyBar(date(2026, 1, 21), 999, 999, 999, 999)
        second = daily_regimes(changed_current_day, 20)[date(2026, 1, 21)]
        self.assertEqual(first, second)
        self.assertEqual(first[0], 1)

    def test_complete_minute_day_replaces_partial_archive_overlap(self) -> None:
        archive = [DailyBar(date(2026, 8, 20), 100, 101, 99, 100)]
        rows = []
        start = datetime(2026, 8, 20, 8, 45)
        for offset in range(300):
            timestamp = start + timedelta(minutes=offset)
            rows.append(
                PriceBar(timestamp, timestamp + timedelta(minutes=1), 200, 201, 199, 200)
            )
        merged = merge_daily_history(archive, rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].close, 200)

    def test_breakout_requires_a_close_cross_after_opening_range(self) -> None:
        rows = []
        start = datetime(2026, 8, 20, 8, 45)
        for offset in range(30):
            timestamp = start + timedelta(minutes=offset)
            rows.append(
                PriceBar(timestamp, timestamp + timedelta(minutes=1), 100, 105, 95, 100)
            )
        rows.append(bar("2026-08-20 09:15:00", high=106, low=100, close=106))
        config = Config(minimum_opening_bars=30)
        self.assertEqual(opening_range(rows, config), (105, 95))
        signal = find_breakout_signal(
            rows,
            side=1,
            opening_high=105,
            opening_low=95,
            config=config,
        )
        self.assertEqual(signal, rows[-1])

    def test_execution_waits_one_full_minute_after_record_time(self) -> None:
        rows = [
            bar("2026-08-20 09:15:00"),
            bar("2026-08-20 09:16:00"),
            bar("2026-08-20 09:17:00"),
        ]
        index = delayed_execution_index(rows, [row.bar_time for row in rows], rows[0])
        self.assertEqual(index, 2)

    def test_retest_limit_gets_price_improvement_when_open_gaps_through(self) -> None:
        rows = [
            bar("2026-08-20 09:17:00", open_=101, high=103, low=100, close=102),
            bar("2026-08-20 09:18:00", open_=97, high=100, low=96, close=99),
        ]
        result = find_retest_entry(
            rows,
            start_index=0,
            side=1,
            limit_price=98,
            penetration_points=1,
            expiry=datetime(2026, 8, 20, 9, 30),
        )
        self.assertEqual(result, (1, 97))

    def test_same_bar_stop_and_target_is_counted_as_stop(self) -> None:
        rows = [bar("2026-08-20 09:17:00", high=111, low=89)]
        result = _trade_exit(
            rows,
            entry_index=0,
            side=1,
            stop_price=90,
            target_price=110,
            force_flat_time=datetime.strptime("11:00", "%H:%M").time(),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1:3], (90, "stop"))

    def test_entry_bar_target_is_not_credited_without_tick_order(self) -> None:
        rows = [
            bar("2026-08-20 09:17:00", high=111, low=98),
            bar("2026-08-20 09:18:00", high=111, low=99),
        ]
        result = _trade_exit(
            rows,
            entry_index=0,
            side=1,
            stop_price=90,
            target_price=110,
            force_flat_time=datetime.strptime("11:00", "%H:%M").time(),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[:3], (1, 110, "target"))

    def test_live_engine_waits_for_delayed_retest_then_exits_at_target(self) -> None:
        engine = self.opening_engine()
        regime = (1, 120.0, 110.0)
        actions, decisions = engine.process_bar(
            bar("2026-08-20 09:15:00", open_=100, high=107, low=100, close=106),
            regime,
        )
        self.assertEqual(actions, [])
        self.assertEqual(decisions[0].kind, "breakout_pending")
        self.assertEqual(engine.state.pending.limit_price, 100)

        actions, _ = engine.process_bar(
            bar("2026-08-20 09:16:00", open_=101, high=103, low=99, close=101),
            regime,
        )
        self.assertEqual(actions, [])

        actions, decisions = engine.process_bar(
            bar("2026-08-20 09:17:00", open_=101, high=104, low=99, close=102),
            regime,
        )
        self.assertEqual([(value.action, value.price) for value in actions], [("enter", 100)])
        self.assertEqual(decisions[0].kind, "retest_filled")

        actions, _ = engine.process_bar(
            bar("2026-08-20 09:18:00", open_=102, high=110, low=101, close=109),
            regime,
        )
        self.assertEqual([(value.reason, value.pnl_points) for value in actions], [("target", 10)])
        self.assertEqual(engine.state.position, 0)

    def test_live_engine_entry_bar_credits_only_adverse_stop(self) -> None:
        engine = self.opening_engine()
        regime = (1, 120.0, 110.0)
        engine.process_bar(
            bar("2026-08-20 09:15:00", open_=100, high=107, low=100, close=106),
            regime,
        )
        actions, _ = engine.process_bar(
            bar("2026-08-20 09:17:00", open_=100, high=111, low=89, close=105),
            regime,
        )
        self.assertEqual([value.action for value in actions], ["enter", "exit"])
        self.assertEqual(actions[-1].reason, "entry_bar_stop")
        self.assertEqual(actions[-1].pnl_points, -10)

    def test_live_engine_expires_unfilled_retest(self) -> None:
        engine = self.opening_engine()
        regime = (1, 120.0, 110.0)
        engine.process_bar(
            bar("2026-08-20 09:15:00", open_=100, high=107, low=100, close=106),
            regime,
        )
        _, decisions = engine.process_bar(
            bar("2026-08-20 09:45:00", open_=106, high=108, low=105, close=107),
            regime,
        )
        self.assertEqual(decisions[0].kind, "retest_expired")
        self.assertIsNone(engine.state.pending)

    def test_engine_state_json_round_trip_preserves_pending_and_position_fields(self) -> None:
        engine = self.opening_engine()
        engine.process_bar(
            bar("2026-08-20 09:15:00", open_=100, high=107, low=100, close=106),
            (1, 120.0, 110.0),
        )
        restored = state_from_dict(state_to_dict(engine.state))
        self.assertEqual(restored, engine.state)
        self.assertIsInstance(restored, EngineState)


if __name__ == "__main__":
    unittest.main()
