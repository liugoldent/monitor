import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from h_core import Contract, Store, TradingError, execute_h, parse_inventory, parse_signal
from capital_broker import build_order

C = Contract('MICRO10', 'ORDER_MICRO', '202610', 'TMF202610', 'TMF')
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def signal(self, **overrides):
        event = dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                     chat_id=-1, message_id=10, received_at=NOW.isoformat(),
                     text='浩克3V3 訊號通知 多3口')
        event.update(overrides)
        return event

    def test_signal_size_does_not_change_one_contract_entry(self):
        self.assertEqual(parse_signal(self.signal(), NOW), ('-1:10', 1, NOW.timestamp()))

    def test_bs_signal_uses_shared_rules_and_fixed_account_entry(self):
        import h_core
        from h_signal import parse_h_event_direction
        self.assertIs(h_core.parse_h_event_direction, parse_h_event_direction)
        for buy, sell, direction in ((0, 8, -1), (9, 0, 1)):
            with self.subTest(buy=buy, sell=sell):
                event = self.signal(text=f'自動交易\n浩克3V3 交易訊號通知 (B={buy} S={sell})\nFrom:')
                signal = parse_signal(event, NOW)
                self.assertEqual(signal, ('-1:10', direction, NOW.timestamp()))
                broker, calls = self.broker(3)
                execute_h(broker, signal[1], self.store, signal[0])
                self.assertEqual(calls, [(-3, 'flatten', 0), (direction, 'entry', direction)])

    def test_bs_still_requires_fresh_trusted_unambiguous_signal(self):
        text = '浩克3V3 交易訊號通知 (B=0 S=1)'
        for event in (self.signal(text=text, sender_username='fake'),
                      self.signal(text=text, event='discord_delivery'),
                      self.signal(text=text, received_at=(NOW-timedelta(seconds=31)).isoformat()),
                      self.signal(text='浩克3V3 交易訊號通知 (B=1 S=1)')):
            self.assertIsNone(parse_signal(event, NOW))

    def test_same_signal_is_consumed_independently_by_each_account(self):
        signal = parse_signal(self.signal(text='浩克3V3 交易訊號通知 (B=0 S=1)'), NOW)
        second_store = Store(Path(self.temp.name) / 'second-account.sqlite')
        try:
            for store, position in ((self.store, 3), (second_store, -2)):
                self.assertTrue(store.consume(*signal))
                self.assertFalse(store.consume(*signal))
                broker, calls = self.broker(position)
                execute_h(broker, signal[1], store, signal[0])
                self.assertEqual(calls, [(-position, 'flatten', 0), (-1, 'entry', -1)])
        finally:
            second_store.db.close()

    def test_forged_sender_stale_and_ambiguous_rejected(self):
        for event in (self.signal(sender_username='fake'),
                      self.signal(received_at=(NOW-timedelta(seconds=31)).isoformat()),
                      self.signal(text='浩克3 訊號通知 多1口 空1口'),
                      self.signal(received_at='bad'), self.signal(message_id=None)):
            self.assertIsNone(parse_signal(event, NOW))

    def test_duplicate_window_is_not_extended(self):
        self.assertTrue(self.store.consume('a', 1, 100))
        self.assertFalse(self.store.consume('b', 1, 109))
        self.assertTrue(self.store.consume('c', 1, 111))
        self.assertFalse(self.store.consume('c', 1, 130))
        self.assertTrue(self.store.consume('d', -1, 112))

    def broker(self, position, fail=False):
        calls = []
        class Broker:
            def __init__(self):
                self.current = position
            def position(self):
                return self.current
            def send_and_confirm(self, delta, phase, target):
                calls.append((delta, phase, target))
                if fail:
                    raise TradingError('timeout')
                self.current = target
                return target
        return Broker(), calls

    def test_flatten_three_then_open_one(self):
        broker, calls = self.broker(3)
        self.assertEqual(execute_h(broker, -1, self.store, 'test'), -1)
        self.assertEqual(calls, [(-3, 'flatten', 0), (-1, 'entry', -1)])
        self.assertIsNone(self.store.pending())

    def test_same_direction_still_reopens(self):
        broker, calls = self.broker(1)
        execute_h(broker, 1, self.store, 'test')
        self.assertEqual(calls, [(-1, 'flatten', 0), (1, 'entry', 1)])

    def test_failed_flatten_never_enters_and_blocks_restart(self):
        broker, calls = self.broker(3, fail=True)
        with self.assertRaises(TradingError):
            execute_h(broker, -1, self.store, 'test')
        self.assertEqual(len(calls), 1)
        self.store.db.close()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        with self.assertRaises(TradingError):
            execute_h(broker, 1, self.store, 'new')
        self.assertEqual(len(calls), 1)

    def inventory(self, raw='', status=0):
        return parse_inventory(SimpleNamespace(StatusCode=status, RawData=raw), 'ACC', 'USER', C)

    def test_inventory_empty_and_net_quantity(self):
        self.assertEqual(self.inventory(), 0)
        self.assertEqual(self.inventory('001,查無資料,ACC', status=1), 0)
        with self.assertRaises(TradingError):
            self.inventory('001,查無資料,OTHER', status=1)
        self.assertEqual(self.inventory('TF,ACC,TMF202610,S,3,0,100,0,0,USER#'), -3)

    def test_inventory_failures_do_not_become_flat(self):
        rows = ['bad', 'TF,OTHER,TMF202610,B,1,0,100,0,0,USER',
                'TF,ACC,TMF202611,B,1,0,100,0,0,USER',
                'TF,ACC,TMF202610,unknown,1,0,100,0,0,USER',
                'TF,ACC,TMF202610,B,1,1,100,0,0,USER',
                'TF,ACC,TMF202610,B,1,0,100,0,0,USER#TF,ACC,TMF202610,S,1,0,100,0,0,USER']
        for raw in rows:
            with self.assertRaises(TradingError):
                self.inventory(raw)
        with self.assertRaises(TradingError):
            self.inventory(status=999)

    def test_vendor_structure_preserves_ioc_and_explicit_close_open(self):
        closing = build_order('ACC', C, -3, 'flatten')
        opening = build_order('ACC', C, -1, 'entry')
        self.assertEqual((closing.nTradeType, closing.nPriceFlag, closing.nQty, closing.nBuySell), (1, 0, 3, 1))
        self.assertEqual(closing.strOrderType, b'1')
        self.assertEqual(opening.strOrderType, b'0')
        self.assertEqual(opening.strStockNo, b'ORDER_MICRO')
        self.assertEqual(opening.strSettleYM, b'202610')

    def test_bad_contract_or_quantity_rejected(self):
        for delta in (0, True, 1.5):
            with self.assertRaises(TradingError):
                build_order('ACC', C, delta, 'entry')
        with self.assertRaises(TradingError):
            Contract('', '', '202613', '', '').validate()

    def test_watcher_skips_history_and_consumes_only_new_complete_lines(self):
        from h_monitor import watch
        source = Path(self.temp.name) / 'relay.jsonl'
        now = datetime.now(timezone.utc).isoformat()
        old = self.signal(received_at=now, message_id=101)
        new = self.signal(received_at=now, message_id=102)
        source.write_text(json.dumps(old) + '\n', encoding='utf-8')
        calls = 0
        class StopWatch(Exception):
            pass
        def next_poll(_seconds):
            nonlocal calls
            calls += 1
            if calls == 1:
                with source.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(new) + '\n')
                    handle.write('{"incomplete":')
            else:
                raise StopWatch()
        with patch('h_monitor.time.sleep', next_poll), self.assertRaises(StopWatch):
            watch({'signal_source': source}, self.store)
        self.assertIsNone(self.store.db.execute('SELECT id FROM signals WHERE id=?', ('-1:101',)).fetchone())
        self.assertIsNotNone(self.store.db.execute('SELECT id FROM signals WHERE id=?', ('-1:102',)).fetchone())


if __name__ == '__main__':
    unittest.main()
