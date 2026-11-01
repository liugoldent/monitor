from datetime import datetime,timedelta
import unittest

from backtest_h3_profit_grid import Policy, PriceBar, prepare, replay


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,10,1,9)
        self.trade=dict(id='1',entry=self.start,exit=self.start+timedelta(minutes=8),
                        entry_price=40000,exit_price=40100,side=1,baseline_points=100)

    def bars(self, closes, opens=None, side=1):
        if opens is None:
            opens=closes
        return [PriceBar(self.start+timedelta(minutes=i),
            self.start+timedelta(minutes=i,seconds=61),
            40000+side*o,40000+side*c) for i,(o,c) in enumerate(zip(opens,closes))]

    def run_policy(self, bars, policy, cost=0, trade=None):
        prepared,next_open=prepare([trade or self.trade],bars)
        return replay(prepared,next_open,policy,cost)

    def test_fixed_tp_is_different_from_trailing_activation(self):
        bars=self.bars([600,1000,650,660,640,1500],[600,1000,620,680,640,1500])
        fixed,_=self.run_policy(bars,Policy('fixed',mode='fixed_tp',activation=500))
        trail,detail=self.run_policy(bars,Policy('trail',activation=500,giveback=.3))
        self.assertEqual(fixed['total'],620)
        self.assertEqual(trail['total'],640)
        self.assertEqual(detail[0]['actual_exit'],self.start+timedelta(minutes=4))

    def test_1000_threshold_does_not_arm_at_999(self):
        result,_=self.run_policy(self.bars([999,600,500,400]),Policy('trail',activation=1000))
        self.assertEqual(result['total'],100)
        self.assertEqual(result['early_exits'],0)

    def test_short_symmetry_and_cost_reconciliation(self):
        trade=dict(self.trade,side=-1,exit_price=39900)
        result,_=self.run_policy(self.bars([500,350,340,330],side=-1),Policy('trail'),cost=2,trade=trade)
        self.assertEqual(result['total'],330)
        self.assertEqual(result['net_total'],326)
        self.assertEqual(result['gross_profit']-result['gross_loss'],result['realized'])

    def test_stale_quote_does_not_arm(self):
        bars=self.bars([1000,300,200,100,0])
        bars[0]=PriceBar(bars[0].bar_time,self.start+timedelta(seconds=181),40000,41000)
        result,_=self.run_policy(bars,Policy('trail'))
        self.assertEqual(result['total'],100)

    def test_no_same_round_reentry(self):
        result,_=self.run_policy(self.bars([500,350,340,330,2000,300,200]),Policy('trail'))
        self.assertEqual(result['total'],330)
        self.assertEqual(result['one_way_turnover'],2)
        self.assertEqual(result['ending_position'],0)

    def test_h3_exit_wins_if_next_open_would_be_later(self):
        trade=dict(self.trade,exit=self.start+timedelta(minutes=3))
        result,_=self.run_policy(self.bars([500,350,340,330]),Policy('trail'),trade=trade)
        self.assertEqual(result['total'],100)
        self.assertEqual(result['early_exits'],0)


if __name__=='__main__':
    unittest.main()
