"""Offline checks for paired H2/H3 P&L in guerrilla notifications."""
from datetime import datetime
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {'MONGO_URI': 'mongodb://localhost'}):
    import monitor_mxf as monitor


class GuerrillaAlertTests(unittest.TestCase):
    def test_both_crossing_notifications_include_h2_and_h3(self):
        bar = datetime(2026, 11, 27, 13, 26, tzinfo=monitor.TZ)
        stamp = int(bar.timestamp() * 1000)
        symbol = monitor.GOODWAY_GUERRILLA_SYMBOLS[0]
        h3 = {'action': 'enter', 'timestamp': '2026-11-27T12:00:00', 'side': 'bull', 'price': '39300'}
        h2 = {'action': 'enter', 'side': 'bear', 'price': '39323'}
        trades = {monitor.H_TRADE_CSV_PATH: [h3], monitor.H2_TRADE_CSV_PATH: [h2]}
        with (patch.object(monitor, 'read_latest_mxf_close', return_value=(bar, 39348.0)),
              patch.object(monitor, 'read_recent_h3_signals', return_value=iter(())),
              patch.object(monitor, 'read_recent_csv_rows', side_effect=trades.__getitem__)):
            goodway = monitor.GoodwayGuerrillaAlerts()
            goodway.observe({symbol: {stamp - 60000: {'high': -1035}}})
            goodway.observe({symbol: {stamp: {'high': -1116}}})
            mxf_value = monitor.MxfValueGuerrillaAlerts()
            mxf_value.observe_snapshot({'mtx_bvav': -2001}, bar)

        for alerts in (goodway, mxf_value):
            self.assertEqual(len(alerts.pending), 1)
            message = alerts.pending[0]
            self.assertIn('H3 單口未實現損益：+48.00 點（多單，進場 39300）', message)
            self.assertIn('H2 單口未實現損益：-25.00 點（空單，進場 39323）', message)

    def test_h2_line_remains_visible_when_flat_or_missing(self):
        exit_trade = {'action': 'exiting', 'timestamp': '2026-11-27T12:00:00'}
        for rows, expected in (([exit_trade], '空手'), ([], '無交易紀錄')):
            with (self.subTest(expected=expected),
                  patch.object(monitor, 'read_latest_mxf_close', return_value=None),
                  patch.object(monitor, 'read_recent_h3_signals', return_value=iter(())),
                  patch.object(monitor, 'read_recent_csv_rows', return_value=rows)):
                self.assertIn(f'H2 單口未實現損益：{expected}', monitor.build_goodway_market_context())
