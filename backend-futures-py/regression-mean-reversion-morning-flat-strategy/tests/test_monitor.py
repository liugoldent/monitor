from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub

try:
    import filelock  # noqa: F401
except ModuleNotFoundError:
    filelock_stub = types.ModuleType("filelock")
    filelock_stub.FileLock = object
    filelock_stub.Timeout = RuntimeError
    sys.modules["filelock"] = filelock_stub

import monitor_and_trade as monitor  # noqa: E402
from strategy import Config, EngineState, MeanReversionEngine, PriceBar  # noqa: E402


class MonitorSafetyTests(unittest.TestCase):
    def test_first_start_does_not_replay_old_orders(self):
        bars = [PriceBar(datetime(2026, 8, 3, 9, 0), datetime(2026, 8, 3, 9, 1), 100, 101, 99, 100)]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "POSITION_PATH", Path(directory) / "position.json"
        ):
            engine, _ = monitor.initialize_runtime(bars, Config())
        self.assertEqual(engine.state.last_bar_time, bars[-1].bar_time)
        self.assertEqual(engine.state.position, 0)

    def test_live_mode_does_not_resend_same_target(self):
        runtime = {"last_order_attempt_target": 1}
        with patch.dict(monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}), patch.object(
            monitor, "execute_target_position"
        ) as execute:
            text = monitor.execute_live_target(runtime, 1, trigger="test")
        execute.assert_not_called()
        self.assertIn("不重送", text)

    def test_restart_catchup_never_sends_historical_order(self):
        state = EngineState(
            position=1,
            entry_price=100,
            target_price=120,
            stop_price=0,
            last_bar_time=datetime(2026, 8, 3, 13, 19),
        )
        engine = MeanReversionEngine(Config(), state)
        bars = [PriceBar(datetime(2026, 8, 3, 13, 20), datetime(2026, 8, 3, 13, 21), 110, 111, 109, 110)]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "H_TRADE_PATH", Path(directory) / "h.csv"
        ), patch.object(
            monitor, "EF_SIGNAL_PATH", Path(directory) / "ef.csv"
        ), patch.object(
            monitor, "append_action"
        ), patch.object(
            monitor, "write_position"
        ), patch.object(
            monitor, "execute_live_target"
        ) as execute:
            monitor.process_new_bars(
                engine, {}, bars, Config(), execute_orders=False, notify=False
            )
        execute.assert_not_called()
        self.assertEqual(engine.state.position, 0)


if __name__ == "__main__":
    unittest.main()
