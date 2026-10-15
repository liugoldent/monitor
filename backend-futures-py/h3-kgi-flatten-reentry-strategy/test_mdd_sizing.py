import json
import tempfile
import unittest
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from shared_h import Store, TradingError
from mdd_sizing import Discord, MddPolicy, execute_mdd, execution_fill, read_webhook, watch


class FakeBroker:
    def __init__(self, prices):
        self.prices = iter(prices)
        self.current = 0
        self.calls = []
        self.entry_quantity = 1
        self.last_fill = None
        self.fail_phase = None

    def position(self):
        return self.current

    def check(self):
        pass

    def send_and_confirm(self, delta, phase, target):
        self.last_fill = None
        self.calls.append((delta, phase, target))
        if phase == self.fail_phase:
            raise TradingError('未確認完整成交')
        if phase == 'entry':
            assert abs(delta) == self.entry_quantity
        self.last_fill = dict(id=f'fill-{len(self.calls)}', symbol='TMFJ6',
                              quantity=abs(delta), price=str(next(self.prices)))
        self.current = target
        return target


class MddTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name)/'state.sqlite'
        self.store = Store(self.db_path)
        self.policy = MddPolicy(self.store, 'TMFJ6', one_way_cost='0')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def run_signal(self, broker, key, direction):
        return execute_mdd(broker, self.policy, key, direction)

    def test_start_immediately_one_threshold_then_sticky_two_then_recovery_one(self):
        b = FakeBroker([51500, 50000, 50000, 49600, 49600, 49950, 49950])
        self.assertEqual(self.run_signal(b, 'a', 1), 1)
        self.assertEqual(self.run_signal(b, 'b', -1), -2)
        s = self.policy.state()
        self.assertEqual((s['equity'], s['peak'], s['last_close']['ratio']), ('-15000', '0', '0.03'))
        self.assertEqual(self.run_signal(b, 'c', 1), 2)
        self.assertEqual(Decimal(self.policy.state()['equity']), -7000)
        self.assertLess(Decimal(self.policy.state()['last_close']['ratio']), Decimal('.03'))
        self.assertEqual(self.run_signal(b, 'd', -1), -1)
        s = self.policy.state()
        self.assertEqual((s['equity'],s['peak'],s['boosted']), ('0','0',False))
        self.assertEqual(b.calls, [(1,'entry',1),(-1,'flatten',0),(-2,'entry',-2),
                                  (2,'flatten',0),(2,'entry',2),(-2,'flatten',0),(-1,'entry',-1)])
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM kgi_mdd_fills').fetchone()[0],7)

    def test_below_threshold_stays_one_and_same_direction_still_flattens(self):
        b = FakeBroker([51499, 50000, 50000])
        self.run_signal(b,'a',1)
        self.assertEqual(self.run_signal(b,'b',1),1)
        self.assertFalse(self.policy.state()['boosted'])
        self.assertEqual(b.calls[-2:],[(-1,'flatten',0),(1,'entry',1)])

    def test_costs_scale_with_closed_quantity_and_only_realize_at_close(self):
        self.store.db.close()
        self.store = Store(Path(self.temp.name)/'cost.sqlite')
        self.policy = MddPolicy(self.store,'TMFJ6',one_way_cost='24')
        b=FakeBroker([51500,50000,50000,50000,50000])
        self.run_signal(b,'a',1)
        self.assertEqual(self.policy.state()['equity'],'0')
        self.run_signal(b,'b',-1)
        self.assertEqual(self.policy.state()['equity'],'-15048')
        self.run_signal(b,'c',1)
        self.assertEqual(self.policy.state()['equity'],'-15144')
        self.assertEqual(self.policy.state()['last_close']['costs'],'96')
        self.assertEqual(self.policy.quantity(),2)

    def test_new_positive_high_water_mark_and_return_to_that_peak(self):
        b=FakeBroker([50000,51000,51000,53000,53000,54010,54010])
        self.run_signal(b,'a',1)
        self.run_signal(b,'b',-1)
        self.assertEqual(self.policy.state()['peak'],'10000')
        self.run_signal(b,'c',1)
        self.assertEqual(self.policy.quantity(),2)
        self.run_signal(b,'d',1)
        self.assertEqual((self.policy.state()['equity'],self.policy.state()['peak']),('10200','10200'))
        self.assertEqual(self.policy.quantity(),1)

    def test_notifications_identify_boost_and_recovery_after_orders_finish(self):
        b=FakeBroker([51500,50000,50000,49250,49250])
        messages=[]
        def notify(message):
            self.assertEqual(b.calls[-1][1],'entry')
            messages.append(message)
        execute_mdd(b,self.policy,'a',1,notify)
        execute_mdd(b,self.policy,'b',-1,notify)
        execute_mdd(b,self.policy,'c',1,notify)
        self.assertTrue(any('達3%｜下一筆加至2口' in x for x in messages))
        self.assertTrue(any('回撤歸零｜下一筆恢復1口' in x for x in messages))

    def test_further_losses_never_increase_above_two(self):
        b=FakeBroker([51500,50000,50000,51000,51000])
        self.run_signal(b,'a',1);self.run_signal(b,'b',-1);self.run_signal(b,'c',-1)
        self.assertEqual(self.policy.quantity(),2)
        self.assertEqual(self.policy.state()['equity'],'-35000')
        self.assertEqual(b.calls[-1],(-2,'entry',-2))

    def test_restart_preserves_entry_cost_peak_and_boost(self):
        b=FakeBroker([51500,50000,50000,49500,49500])
        self.run_signal(b,'a',1);self.run_signal(b,'b',-1)
        before=self.policy.state()
        self.store.db.close();self.store=Store(self.db_path)
        self.policy=MddPolicy(self.store,'TMFJ6',one_way_cost='0')
        self.assertEqual(self.policy.state(),before)
        self.policy.validate_position(-2)
        self.run_signal(b,'c',1)
        self.assertEqual(self.policy.state()['equity'],'-5000')
        self.assertEqual(self.policy.quantity(),2)

    def test_unknown_inventory_or_contract_is_never_adopted(self):
        b=FakeBroker([]);b.current=1
        with self.assertRaisesRegex(TradingError,'庫存'):
            self.run_signal(b,'a',1)
        self.assertEqual(b.calls,[])
        b=FakeBroker([50000]);self.run_signal(b,'a',1)
        with self.assertRaises(TradingError):
            MddPolicy(self.store,'TMFK6',one_way_cost='0')
        with self.assertRaises(TradingError):
            MddPolicy(self.store,'TMFJ6',one_way_cost='24')

    def test_partial_flatten_blocks_new_entry_and_preserves_unsettled_state(self):
        b=FakeBroker([51500]);self.run_signal(b,'a',1)
        before=self.policy.state();b.fail_phase='flatten'
        with self.assertRaises(TradingError):self.run_signal(b,'b',-1)
        self.assertEqual(self.policy.state(),before)
        self.assertEqual(self.store.pending()['phase'],'flatten')
        with self.assertRaises(TradingError):self.run_signal(b,'c',-1)
        self.assertEqual(len(b.calls),2)

    def test_failed_reentry_retains_booked_close_and_new_stage_without_retry(self):
        b=FakeBroker([51500,50000]);self.run_signal(b,'a',1);b.fail_phase='entry'
        with self.assertRaises(TradingError):self.run_signal(b,'b',-1)
        self.assertEqual(self.policy.state()['equity'],'-15000')
        self.assertEqual(self.policy.quantity(),2)
        self.assertIsNone(self.policy.state()['open_trade'])
        self.assertEqual(self.store.pending()['phase'],'entry')
        self.assertEqual(b.calls[-1],(-2,'entry',-2))
        with self.assertRaises(TradingError):self.run_signal(b,'c',-1)
        self.assertEqual(len(b.calls),3)

    def test_duplicate_fill_does_not_mutate_equity_or_clear_guard(self):
        b=FakeBroker([50000]);self.run_signal(b,'a',1)
        before=self.policy.state()
        payload=dict(trigger='b',phase='flatten',previous=1,target=0,side='sell',quantity=1)
        self.store.begin(payload)
        with self.assertRaisesRegex(TradingError,'已計入'):
            self.policy.confirmed(payload,b.last_fill)
        self.assertEqual(self.policy.state(),before)
        self.assertEqual(self.store.pending(),payload)

    def test_accounting_failure_rolls_back_and_retains_order_guard(self):
        payload=dict(trigger='a',phase='entry',previous=0,target=1,side='buy',quantity=1)
        self.store.begin(payload)
        with self.assertRaises(TradingError):
            self.policy.confirmed(payload,dict(id='bad',symbol='OTHER',quantity=1,price='100'))
        self.assertIsNone(self.policy.state()['open_trade'])
        self.assertEqual(self.store.pending(),payload)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM kgi_mdd_fills').fetchone()[0],0)

    def test_real_sdk_deal_price_weighting_and_missing_data(self):
        t=NS(order=NS(order_id='ORDER',symbol='TMFJ6'),order_status=NS(deals=[
            NS(reportseq='2',quantity=1,price=50002,ts='120002'),
            NS(reportseq='1',quantity=1,price=50000,ts='120001')]))
        fill=execution_fill(t,2,'TMFJ6')
        self.assertEqual(fill['price'],'50001')
        t.order_status.deals.reverse()
        self.assertEqual(execution_fill(t,2,'TMFJ6')['id'],fill['id'])
        for key,value in [('price',None),('price','NaN'),('price',0),('ts',None),('quantity',0),('reportseq','2')]:
            with self.subTest(key=key,value=value):
                old=getattr(t.order_status.deals[0],key)
                setattr(t.order_status.deals[0],key,value)
                with self.assertRaises(TradingError):execution_fill(t,2,'TMFJ6')
                setattr(t.order_status.deals[0],key,old)

    def test_discord_uses_selected_key_and_sanitizes_failures(self):
        path=Path(self.temp.name)/'.env'
        hook='https://discord.com/api/webhooks/123/token'
        path.write_text('OTHER_SECRET=do-not-load\nDISCORD_KGI_H3_MDD_ADD_URL="'+hook+'"\n')
        self.assertEqual(read_webhook(path),hook)
        with patch('mdd_sizing.urllib.request.urlopen',side_effect=RuntimeError(hook)) as post:
            Discord(hook,self.store)('測試通知')
        self.assertEqual(post.call_count,1)
        request=post.call_args.args[0]
        self.assertEqual(request.full_url,hook)
        self.assertEqual(json.loads(request.data)['allowed_mentions'],{'parse':[]})
        audit=self.store.db.execute("SELECT detail FROM audit WHERE event='kgi_discord_failed'").fetchone()[0]
        self.assertNotIn('token',audit)

    def test_paper_watcher_skips_history_rejects_stale_and_deduplicates(self):
        source=Path(self.temp.name)/'signals.jsonl'
        event=dict(event='received',route='h',sender_username='taiwan_mxf_bot',chat_id=-1,
                   message_id=100,received_at=datetime.now(timezone.utc).isoformat(),
                   text='浩克3 訊號通知 (B=0 S=8)')
        source.write_text(json.dumps(event)+'\n',encoding='utf-8')
        class StopWatch(Exception):pass
        polls=0
        def poll(_seconds):
            nonlocal polls
            polls+=1
            if polls==1:
                fresh=event | {'message_id':101}
                stale=event | {'message_id':102,'received_at':'2000-01-01T00:00:00+08:00'}
                with source.open('a',encoding='utf-8') as f:
                    for item in (fresh,fresh,stale):f.write(json.dumps(item)+'\n')
            else:raise StopWatch()
        config=dict(signal_source=source,contract=NS(symbol='TMFJ6'),mdd_one_way_cost='0')
        with patch('mdd_sizing.time.sleep',poll),patch('mdd_sizing.urllib.request.urlopen') as post:
            with self.assertRaises(StopWatch):watch(config,self.store)
            post.assert_not_called()
        rows=self.store.db.execute("SELECT detail FROM audit WHERE event='paper_signal'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0][0])['key'],'-1:101')
        self.assertEqual(self.policy.state()['equity'],'0')


if __name__=='__main__':unittest.main()
