"""Reuse confirmed expiry workflow; retain the H3 round's peak across months."""
from dataclasses import replace
import json

import protection_bootstrap
from auto_rollover import AutomaticRollover
from runtime import LedgerError
from protection_policy import Protection, number
from trade_records import entry_record
import shioaji_tmf_target as shared


class ProtectionRollover(AutomaticRollover):
    close_points = None

    def announce(self, message):
        text = '【永豐2｜H3跟單換月】'+message
        print(text, flush=True)
        try:
            self.engine.notify(text)
        except Exception:
            print('永豐2換月通知失敗，保留成交核對進度', flush=True)

    def recover_leg(self, plan):
        self.close_points = None
        # Obtain complete old-contract fills BEFORE the parent's atomic commit.
        # Its existing reconciliation separately validates order identity,
        # contract, side, full quantity, broker guards and actual inventory.
        if plan['stage'] == 'close':
            row = self.store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
            guard = json.loads(row[0]) if row else {}
            pending = guard.get('pending') or guard.get('last_resolved', {})
            shared._refresh_status(self.broker.api)
            trades = self.broker.api.list_trades()
            matches = [t for t in trades if shared._trade_id(t) == pending.get('trade_id')] if isinstance(trades, list) else []
            if len(matches) != 1:
                raise LedgerError('永豐2換月缺少唯一舊月委託，不重送')
            trade = matches[0]
            shared._refresh_status(self.broker.api, trade=trade)
            quantity = plan['quantity']
            side = 'sell' if quantity > 0 else 'buy'
            fill = entry_record(shared.OrderResult(quantity, 0, 0, side, abs(quantity), trade),
                                plan['key'], self.clock().isoformat())
            if fill['fill_status'] != 'complete' or plan['old_state'].get('first_price') is None:
                raise LedgerError('永豐2換月缺少舊月完整成交價／進場價，不重設保護')
            average = sum(number(f['price'])*f['quantity'] for f in fill['fills']) / abs(quantity)
            self.close_points = ((average-number(plan['old_state']['first_price']))
                                 * (1 if quantity > 0 else -1))
        return super().recover_leg(plan)

    def audit(self, event, detail):
        # Called INSIDE the inherited confirmed-leg transaction. Protection
        # carry and actual contract position therefore commit together.
        if event == 'pyramid_auto_rollover_leg_confirmed':
            # Parent has switched the DB contract inside this transaction;
            # engine.symbol changes only AFTER commit. Validate protection
            # directly here rather than reading the temporarily old symbol.
            row = self.store.db.execute('SELECT payload FROM profit_protection WHERE id=1').fetchone()
            protection = Protection(**json.loads(row[0])).validate()
            quantity = self.plan()['quantity']
            direction = (1 if quantity > 0 else -1) if quantity else 0
            if protection.locked or protection.h3_direction != direction:
                raise LedgerError('換月輪次方向與保護帳本不符，不覆蓋')
            if detail['payload']['phase'] == 'roll_close':
                if self.close_points is None:
                    raise LedgerError('缺少已核對換月損益，不覆蓋保護帳本')
                protection = replace(protection,
                    carry_points=str(number(protection.carry_points)+self.close_points))
                # Keep the H3 round intact; count the extra roll roundtrip cost
                # only when the eventual H3 exit closes this entire round.
                self.engine.save_sizing(self.engine.sizing().rolled())
            else:
                fill = detail['entry']
                if not fill or fill['fill_status'] != 'complete':
                    raise LedgerError('換月缺少完整新月成交均價')
                average = sum(number(f['price'])*f['quantity'] for f in fill['fills']) / abs(quantity)
                row = self.store.db.execute('SELECT payload FROM pyramid_state WHERE id=1').fetchone()
                state = self.engine.state_type(**json.loads(row[0])).validate()
                self.engine.save(replace(state, first_price=str(average)))
                # Retain peak and armed state, but the old-month sample must
                # never be reused against the newly filled contract's cost.
                protection = replace(protection, entered_at=self.clock().isoformat(),
                    sample_bar=None, sample_received=None, sample_price=None)
            self.engine.save_protection(protection)
        super().audit(event, detail)
