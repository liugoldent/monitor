import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pandas as pd

from shared_h import Store, TradingError, execute_h, parse_signal, watch
from kgi_broker import (Contract, ExplicitPositionBridge, KgiBroker, POSITION_COLUMNS,
                        filled_quantity, parse_position)
from kgi_monitor import account_key, main, read_config, read_unit

C = Contract('TMF', '202610', 'TMFJ6')
class State(Enum):
    Pending = 0
    Submitted = 1
    Filled = 2
    Failed = 3
    PartFilled = 4
    Cancelled = 5
    PartFilled_Cancelled = 6


def frame(*rows):
    return pd.DataFrame(rows, columns=sorted(POSITION_COLUMNS))


def row(**kw):
    result = dict(Symbol='TMFJ6', BrokerId='BROKER', Account='ACC', Exchange='TIMEX',
                  ComType='F', ComID='TMF', ComYM='202610', BS='B', OTQty='1', Group='', Trader='')
    return result | kw


def trade(state=State.Filled, qty=1, failed=False):
    return NS(order=NS(order_id='ORDER1', symbol='TMFJ6'),
              order_status=NS(status=state, deals=[NS(reportseq='FILL1', quantity=qty)] if qty else []),
              operations=[NS(status=State.Failed if failed else State.Submitted)])


class KgiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_inventory_requires_response_and_matching_account_contract(self):
        self.assertEqual(parse_position(frame(), 'ACC', 'BROKER', C), 0)
        self.assertEqual(parse_position(frame(row(BS='S', OTQty='3')), 'ACC', 'BROKER', C), -3)
        invalid = [None, pd.DataFrame(), frame(row(Account='OTHER')), frame(row(BrokerId='OTHER')),
                   frame(row(ComYM='202611')), frame(row(Symbol='TMFK6')), frame(row(Group='sub')),
                   frame(row(BS='?')), frame(row(OTQty='1.1')), frame(row(OTQty='NaN')),
                   frame(row(), row(BS='S'))]
        for value in invalid:
            with self.subTest(value=str(value)), self.assertRaises(TradingError):
                parse_position(value, 'ACC', 'BROKER', C)

    def test_fills_require_full_nonduplicate_reports(self):
        self.assertTrue(filled_quantity(trade(), 1))
        self.assertFalse(filled_quantity(trade(State.Submitted, 0), 1))
        self.assertFalse(filled_quantity(trade(State.PartFilled, 1), 3))
        for value in [trade(State.Failed), trade(State.Cancelled, 0), trade(State.PartFilled_Cancelled),
                      trade(qty=2), trade(failed=True)]:
            with self.assertRaises(TradingError):
                filled_quantity(value, 1)
        duplicate = trade(qty=1)
        duplicate.order_status.deals *= 2
        with self.assertRaises(TradingError):
            filled_quantity(duplicate, 2)

    def test_bridge_sets_explicit_close_open_and_blocks_unscoped_order(self):
        native = Mock()
        bridge = ExplicitPositionBridge(native)
        args = [0, 0, 123, 'BROKER', 'ACC', '', 'TMFJ6', 'S', 1, '0', 1, 3, 3, 0, '', '', '']
        with self.assertRaises(TradingError):
            bridge.Order(*args)
        for phase, pf in [('flatten', 1), ('entry', 0)]:
            bridge.phase = phase
            bridge.Order(*args)
            call = native.Order.call_args.args
            self.assertEqual((call[8], call[10], call[11], call[12], call[13]), (1, 1, 3, pf, 0))

    def broker(self, positions, returned, unit=1):
        config = dict(contract=C, confirmation_timeout=20, entry_quantity=unit)
        b = KgiBroker(config, self.store)
        b.account, b.broker_id = 'ACC', 'BROKER'
        native = NS(FIsConnected=True, FIsLogon=True)
        b.bridge = NS(phase=None)
        b.api = NS(_ObjOrder=native, FutAccount=NS(PositionSum=Mock(side_effect=[frame(*p) for p in positions])),
                   FutOrder=NS(create_order=Mock(return_value=returned), get_trades=Mock(return_value={})))
        b.kgi = NS(Action=NS(Buy='B', Sell='S'), PriceType=NS(MKT='MKT'), TimeInForce=NS(IOC='IOC'))
        return b

    def test_confirmed_flatten_then_entry_uses_two_orders(self):
        # execute_h queries, submit precheck, fill reconciliation, then next phase.
        b = self.broker([[row(OTQty=3)], [row(OTQty=3)], [], [], [], [row(BS='S')]], trade(qty=3))
        b.api.FutOrder.create_order.side_effect = [trade(qty=3), trade(qty=1)]
        with patch('kgi_broker.time.sleep'):
            self.assertEqual(execute_h(b, -1, self.store, 'signal'), -1)
        calls = b.api.FutOrder.create_order.call_args_list
        self.assertEqual([(c.kwargs['action'], c.kwargs['qty']) for c in calls], [('S', 3), ('S', 1)])
        self.assertIsNone(self.store.pending())

    def test_failed_close_retains_guard_and_never_enters(self):
        b = self.broker([[row(OTQty=3)], [row(OTQty=3)]], trade(State.PartFilled_Cancelled, qty=1))
        with self.assertRaises(TradingError):
            execute_h(b, -1, self.store, 'signal')
        self.assertEqual(b.api.FutOrder.create_order.call_count, 1)
        self.assertIsNotNone(self.store.pending())
        with self.assertRaises(TradingError):
            execute_h(b, 1, self.store, 'next')
        self.assertEqual(b.api.FutOrder.create_order.call_count, 1)

    def test_disconnect_and_existing_orders_block_submission(self):
        b = self.broker([], trade())
        b.disconnected.set()
        with self.assertRaises(TradingError):
            b.send_and_confirm(1, 'entry', 1)
        b.disconnected.clear()
        b.api.FutOrder.get_trades.return_value = {'o': trade(State.Submitted, 0)}
        with self.assertRaises(TradingError):
            b.send_and_confirm(1, 'entry', 1)
        b.api.FutOrder.create_order.assert_not_called()

    def test_account_and_environment_state_are_independent(self):
        self.assertNotEqual(account_key('A', 'B', True), account_key('A', 'B', False))
        self.assertNotEqual(account_key('A', 'B', True), account_key('C', 'B', True))
        second = Store(Path(self.temp.name) / 'second.sqlite')
        try:
            self.assertTrue(self.store.consume('same', 1, 100))
            self.assertTrue(second.consume('same', 1, 100))
            self.assertFalse(self.store.consume('same', 1, 120))
            self.assertFalse(self.store.consume('dup', 1, 105))
        finally:
            second.db.close()

    def test_shared_signal_trust_freshness_and_fixed_quantity(self):
        now = datetime.now(timezone.utc)
        event = dict(event='received', route='h', sender_username='taiwan_mxf_bot', chat_id=-1,
                     message_id=1, received_at=now.isoformat(), text='浩克3 訊號通知 (B=0 S=8)')
        self.assertEqual(parse_signal(event, now)[1], -1)
        for changes in [dict(sender_username='fake'), dict(event='sent'), dict(route='ef'),
                        dict(received_at=(now-timedelta(seconds=31)).isoformat()),
                        dict(text='浩克3 訊號通知 (B=1 S=1)')]:
            self.assertIsNone(parse_signal(event | changes, now))

    def test_config_check_does_not_login(self):
        env = dict(KGI_LOGIN_ID='USER', KGI_PASSWORD='PASS', KGI_ACCOUNT='ACC',
                   KGI_BROKER_ID='BROKER', KGI_SIMULATION='true')
        config = dict(contract=C, entry_quantity=1)
        with patch.dict(os.environ, env), patch('kgi_monitor.read_config', return_value=config), \
                patch('kgi_monitor.KgiBroker') as broker:
            main(['--check-config'])
            broker.assert_not_called()

    def test_timeout_keeps_guard_without_retry(self):
        b = self.broker([[], [], []], trade(State.Submitted, 0))
        # First clock sets submission time; second is deadline; third expires it.
        with patch('kgi_broker.time.monotonic', side_effect=[0, 0, 21]), self.assertRaises(TradingError):
            execute_h(b, 1, self.store, 'signal')
        self.assertEqual(b.api.FutOrder.create_order.call_count, 1)
        self.assertIsNotNone(self.store.pending())

    def test_diagnose_only_queries_and_never_watches_signals(self):
        fake = NS(account='ACC', contract=C, login=Mock(), position=Mock(return_value=0), close=Mock(),
                  send_and_confirm=Mock())
        creds = ('USER', 'PASS', 'ACC', 'BROKER', True)
        with patch('kgi_monitor.BASE', Path(self.temp.name)), \
                patch('kgi_monitor.credentials', return_value=creds), \
                patch('kgi_monitor.read_config', return_value={'contract': C}), \
                patch('kgi_monitor.KgiBroker', return_value=fake), patch('kgi_monitor.watch') as watcher:
            main(['--diagnose'])
        fake.login.assert_called_once_with(*creds)
        fake.send_and_confirm.assert_not_called()
        watcher.assert_not_called()
        fake.close.assert_called_once()

    def test_inventory_lag_waits_without_second_submission(self):
        b = self.broker([[], [], [], [row()]], trade())
        with patch('kgi_broker.time.sleep'):
            self.assertEqual(b.send_and_confirm(1, 'entry', 1), 1)
        b.api.FutOrder.create_order.assert_called_once()

    def inventory_retry_clock(self):
        clock = NS(now=0.0)
        def sleep(seconds):
            clock.now += seconds
        return clock, sleep

    def test_inventory_no_response_then_valid_empty_is_zero(self):
        b = self.broker([], trade())
        b.output = Mock()
        b.api.FutAccount.PositionSum.side_effect = [None, frame()]
        clock, sleep = self.inventory_retry_clock()
        with patch('kgi_broker.time.monotonic', lambda: clock.now), patch('kgi_broker.time.sleep', sleep):
            self.assertEqual(b.position(), 0)
        self.assertAlmostEqual(clock.now, 6)
        self.assertEqual(b.api.FutAccount.PositionSum.call_count, 2)
        b.api.FutOrder.create_order.assert_not_called()
        detail = self.store.db.execute("SELECT detail FROM audit WHERE event='kgi_inventory_query_unavailable'").fetchone()[0]
        self.assertEqual(json.loads(detail), {'reason': 'no_response', 'attempt': 1, 'max_attempts': 3})

    def test_inventory_timeout_recovers_actual_position(self):
        b = self.broker([], trade())
        b.output = Mock()
        b.api.FutAccount.PositionSum.side_effect = [TimeoutError('private vendor payload'), frame(row(BS='S', OTQty=3))]
        clock, sleep = self.inventory_retry_clock()
        with patch('kgi_broker.time.monotonic', lambda: clock.now), patch('kgi_broker.time.sleep', sleep):
            self.assertEqual(b.position(), -3)
        detail = self.store.db.execute('SELECT detail FROM audit').fetchone()[0]
        self.assertEqual(json.loads(detail)['reason'], 'timeout')
        self.assertNotIn('private vendor payload', detail)

    def test_persistent_inventory_no_response_stops_before_entry(self):
        b = self.broker([], trade(qty=3), unit=3)
        b.output = Mock()
        b.api.FutAccount.PositionSum.side_effect = [None] * 3
        clock, sleep = self.inventory_retry_clock()
        with patch('kgi_broker.time.monotonic', lambda: clock.now), patch('kgi_broker.time.sleep', sleep), \
                self.assertRaisesRegex(TradingError, '3 次仍無回應'):
            execute_h(b, 1, self.store, 'signal', entry_quantity=3)
        self.assertAlmostEqual(clock.now, 12)
        self.assertEqual(b.api.FutAccount.PositionSum.call_count, 3)
        b.api.FutOrder.create_order.assert_not_called()
        self.assertIsNone(self.store.pending())

    def test_inventory_invalid_response_or_unknown_exception_is_not_retried(self):
        for value in (pd.DataFrame(), frame(row(Account='OTHER')), frame(row(OTQty='bad')),
                      RuntimeError('vendor failure')):
            with self.subTest(value=str(value)):
                b = self.broker([], trade())
                b.api.FutAccount.PositionSum.side_effect = [value]
                with patch('kgi_broker.time.sleep') as sleep, self.assertRaises((TradingError, RuntimeError)):
                    b.position()
                b.api.FutAccount.PositionSum.assert_called_once()
                sleep.assert_not_called()

    def test_inventory_retry_wait_stops_on_disconnect(self):
        b = self.broker([], trade())
        b.output = Mock()
        b.api.FutAccount.PositionSum.side_effect = [None]
        b.check = Mock(side_effect=[None, None, TradingError('斷線')])
        with self.assertRaisesRegex(TradingError, '斷線'):
            b.position()
        b.api.FutAccount.PositionSum.assert_called_once()
        b.api.FutOrder.create_order.assert_not_called()

    def test_fill_inventory_retries_respect_deadline_and_never_resubmit(self):
        b = self.broker([], trade())
        b.output = Mock()
        b.timeout = 5
        b.api.FutAccount.PositionSum.side_effect = [frame(), frame(), frame(), None]
        clock, sleep = self.inventory_retry_clock()
        with patch('kgi_broker.time.monotonic', lambda: clock.now), patch('kgi_broker.time.sleep', sleep), \
                self.assertRaisesRegex(TradingError, '庫存確認逾時'):
            execute_h(b, 1, self.store, 'signal')
        self.assertAlmostEqual(clock.now, 5)
        self.assertEqual(b.api.FutAccount.PositionSum.call_count, 4)
        b.api.FutOrder.create_order.assert_called_once()
        self.assertIsNotNone(self.store.pending())

    def test_blocking_inventory_query_cannot_confirm_after_deadline(self):
        b = self.broker([], trade())
        clock, _sleep = self.inventory_retry_clock()
        def late_response():
            clock.now = 6
            return frame()
        b.api.FutAccount.PositionSum.side_effect = late_response
        with patch('kgi_broker.time.monotonic', lambda: clock.now), \
                self.assertRaisesRegex(TradingError, '庫存確認逾時'):
            b.position(deadline=5)

    def test_inventory_recovery_preserves_unit_and_only_submits_entry_once(self):
        b = self.broker([], trade(qty=3), unit=3)
        b.output = Mock()
        b.api.FutAccount.PositionSum.side_effect = [None, frame(), frame(), frame(), None, frame(row(OTQty=3))]
        clock, sleep = self.inventory_retry_clock()
        with patch('kgi_broker.time.monotonic', lambda: clock.now), patch('kgi_broker.time.sleep', sleep):
            self.assertEqual(execute_h(b, 1, self.store, 'B=1', entry_quantity=3), 3)
        b.api.FutOrder.create_order.assert_called_once()
        self.assertEqual(b.api.FutOrder.create_order.call_args.kwargs['qty'], 3)
        self.assertIsNone(self.store.pending())

    def test_unit_controls_both_directions_independently_of_signal_size(self):
        now = datetime.now(timezone.utc)
        for buy, sell, direction in [(1, 0, 1), (8, 0, 1), (0, 1, -1), (0, 8, -1)]:
            with self.subTest(buy=buy, sell=sell):
                event = dict(event='received', route='h', sender_username='taiwan_mxf_bot', chat_id=-1,
                    message_id=buy * 10 + sell, received_at=now.isoformat(),
                    text=f'浩克3 訊號通知 (B={buy} S={sell})')
                signal = parse_signal(event, now)
                # Actual old inventory is 5, while configured entry UNIT is 3.
                b = self.broker([[row(OTQty=5)], [row(OTQty=5)], [], [], [],
                    [row(BS='B' if direction == 1 else 'S', OTQty=3)]], trade(qty=5), unit=3)
                b.api.FutOrder.create_order.side_effect = [trade(qty=5), trade(qty=3)]
                with patch('kgi_broker.time.sleep'):
                    self.assertEqual(execute_h(b, signal[1], self.store, signal[0], entry_quantity=3), direction * 3)
                calls = b.api.FutOrder.create_order.call_args_list
                self.assertEqual([(c.kwargs['action'], c.kwargs['qty']) for c in calls],
                    [('S', 5), ('B' if direction == 1 else 'S', 3)])
                self.assertIsNone(self.store.pending())

    def test_unit_requires_positive_integer_and_rejects_mismatched_entry(self):
        for value in ('', '0', '-1', '1.5', 'true', 'NaN'):
            with self.subTest(value=value), patch.dict(os.environ, {'KGI_UNIT': value}), self.assertRaises(TradingError):
                read_unit()
        with patch.dict(os.environ, {'KGI_UNIT': '3'}):
            self.assertEqual(read_unit(), 3)
        b = self.broker([], trade(), unit=3)
        with self.assertRaises(TradingError):
            b.send_and_confirm(1, 'entry', 1)
        b.api.FutOrder.create_order.assert_not_called()
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(TradingError):
                execute_h(b, 1, self.store, 'invalid', entry_quantity=invalid)

    def test_env_unit_overrides_legacy_json_entry_quantity(self):
        source = Path(self.temp.name) / 'signals.jsonl'
        source.write_text('', encoding='utf-8')
        config_path = Path(self.temp.name) / 'config.json'
        config_path.write_text(json.dumps(dict(entry_quantity=1, signal_source='signals.jsonl',
            contract=dict(product=C.product, settle_ym=C.settle_ym, symbol=C.symbol))), encoding='utf-8')
        with patch.dict(os.environ, {'KGI_UNIT': '3'}):
            self.assertEqual(read_config(config_path)['entry_quantity'], 3)

    def test_watcher_applies_unit_to_live_orders_and_paper_audit(self):
        for live in (False, True):
            with self.subTest(live=live):
                source = Path(self.temp.name) / f'signals-{live}.jsonl'
                source.write_text('', encoding='utf-8')
                event = dict(event='received', route='h', sender_username='taiwan_mxf_bot', chat_id=-1,
                    message_id=200 + live, received_at=datetime.now(timezone.utc).isoformat(),
                    text='浩克3 訊號通知 (B=0 S=1)')
                class StopWatch(Exception):
                    pass
                polls = 0
                def poll(_seconds):
                    nonlocal polls
                    polls += 1
                    if polls == 1:
                        source.write_text(json.dumps(event) + '\n', encoding='utf-8')
                    else:
                        raise StopWatch()
                class Broker:
                    current = 2
                    calls = []
                    def check(self):
                        pass
                    def position(self):
                        return self.current
                    def send_and_confirm(self, delta, phase, target):
                        self.calls.append((delta, phase, target))
                        self.current = target
                        return target
                b = Broker() if live else None
                # Paper and live services own separate deduplication/guard databases.
                run_store = Store(Path(self.temp.name) / f'watch-{live}.sqlite')
                try:
                    with patch('h_monitor.time.sleep', poll), self.assertRaises(StopWatch):
                        watch(dict(signal_source=source, entry_quantity=3), run_store, b)
                    if live:
                        self.assertEqual(b.calls, [(-2, 'flatten', 0), (-3, 'entry', -3)])
                    else:
                        payload = run_store.db.execute("SELECT detail FROM audit WHERE event='paper_signal' ORDER BY id DESC LIMIT 1").fetchone()[0]
                        self.assertEqual(json.loads(payload)['entry_quantity'], 3)
                finally:
                    run_store.db.close()


class SdkSourceTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('KGI_SDK_SOURCE'), 'set KGI_SDK_SOURCE to an extracted kgisuperpy directory')
    def test_actual_sdk_create_order_registers_trade_and_preserves_callbacks(self):
        # Execute the real pinned SDK's FutOrder.py, without importing its login/native DLLs.
        sdk = Path(os.environ['KGI_SDK_SOURCE'])
        package = types.ModuleType('offline_kgi')
        package.__path__ = [str(sdk)]
        log_package = types.ModuleType('offline_kgi.log')
        log_package.__path__ = []
        logger = types.ModuleType('offline_kgi.log._log')
        logger.new_log = lambda *a, **kw: Mock()
        with patch.dict(sys.modules, {'offline_kgi': package, 'offline_kgi.log': log_package,
                                      'offline_kgi.log._log': logger}):
            from importlib import import_module
            sdk_order = import_module('offline_kgi.trading.FutOrder')
            sdk_base = import_module('offline_kgi.trading._trade_base')
            native = Mock()
            native.RetriveRequestID.return_value = 123
            native.Order.return_value = 0
            bridge = ExplicitPositionBridge(native)
            core = sdk_order.FutOrderAPI(bridge, 'BROKER', 'ACC')
            core._show = True
            events = Mock()
            core.set_event(events)
            for phase, pf in [('flatten', 1), ('entry', 0)]:
                request_id = 123 + pf
                native.RetriveRequestID.return_value = request_id
                bridge.phase = phase
                returned = core.create_order(sdk_base.Action.Sell, 'TMFJ6', 3,
                                             sdk_base.PriceType.MKT, sdk_base.TimeInForce.IOC)
                self.assertIs(core._rid[str(request_id)], returned)
                self.assertEqual(native.Order.call_args.args[12], pf)
                self.assertEqual(native.Order.call_args.args[8:12], (1, '0', 1, 3))
                order_id = f'ORDER{request_id}'
                cnt = f'CNT{request_id}'
                core._OnOrderPending(dict(RequestID=str(request_id), CNT=cnt, OrderNo=order_id,
                                          Error='0', WEBID='WEB'))
                core._OnOrderReport(dict(CNT=cnt, OrderNo=order_id, WEBID='WEB', ErrCode='0',
                    OrderFunc='I', Symbol='TMFJ6', ReportTime='120000000', BS='S', AfterQty='3',
                    PriceFlag='1', Price='0', TimeInForce='I', TradeHour='R', TaiCode='', ErrMsg=''))
                self.assertFalse(filled_quantity(returned, 3))
                core._OnExecReport(dict(Symbol='TMFJ6', BS='S', DealQty='3', DealPrice='100',
                    TradeHour='R', ReportTime='120001', OrderNo=order_id, CNT=cnt, MarketNo=f'FILL{request_id}'))
                self.assertTrue(filled_quantity(returned, 3))
                self.assertIs(core.get_trades(full=True)[order_id], returned)
            self.assertEqual(events.call_count, 6)


if __name__ == '__main__':
    unittest.main()
