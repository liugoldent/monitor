import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from h_core import Contract, OrderRejected, Store, TradingError, execute_h
from h_monitor import main, watch
from capital_broker import CapitalBroker
from capital_operations import manual_buy_one, review_pending, test_buy_one_with_review as run_test_buy_one

C = Contract('TM2610', 'FITM', '202610', 'TM2610', 'TM')
PENDING = dict(trigger='old-signal', phase='entry', previous=0, target=1, side='buy', quantity=1)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'state.sqlite')
        self.broker = NS(account='TEST2899', contract=C, replay_complete=threading.Event(),
                         check_outstanding=Mock(), position=Mock(return_value=0),
                         redact=lambda text: text.replace('TEST2899', '[redacted]'),
                         send_and_confirm=Mock(return_value=1))
        self.broker.replay_complete.set()
        self.confirm = Mock()
        self.output = Mock()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def seed_pending(self):
        self.store.begin(PENDING)
        self.store.audit('prepared', PENDING)
        now = datetime.now(timezone(timedelta(hours=8)))
        row = dict(MarketType='TF', Type='N', OrderErr='Y', OrderNo='00000', Qty='1',
                   YearMonth1='202610', ComId='TM2610', ComId1='FITM', BuySell='BNI10',
                   SeqNo='TEST-SEQUENCE', ErrorMsg='[060] 保證金不足',
                   Date=now.strftime('%Y%m%d'), Time=now.strftime('%H:%M:%S'))
        self.evidence = dict(mode='read_only', orders_sent=0, report_account_exact_match=True,
                             account_suffix='2899', replay_complete=True, reports=[row])
        self.path = Path(self.temp.name)/'evidence.json'
        self.save_evidence()

    def save_evidence(self):
        self.path.write_text(json.dumps(self.evidence), encoding='utf-8')

    def review(self):
        return review_pending(self.broker, self.store, self.path, self.confirm, self.output)

    def test_review_requires_human_and_does_not_send_or_erase_history(self):
        self.seed_pending()
        self.confirm.return_value = 'RESOLVE REJECTED 2899 TEST-SEQUENCE'
        self.assertTrue(self.review())
        self.assertIsNone(self.store.pending())
        self.broker.send_and_confirm.assert_not_called()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM audit WHERE event='prepared'").fetchone()[0], 1)
        proof = json.loads(self.store.db.execute("SELECT detail FROM audit WHERE event='order_resolved_rejected'").fetchone()[0])
        self.assertTrue(proof['evidence']['operator_confirmed'])
        self.assertEqual(proof['evidence']['current_position'], 0)

    def test_review_cancellation_keeps_guard(self):
        self.seed_pending()
        self.confirm.return_value = 'yes'
        with self.assertRaises(TradingError): self.review()
        self.assertEqual(self.store.pending(), PENDING)
        self.broker.send_and_confirm.assert_not_called()

    def test_wrong_evidence_cannot_unlock_guard(self):
        self.seed_pending()
        original = json.loads(json.dumps(self.evidence))
        changes = [('account_suffix', '9999'), ('report_account_exact_match', False),
                   ('replay_complete', False), ('orders_sent', 1)]
        for key, value in changes:
            with self.subTest(key=key):
                self.evidence = json.loads(json.dumps(original))
                self.evidence[key] = value
                self.save_evidence()
                with self.assertRaises(TradingError): self.review()
        for key, value in [('Date', '20000101'), ('ComId', 'OTHER'), ('Qty', '2'),
                           ('OrderErr', 'N'), ('OrderNo', '12345'), ('Type', 'D')]:
            with self.subTest(key=key):
                self.evidence = json.loads(json.dumps(original))
                self.evidence['reports'][0][key] = value
                self.save_evidence()
                with self.assertRaises(TradingError): self.review()
        self.assertEqual(self.store.pending(), PENDING)
        self.confirm.assert_not_called()
        self.broker.send_and_confirm.assert_not_called()

    def test_nonzero_or_changed_inventory_keeps_guard(self):
        self.seed_pending()
        self.broker.position.return_value = 1
        with self.assertRaises(TradingError): self.review()
        self.confirm.assert_not_called()
        self.broker.position.side_effect = [0, 1]
        self.confirm.return_value = 'RESOLVE REJECTED 2899 TEST-SEQUENCE'
        with self.assertRaises(TradingError): self.review()
        self.assertEqual(self.store.pending(), PENDING)

    def test_manual_order_requires_exact_human_confirmation_and_is_once(self):
        self.confirm.return_value = 'no'
        with self.assertRaises(TradingError):
            manual_buy_one(self.broker, self.store, self.confirm, self.output)
        self.broker.send_and_confirm.assert_not_called()
        self.confirm.return_value = 'BUY 1 TM2610 MKT IOC 2899'
        self.assertEqual(manual_buy_one(self.broker, self.store, self.confirm, self.output), 1)
        self.broker.send_and_confirm.assert_called_once_with(1, 'entry', 1)
        with self.assertRaises(TradingError):
            manual_buy_one(self.broker, self.store, self.confirm, self.output)
        self.assertEqual(self.broker.send_and_confirm.call_count, 1)

    def test_failed_manual_order_cannot_be_repeated(self):
        self.confirm.return_value = 'BUY 1 TM2610 MKT IOC 2899'
        self.broker.send_and_confirm.side_effect = TradingError('unknown outcome')
        with self.assertRaises(TradingError):
            manual_buy_one(self.broker, self.store, self.confirm, self.output)
        with self.assertRaises(TradingError):
            manual_buy_one(self.broker, self.store, self.confirm, self.output)
        self.broker.send_and_confirm.assert_called_once()
        self.assertIsNotNone(self.store.pending())

    def test_combined_test_reviews_legacy_rejection_then_prompts_for_new_order(self):
        self.seed_pending()
        self.confirm.side_effect = ['RESOLVE REJECTED 2899 TEST-SEQUENCE', 'BUY 1 TM2610 MKT IOC 2899']
        self.assertEqual(run_test_buy_one(self.broker, self.store, self.path, self.confirm, self.output), 1)
        self.assertEqual(self.confirm.call_count, 2)
        self.assertIn('RESOLVE REJECTED', self.confirm.call_args_list[0].args[0])
        self.assertIn('BUY 1 TM2610', self.confirm.call_args_list[1].args[0])
        self.broker.send_and_confirm.assert_called_once_with(1, 'entry', 1)
        self.assertIsNone(self.store.pending())

    def test_combined_test_cancelling_either_prompt_never_sends(self):
        self.seed_pending()
        self.confirm.return_value = ''
        with self.assertRaises(TradingError):
            run_test_buy_one(self.broker, self.store, self.path, self.confirm, self.output)
        self.assertEqual(self.store.pending(), PENDING)
        self.broker.send_and_confirm.assert_not_called()
        self.confirm.side_effect = ['RESOLVE REJECTED 2899 TEST-SEQUENCE', '']
        with self.assertRaises(TradingError):
            run_test_buy_one(self.broker, self.store, self.path, self.confirm, self.output)
        self.assertIsNone(self.store.pending())
        self.broker.send_and_confirm.assert_not_called()

    def test_combined_test_missing_evidence_preserves_old_guard_and_never_prompts_order(self):
        self.seed_pending()
        with self.assertRaisesRegex(TradingError, '找不到拒單證據'):
            run_test_buy_one(self.broker, self.store, Path(self.temp.name)/'missing.json', self.confirm, self.output)
        self.confirm.assert_not_called()
        self.broker.send_and_confirm.assert_not_called()
        self.assertEqual(self.store.pending(), PENDING)

    def test_combined_test_without_pending_order_asks_only_order_confirmation(self):
        self.confirm.return_value = 'BUY 1 TM2610 MKT IOC 2899'
        self.assertEqual(run_test_buy_one(self.broker, self.store, 'unused.json', self.confirm, self.output), 1)
        self.confirm.assert_called_once()
        self.broker.send_and_confirm.assert_called_once()

    def test_cli_combined_test_logs_in_readonly_then_runs_operator_workflow_and_exits(self):
        self.broker.login = Mock()
        self.broker.close = Mock()
        with patch('h_monitor.BASE', Path(self.temp.name)), \
                patch('h_monitor.sys.stdin.isatty', return_value=True), \
                patch('h_monitor.load_env'), \
                patch('h_monitor.read_config', return_value={'contract': C, 'account': 'TEST2899'}), \
                patch('h_monitor.selected_account', return_value='TEST2899'), \
                patch.dict('os.environ', {'CAPITAL_LOGIN_ID': 'USER', 'CAPITAL_PASSWORD': 'PASSWORD'}), \
                patch('capital_broker.CapitalBroker', return_value=self.broker), \
                patch('capital_operations.test_buy_one_with_review') as workflow, \
                patch('h_monitor.watch') as watcher, patch('builtins.input') as human:
            main(['--test-buy-one'])
        self.assertTrue(self.broker.login.call_args.kwargs['diagnose'])
        workflow.assert_called_once()
        self.assertEqual(workflow.call_args.args[2], Path(self.temp.name)/'runtime/read-only-order-check-20261012.json')
        self.assertIs(workflow.call_args.args[3], human)
        watcher.assert_not_called()
        self.broker.send_and_confirm.assert_not_called()
        self.broker.close.assert_called_once()

    def test_operator_modes_require_terminal_before_configuration_or_login(self):
        for args in (['--test-buy-one'], ['--review-pending', '--rejection-evidence', 'proof.json']):
            with self.subTest(args=args), patch('h_monitor.sys.stdin.isatty', return_value=False), patch('h_monitor.load_env') as load:
                with self.assertRaises(TradingError): main(args)
                load.assert_not_called()

    def test_watcher_continues_after_verified_rejection_without_retrying_signal(self):
        source = Path(self.temp.name)/'signals.jsonl'
        source.write_text('', encoding='utf-8')
        now = datetime.now(timezone.utc).isoformat()
        events = [dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                       chat_id=-1, message_id=i+1, received_at=now,
                       text=f'浩克3 訊號通知 (B={1-i} S={i})') for i in range(2)]
        current = [0]
        calls = []
        def send(delta, phase, target):
            calls.append((delta, phase, target))
            if len(calls) == 1:
                raise OrderRejected('[060] 已確認拒單', dict(key='KEY', position=0))
            current[0] = target
            return target
        broker = NS(check=Mock(), position=lambda: current[0], send_and_confirm=send)
        polls = [0]
        class Done(Exception): pass
        def poll(_):
            if polls[0] == 2: raise Done()
            with source.open('a', encoding='utf-8') as f:
                # Duplicate delivery must not turn a rejection into a retry.
                f.write(json.dumps(events[polls[0]])+'\n')
                f.write(json.dumps(events[polls[0]])+'\n')
            polls[0] += 1
        with patch('h_monitor.time.sleep', poll), self.assertRaises(Done):
            watch(dict(signal_source=source), self.store, broker)
        self.assertEqual(calls, [(1, 'entry', 1), (-1, 'entry', -1)])
        self.assertIsNone(self.store.pending())


class LoginTests(unittest.TestCase):
    def test_pending_guard_still_allows_fresh_queries_but_blocks_live_orders(self):
        for readonly in (True, False):
            with self.subTest(readonly=readonly), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory)/'state.sqlite')
                store.begin(PENDING)
                try:
                    with patch('capital_broker.SK') as sdk:
                        broker = CapitalBroker(dict(account='ACC', contract=C), store, output=Mock())
                        broker.position = Mock(return_value=0)
                        broker.validate_contract = Mock()
                        broker.check_outstanding = Mock()
                        sdk.Login.return_value = NS(Code=0, TFAccounts=[NS(FullAccount='ACC')])
                        def connect(*_):
                            {0: broker.reply_ready, 1: broker.quote_ready, 4: broker.proxy_ready}[broker.waiting_target].set()
                            broker.replay_complete.set()
                            return 0
                        sdk.ManageServerConnection.side_effect = connect
                        if readonly:
                            broker.login('USER', 'PASSWORD', diagnose=True)
                        else:
                            with self.assertRaisesRegex(TradingError, '--review-pending'):
                                broker.login('USER', 'PASSWORD')
                        broker.position.assert_called_once()
                        sdk.SendFutureProxyOrder.assert_not_called()
                        self.assertEqual(store.pending(), PENDING)
                finally:
                    store.db.close()


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.broker = object.__new__(CapitalBroker)
        self.broker.account, self.broker.login_id, self.broker.contract = 'ACC', 'USER', C
        self.broker.check = Mock()
        self.broker.store = Mock()
        self.broker.output = Mock()
        self.clock = 0.0

    def sleep(self, seconds):
        self.clock += seconds

    def test_successful_queries_are_spaced_and_never_reuse_cached_inventory(self):
        first = NS(StatusCode=1, RawData='001,查無資料,ACC')
        second = NS(StatusCode=0, RawData='TF,ACC,TM2610,S,1,0,100,0,0,USER#')
        starts = []
        def query(*_):
            starts.append(self.clock)
            return first if len(starts) == 1 else second
        with patch('capital_broker.time.monotonic', lambda: self.clock), \
                patch('capital_broker.time.sleep', self.sleep), \
                patch('capital_broker.SK.GetOpenInterestGW', side_effect=query):
            self.assertEqual(self.broker.position(), 0)
            self.assertEqual(self.broker.position(), -1)
        self.assertEqual(starts, [0, 6])

    def test_deadline_stops_before_query_if_spacing_would_overrun_it(self):
        self.broker._next_inventory_query = 6
        with patch('capital_broker.time.monotonic', lambda: self.clock), \
                patch('capital_broker.time.sleep', self.sleep), \
                patch('capital_broker.SK.GetOpenInterestGW') as query, self.assertRaises(TradingError):
            self.broker.position(deadline=5)
        query.assert_not_called()

    def test_late_sdk_response_cannot_confirm_inventory(self):
        def query(*_):
            self.clock = 6
            return NS(StatusCode=1, RawData='001,查無資料,ACC')
        with patch('capital_broker.time.monotonic', lambda: self.clock), \
                patch('capital_broker.SK.GetOpenInterestGW', side_effect=query), self.assertRaises(TradingError):
            self.broker.position(deadline=5)


if __name__ == '__main__': unittest.main()
