import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from shared_h import OrderRejected, Store, TradingError
from kgi_broker import Contract, KgiBroker
from mdd_sizing import MddPolicy, execute_mdd, watch
from test_mdd_sizing import FakeBroker

C = Contract('TMF', '202610', 'TMFJ6')
PAYLOAD = dict(trigger='manual-production-buy-one-v1', phase='entry',
               previous=0, target=1, side='buy', quantity=1)


def rejected_row(**changes):
    row = dict(ActNo='ACC', BrokerID='BROKER', OrderFunc='I', ErrCode='3296',
               ErrMsg='ACC BROKER 保證金不足', CNT='CNT1', OrderNo='cA000',
               Symbol=C.symbol, BS='B', BeforeQty='0', AfterQty='0', TimeInForce='I',
               PriceFlag='1', PositionEffect='O', TradeDate='20261015', ReportTime='233253299')
    return row | changes


def rejected_trade():
    return NS(order=NS(order_id='cA000', symbol=C.symbol, quantity=0),
              order_status=NS(status=None, deals=[]),
              operations=[NS(task=NS(name='NewOrder'), status=NS(name='Failed'), nid='CNT1')])


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'state.sqlite')
        self.policy = MddPolicy(self.store, C.symbol, one_way_cost='0')
        self.broker = KgiBroker(dict(contract=C, confirmation_timeout=1, mdd_sizing=True), self.store, output=Mock())
        self.broker.account, self.broker.broker_id = 'ACC', 'BROKER'
        self.broker.check = Mock()
        self.broker.position = Mock(return_value=0)
        self.broker.bridge = NS(phase=None)
        self.broker.core = NS(order_list=[rejected_row()], deal_list=[])
        self.broker.api = NS(FutOrder=NS(create_order=Mock(return_value=rejected_trade()),
                                        get_trades=Mock(return_value={'無效單': [rejected_trade()]})))
        self.broker.kgi = NS(Action=NS(Buy='B', Sell='S'), PriceType=NS(MKT='MKT'), TimeInForce=NS(IOC='IOC'))
        self.clock = 0.0

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def sleep(self, seconds):
        self.clock += seconds

    def test_full_rejection_resolves_guard_does_not_change_mdd_and_next_signal_can_trade(self):
        before = self.policy.state()
        messages = []
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            with self.assertRaises(OrderRejected):
                execute_mdd(self.broker, self.policy, 'signal-a', 1, messages.append)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.policy.state(), before)
        self.broker.api.FutOrder.create_order.assert_called_once()
        self.assertTrue(any('繼續等待新訊號' in message for message in messages))
        proof = json.loads(self.store.db.execute("SELECT detail FROM audit WHERE event='order_resolved_rejected'").fetchone()[0])
        self.assertEqual(proof['evidence']['cnt'], 'CNT1')
        self.assertNotIn('ACC', proof['evidence']['reason'])
        next_trade = NS(order=NS(order_id='NEXT', symbol=C.symbol), operations=[],
                        order_status=NS(status=NS(name='Filled'),
                            deals=[NS(reportseq='FILL', quantity=1, price=50000, ts='233300')]))
        self.broker.api.FutOrder.create_order.return_value = next_trade
        self.broker.position.side_effect = [0, 0, 0, -1]
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            self.assertEqual(execute_mdd(self.broker, self.policy, 'signal-b', -1), -1)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.broker.api.FutOrder.create_order.call_count, 2)

    def test_rejected_close_keeps_existing_leg_and_never_opens_new_leg(self):
        execute_mdd(FakeBroker([50000]), self.policy, 'initial', 1)
        before = self.policy.state()
        self.broker.position.return_value = 1
        self.broker.core.order_list = [rejected_row(BS='S', PositionEffect='C')]
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            with self.assertRaises(OrderRejected):
                execute_mdd(self.broker, self.policy, 'close-rejected', -1)
        self.assertEqual(self.policy.state(), before)
        self.assertIsNone(self.store.pending())
        self.broker.api.FutOrder.create_order.assert_called_once()

    def test_rejection_after_confirmed_close_retains_realized_loss_and_boost(self):
        execute_mdd(FakeBroker([51500]), self.policy, 'initial', 1)
        closed = NS(order=NS(order_id='CLOSE', symbol=C.symbol), operations=[],
                    order_status=NS(status=NS(name='Filled'),
                        deals=[NS(reportseq='CLOSE-FILL', quantity=1, price=50000, ts='233200')]))
        self.broker.api.FutOrder.create_order.side_effect = [closed, rejected_trade()]
        self.broker.core.order_list = [rejected_row(BS='S')]
        self.broker.position.side_effect = [1, 1, 0, 0, 0, 0]
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            with self.assertRaises(OrderRejected):
                execute_mdd(self.broker, self.policy, 'reverse', -1)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.policy.state()['equity'], '-15000')
        self.assertTrue(self.policy.state()['boosted'])
        self.assertIsNone(self.policy.state()['open_trade'])
        self.assertEqual(self.broker.api.FutOrder.create_order.call_args_list[-1].kwargs['qty'], 2)

    def test_linked_native_rejection_is_terminal_even_if_sdk_status_is_none(self):
        self.broker.api.FutOrder.get_trades.return_value = {'cA000': rejected_trade()}
        self.broker.ensure_no_working_orders()
        self.broker.core.order_list = []
        with self.assertRaises(TradingError):
            self.broker.ensure_no_working_orders()

    def test_ambiguous_failures_keep_guard(self):
        for changes in ({'ErrCode': '0'}, {'ActNo': 'OTHER'}, {'AfterQty': '1'},
                        {'CNT': 'OTHER'}, {'TimeInForce': 'R'}):
            with self.subTest(changes=changes):
                self.broker.core.order_list = [rejected_row(**changes)]
                self.store.begin(PAYLOAD)
                with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
                    with self.assertRaises(TradingError) as error:
                        self.broker.send_and_confirm(1, 'entry', 1)
                self.assertNotIsInstance(error.exception, OrderRejected)
                self.assertEqual(self.store.pending(), PAYLOAD)
                self.store.complete()

    def test_partial_fill_or_changed_inventory_cannot_be_resolved(self):
        trade = rejected_trade()
        trade.order_status.deals = [NS(quantity=1)]
        self.broker.api.FutOrder.create_order.return_value = trade
        self.store.begin(PAYLOAD)
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            with self.assertRaises(TradingError) as error:
                self.broker.send_and_confirm(1, 'entry', 1)
        self.assertNotIsInstance(error.exception, OrderRejected)
        self.assertIsNotNone(self.store.pending())
        self.store.complete()
        self.broker.api.FutOrder.create_order.return_value = rejected_trade()
        self.broker.position.side_effect = [0, 1]
        self.store.begin(PAYLOAD)
        with patch('kgi_broker.time.monotonic', lambda: self.clock), patch('kgi_broker.time.sleep', self.sleep):
            with self.assertRaisesRegex(TradingError, '拒單後庫存不符'):
                self.broker.send_and_confirm(1, 'entry', 1)
        self.assertIsNotNone(self.store.pending())

    def seed_legacy(self):
        self.store.begin(PAYLOAD)
        for at, event, detail in [('2026-10-15T15:32:41+00:00', 'prepared', PAYLOAD),
                                  ('2026-10-15T15:32:53.5+00:00', 'stopped',
                                   dict(stage='manual_production_test', pending_order=True))]:
            self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)', (at, event, json.dumps(detail)))
        self.store.db.execute('CREATE TABLE kgi_manual_production_test (id INTEGER PRIMARY KEY, status TEXT)')
        self.store.db.execute("INSERT INTO kgi_manual_production_test VALUES (1,'attempted')")
        self.store.db.commit()

    def test_legacy_manual_rejection_recovers_only_with_matching_native_replay(self):
        self.seed_legacy()
        self.assertTrue(self.broker.recover_rejected_pending())
        self.assertIsNone(self.store.pending())
        self.broker.api.FutOrder.create_order.assert_not_called()
        self.assertEqual(self.policy.state()['equity'], '0')

    def test_legacy_wrong_time_or_missing_replay_never_unlocks(self):
        self.seed_legacy()
        self.broker.core.order_list = [rejected_row(ReportTime='223253299')]
        with self.assertRaises(TradingError):
            self.broker.recover_rejected_pending()
        self.assertIsNotNone(self.store.pending())
        self.broker.core.order_list = []
        with self.assertRaises(TradingError):
            self.broker.recover_rejected_pending()
        self.assertIsNotNone(self.store.pending())
        self.broker.api.FutOrder.create_order.assert_not_called()

    def test_identity_recovery_requires_zero_related_deals(self):
        self.store.begin(PAYLOAD)
        self.store.audit('kgi_order_identity', dict(pending=PAYLOAD, identities=['CNT1']))
        self.broker.core.deal_list = [dict(CNT='CNT1', OrderNo='cA000')]
        with self.assertRaises(TradingError):
            self.broker.recover_rejected_pending()
        self.assertIsNotNone(self.store.pending())
        self.broker.core.deal_list = []
        self.assertTrue(self.broker.recover_rejected_pending())
        self.assertIsNone(self.store.pending())


class InventoryPacingTests(unittest.TestCase):
    def test_successful_inventory_queries_are_spaced_six_seconds(self):
        b = KgiBroker(dict(contract=C, confirmation_timeout=20), Mock())
        b.account, b.broker_id = 'ACC', 'BROKER'
        b.check = Mock()
        from test_kgi_execution import frame, row
        clock = [0.0]
        starts = []
        def query():
            starts.append(clock[0])
            return frame() if len(starts) == 1 else frame(row(BS='S'))
        b.api = NS(FutAccount=NS(PositionSum=Mock(side_effect=query)))
        with patch('kgi_broker.time.monotonic', lambda: clock[0]), \
                patch('kgi_broker.time.sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            self.assertEqual(b.position(), 0)
            self.assertEqual(b.position(), -1)
        self.assertEqual(starts, [0, 6])


class NormalWatcherTests(unittest.TestCase):
    def test_rejection_keeps_watcher_running_and_does_not_retry_same_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'signals.jsonl'
            source.write_text('', encoding='utf-8')
            store = Store(Path(directory)/'state.sqlite')
            broker = FakeBroker([50000])
            original = broker.send_and_confirm
            calls = []
            def send(delta, phase, target):
                calls.append((delta, phase, target))
                if len(calls) == 1:
                    raise OrderRejected('整筆拒單', dict(error_code='3296', position=0, zero_fills=True))
                return original(delta, phase, target)
            broker.send_and_confirm = send
            events = [dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                          chat_id=-1, message_id=i+1, received_at=datetime.now(timezone.utc).isoformat(),
                          text=f'浩克3 訊號通知 (B={1-i} S={i})') for i in range(2)]
            polls = [0]
            class Done(Exception): pass
            def poll(_):
                if polls[0] == 2: raise Done()
                with source.open('a', encoding='utf-8') as handle:
                    for _ in range(2): handle.write(json.dumps(events[polls[0]])+'\n')
                polls[0] += 1
            try:
                with patch('mdd_sizing.time.sleep', poll), patch('mdd_sizing.Discord', return_value=Mock()), self.assertRaises(Done):
                    watch(dict(signal_source=source, contract=C, mdd_one_way_cost='0', notification_url='unused'), store, broker)
                self.assertEqual(calls, [(1, 'entry', 1), (-1, 'entry', -1)])
                self.assertIsNone(store.pending())
                self.assertEqual(MddPolicy(store, C.symbol, one_way_cost='0').state()['open_trade']['direction'], -1)
            finally:
                store.db.close()


if __name__ == '__main__': unittest.main()
