import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from h3_entry_watch import Curve, WatchError, build_notice, seed_from_export


TZ = ZoneInfo("Asia/Taipei")


class H3EntryWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / "seed.json"
        self.events = self.root / "events.jsonl"
        self.prices = self.root / "prices.csv"
        self.seed.write_text(json.dumps(dict(
            version=2, entry_threshold="0.025", recovery_threshold="0.0125", source_sha256="a" * 64,
            first_at="2026-01-01 09:00:00", last_at="2026-01-01 09:00:00",
            cost_points_one_way="2.4", curve=dict(
                equity_points="0", peak_points="0", entry_price="10000", side=1,
                armed=False, phase="waiting_drawdown", closed_trades=0,
                high_count=0, recovered=False, entry_ready=False, qualifies=False))), encoding="utf-8")
        self.events.write_text("", encoding="utf-8")
        with self.prices.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(["Record Time", "TradingView Time", "Symbol", "Timeframe", "Close"])

    def signal(self, minute, side, price, key, *, sender="taiwan_mxf_bot"):
        stamp = f"2026-01-01 09:{minute:02d}:05"
        event = dict(event="received", route="h", received_at=stamp.replace(" ", "T") + "+08:00",
                     chat_id=1, message_id=key, sender_username=sender,
                     text=f"浩克3V3 交易訊號通知 (B={int(side == 1)} S={int(side == -1)})")
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        with self.prices.open("a", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow([stamp[:-2] + "00", stamp[:-2] + "00", "MXF1!", "1", price])
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)

    def notice(self, stamp, key, side, **kwargs):
        return build_notice(self.seed, self.events, self.prices, stamp, f"1:{key}", side, **kwargs)

    def test_2_5_percent_arms_only_and_recovery_produces_entry_window(self):
        first = self.signal(1, -1, 9745, 1)
        notice = self.notice(first, 1, -1)
        self.assertIn("259.80 點", notice)
        self.assertIn("2.67%", notice)
        self.assertIn("等待修復", notice)
        self.assertNotIn("本次新訊號符合", notice)
        second = self.signal(2, 1, 9598, 2)
        notice = self.notice(second, 2, 1)
        self.assertIn("117.60 點", notice)
        self.assertIn("1.23%", notice)
        self.assertIn("第2口加碼觀察條件成立", notice)
        self.assertIn("第1口從新H3持續跑、不等回撤", notice)

    def test_restart_rebuild_is_deterministic_and_duplicates_do_not_close_again(self):
        self.signal(1, -1, 9745, 1)
        stamp = self.signal(2, 1, 9598, 2)
        initial = self.notice(stamp, 2, 1)
        self.assertEqual(initial, self.notice(stamp, 2, 1))
        # Same event replay and same-direction new receipt must not add P&L.
        self.signal(2, 1, 9598, 2)
        stamp = self.signal(3, 1, 9800, 3)
        repeated = self.notice(stamp, 3, 1, new_transition=False)
        self.assertIn("117.60 點", repeated)
        self.assertIn("未產生新的進場提示", repeated)
        self.assertNotIn("本次新訊號符合", repeated)

    def test_exact_thresholds_and_recovery_without_prior_drawdown(self):
        state = json.loads(self.seed.read_text())["curve"]
        state["equity_points"] = "-50"
        curve = Curve(state)
        curve.check(10000)
        self.assertEqual(curve.phase, "waiting_drawdown")
        curve.equity = Decimal("-250")
        curve.check(10000)
        self.assertEqual(curve.phase, "waiting_recovery")
        curve.equity = Decimal("-125")
        curve.check(10000)
        self.assertEqual(curve.phase, "entry_window")

    def test_later_signal_cannot_change_earlier_decision(self):
        stamp = self.signal(1, -1, 9745, 1)
        original = self.notice(stamp, 1, -1)
        self.signal(2, 1, 9598, 2)
        self.assertEqual(original, self.notice(stamp, 1, -1))

    def test_untrusted_received_events_do_not_change_shadow_book(self):
        self.signal(1, -1, 100, 99, sender="untrusted")
        stamp = self.signal(2, -1, 9745, 1)
        self.assertIn("259.80 點", self.notice(stamp, 1, -1))

    def test_missing_or_stale_price_never_produces_entry_hint(self):
        stamp = self.signal(1, -1, 9895, 1)
        stale = stamp.replace(minute=10)
        self.assertIn("資料待核對", self.notice(stale, 1, -1))
        self.prices.write_text("Record Time,TradingView Time,Symbol,Timeframe,Close\n", encoding="utf-8")
        self.assertIn("暫不判定", self.notice(stamp, 1, -1))

    def test_missing_intermediate_price_stops_even_if_current_price_is_fresh(self):
        self.signal(1, -1, 9895, 1)
        stamp = self.signal(8, 1, 9828, 2)
        lines = self.prices.read_text().splitlines()
        self.prices.write_text("\n".join([lines[0], lines[-1]]) + "\n", encoding="utf-8")
        self.assertIn("資料待核對", self.notice(stamp, 2, 1))

    def test_wrong_symbol_or_future_bar_cannot_supply_a_price(self):
        stamp = self.signal(1, -1, 9895, 1)
        for symbol, bar in [("OTHER", "2026-01-01 09:01:00"), ("MXF1!", "2026-01-01 09:02:00")]:
            self.prices.write_text(
                "Record Time,TradingView Time,Symbol,Timeframe,Close\n"
                f"2026-01-01 09:01:00,{bar},{symbol},1,9895\n", encoding="utf-8")
            self.assertIn("資料待核對", self.notice(stamp, 1, -1))

    def test_bad_seed_and_unrecorded_signal_are_not_actionable(self):
        stamp = self.signal(1, -1, 9895, 1)
        self.assertIn("資料待核對", self.notice(stamp, 1, 1))
        state = json.loads(self.seed.read_text())
        state["curve"]["peak_points"] = "-1"
        self.seed.write_text(json.dumps(state))
        self.assertIn("資料待核對", self.notice(stamp, 1, -1))

    def export(self, rows):
        source = self.root / "history.csv"
        with source.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["No", "類型", "日期", "時間", "價格", "數量", "獲利($)"])
            writer.writerows(rows)
        return source

    def test_export_recomputes_closed_curve_and_ignores_future_entry_pnl(self):
        source = self.export([
            [1, "Entry Long", "2026/01/01", "09:00:00", 10000, 1, "future profit ignored"],
            [1, "Exit Long", "2026/01/01", "09:01:00", 9745, 1, -2550],
            [2, "Entry Short", "2026/01/01", "09:01:00", 9745, 1, "future profit ignored"],
            ["", "", "1900/01/00", "", "", "", ""],
        ])
        seed = seed_from_export(source)
        self.assertEqual(seed["curve"]["equity_points"], "-259.8")
        self.assertEqual(seed["curve"]["closed_trades"], 1)
        self.assertTrue(seed["curve"]["armed"])
        self.assertEqual(seed["last_at"], "2026-01-01 09:01:00")

    def test_export_rejects_mixed_quantity_or_wrong_point_value(self):
        source = self.export([[1, "Entry Long", "2026/01/01", "09:00:00", 10000, 2, ""]])
        with self.assertRaises(WatchError):
            seed_from_export(source)
        source = self.export([
            [1, "Entry Long", "2026/01/01", "09:00:00", 10000, 1, ""],
            [1, "Exit Long", "2026/01/01", "09:01:00", 9895, 1, -5250],
        ])
        with self.assertRaises(WatchError):
            seed_from_export(source)


if __name__ == "__main__":
    unittest.main()
