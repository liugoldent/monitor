"""Offline sizing, confirmed fills, restart, rollback and expiry coverage."""
from datetime import datetime
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_protection as support
from test_protection import event
from recovery_sizing import RecoverySizing
from protection_policy import number
from protection_rollover import ProtectionRollover
from runtime import LedgerError
from h_core import TradingError


class PolicyTests(unittest.TestCase):
    def test_six_losses_add_exactly_one_and_stay_until_recovery(self):
        s=RecoverySizing()
        for i in range(8):
            q=s.quantity(3)
            self.assertEqual(q, 3 if i<6 else 4)
            s=s.close('-100',q)
        self.assertEqual(s.loss_streak,8)
        # A winning trade breaks the streak but does not end the boosted phase.
        s=s.close('10',4)
        self.assertEqual(s.loss_streak,0)
        self.assertEqual(s.quantity(3),4)
        gross=(number(s.peak_points)-number(s.equity_points))/4+number('4.8')
        s=s.close(gross,4)
        self.assertEqual(number(s.equity_points),number(s.peak_points))
        self.assertFalse(s.boosted)
        self.assertEqual(s.quantity(3),3)

    def test_zero_net_breaks_streak_and_small_gross_profit_can_be_net_loss(self):
        s=RecoverySizing().close('1',1)
        self.assertEqual(s.loss_streak,1)
        s=s.close('4.8',1)
        self.assertEqual(s.loss_streak,0)

    def test_recovery_uses_previous_profit_peak_not_initial_zero(self):
        s=RecoverySizing().close('1004.8',1)
        for _ in range(6): s=s.close('-95.2',1)
        self.assertEqual(number(s.equity_points),400)
        self.assertEqual(number(s.peak_points),1000)
        self.assertTrue(s.boosted)
        s=s.close('304.8',1)
        self.assertTrue(s.boosted)

    def test_roll_cost_does_not_count_as_separate_loss(self):
        s=RecoverySizing().rolled().rolled()
        self.assertEqual(s.closed_trades,0)
        self.assertEqual(s.loss_streak,0)
        s=s.close('20',3)
        self.assertEqual(number(s.equity_points),number('16.8'))
        self.assertEqual(s.roll_cost_points,'0')

    def test_invalid_state_rejected(self):
        for s in [RecoverySizing(equity_points='NaN'),RecoverySizing(boosted=True),
                  RecoverySizing(loss_streak=6,closed_trades=6),
                  RecoverySizing(roll_cost_points='-1'),RecoverySizing(cost_points_roundtrip='0')]:
            with self.assertRaises(ValueError):s.validate()


class ExecutionTests(unittest.TestCase):
    setUp=support.RuntimeTests.setUp
    restart=support.RuntimeTests.restart
    api_class=support.AutoAPI

    def lose_rounds(self,n,first_id=1):
        if not self.api.current:self.engine.on_signal(event(1,first_id))
        for i in range(n):
            side=1 if self.api.current>0 else -1
            self.api.price-=100*side
            self.engine.on_signal(event(-side,first_id+i+1))

    def test_unit_three_becomes_four_on_seventh_entry_and_never_five(self):
        with patch.dict(os.environ,H_UNIT_KEY2='3'):
            self.restart()
            self.lose_rounds(5)
            self.assertEqual(abs(self.api.current),3)
            self.assertEqual(self.engine.sizing().loss_streak,5)
            self.restart()
            self.lose_rounds(1,20)
            self.assertEqual(abs(self.api.current),4)
            self.assertTrue(self.engine.sizing().boosted)
            self.restart()
            self.engine.on_signal(event(1 if self.api.current>0 else -1,30))
            self.assertEqual(abs(self.api.current),4)
            self.lose_rounds(2,40)
            self.assertEqual(abs(self.api.current),4)
            side=1 if self.api.current>0 else -1
            self.api.price+=1000*side
            self.engine.on_signal(event(-side,50))
            self.assertEqual(abs(self.api.current),3)
            self.assertFalse(self.engine.sizing().boosted)

    def test_missing_close_fills_keep_guard_and_do_not_count_loss(self):
        self.engine.on_signal(event(1,1))
        self.api.missing_fills=True
        with self.assertRaises(TradingError): self.engine.on_signal(event(-1,2))
        self.assertEqual(self.engine.sizing().closed_trades,0)
        self.assertTrue(self.store.pending())
        self.assertEqual(len(self.api.orders),2)

    def test_partial_close_does_not_count_loss_or_open_reverse(self):
        with patch.dict(os.environ,H_UNIT_KEY2='3'):
            self.restart();self.engine.on_signal(event(1,1))
            self.api.partial=True
            with self.assertRaises(Exception):self.engine.on_signal(event(-1,2))
            self.assertEqual(self.engine.sizing().closed_trades,0)
            self.assertTrue(self.store.pending())
            self.assertEqual(len(self.api.orders),2)

    def test_sizing_commit_failure_rolls_back_all_ledgers(self):
        self.engine.on_signal(event(1,1))
        self.api.price-=100
        before=self.engine.sizing()
        with patch.object(self.engine,'save_sizing',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):self.engine.on_signal(event(-1,2))
        self.assertEqual(self.engine.sizing(),before)
        self.assertEqual(self.engine.state().expected,1)
        self.assertTrue(self.store.pending())
        self.restart()
        with self.assertRaises(TradingError):self.engine.on_signal(event(-1,3))
        self.assertEqual(len(self.api.orders),2)

    def test_deleted_or_corrupt_sizing_never_resets_silently(self):
        with self.store.db:self.store.db.execute('DELETE FROM h3_profit_sizing')
        with self.assertRaises(LedgerError):self.restart()
        with self.store.db:
            self.store.db.execute('INSERT INTO h3_profit_sizing VALUES (1,?)',('{"equity_points":"NaN"}',))
        with self.assertRaises(LedgerError):self.restart()

    def test_weighted_entry_and_exit_prices_use_all_contracts(self):
        original=self.api.place_order
        def fill(*args,**kwargs):
            t=original(*args,**kwargs)
            ts=t.status.deals[0]['ts']
            t.status.deals=[dict(price=self.api.price,quantity=1,ts=ts),
                            dict(price=self.api.price+3,quantity=2,ts=ts)]
            return t
        with patch.dict(os.environ,H_UNIT_KEY2='3'),patch.object(self.api,'place_order',side_effect=fill):
            self.restart();self.engine.on_signal(event(1,1))
            self.assertEqual(number(self.engine.state().first_price),22002)
            self.api.price=22100
            self.engine.on_signal(event(0,2))
            self.assertEqual(number(self.engine.sizing().equity_points),number('285.6'))

    def test_rollover_preserves_boost_and_combines_months_as_one_round(self):
        self.lose_rounds(6)
        self.assertEqual(abs(self.api.current),2)
        before=self.engine.sizing()
        self.api.price+=100
        self.broker.rollover=ProtectionRollover(self.engine,self.base,clock=lambda:datetime(2026,10,21,13,25))
        self.broker.rollover.synchronize()
        self.assertEqual(abs(self.api.current),2)
        self.assertEqual(self.engine.sizing().loss_streak,6)
        self.assertEqual(self.engine.sizing().closed_trades,6)
        self.assertEqual(number(self.engine.sizing().roll_cost_points),number('4.8'))
        self.restart()
        self.engine.on_signal(event(0,20))
        self.assertEqual(self.engine.sizing().closed_trades,7)
        self.assertEqual(number(self.engine.sizing().equity_points)-number(before.equity_points),number('180.8'))


if __name__=='__main__':unittest.main()
