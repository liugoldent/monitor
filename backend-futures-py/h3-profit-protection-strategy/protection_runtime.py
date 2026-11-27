"""Fixed-quantity H3 following with separate broker-fill accounting."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import protection_bootstrap
from h_core import TradingError
from h3_signal_retry import signal_retry_used, signal_step
from runtime import Engine as BaseEngine, LedgerError, RolloverPause, signal
from follow_state import FollowState as State
from protection_policy import Protection
from protection_policy import number
from recovery_sizing import RecoverySizing


class ProtectionEngine(BaseEngine):
    state_type = State

    def __init__(self, store, broker, symbol, notify=lambda message: None):
        # Reuse established concrete-contract ledger shape for rollover/broker.
        super().__init__(store, broker, symbol, notify)
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS profit_protection '
                             '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if store.db.execute('SELECT 1 FROM profit_protection').fetchone() is None:
                if self.state().expected or store.pending():
                    raise LedgerError('缺少永豐2保護帳本，不接管既有部位')
                self.save_protection(Protection())
        self.protection()
        with store.db:
            existed = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='h3_profit_sizing'").fetchone()
            store.db.execute('CREATE TABLE IF NOT EXISTS h3_profit_sizing '
                             '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if store.db.execute('SELECT 1 FROM h3_profit_sizing').fetchone() is None:
                if existed or store.pending():
                    raise LedgerError('成交損益帳本缺漏或有未確認委託，不自動重設')
                self.save_sizing(RecoverySizing())
                self.audit_in_transaction('sizing_initialized', dict(
                    basis='new closed-H3-round ledger; no historical backfill',
                    existing_position=self.state().expected))
        self.sizing()

    def sizing(self):
        try:
            row = self.store.db.execute('SELECT payload FROM h3_profit_sizing WHERE id=1').fetchone()
            return RecoverySizing.from_payload(json.loads(row[0]))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise LedgerError('永豐2成交損益帳本無效，停止送單並保留原始狀態') from exc

    def save_sizing(self, value):
        value.validate()
        self.store.db.execute('INSERT OR REPLACE INTO h3_profit_sizing VALUES (1,?)',
                              (json.dumps(asdict(value)),))

    def on_external_inventory(self, actual):
        self.save_protection(Protection(h3_direction=(1 if actual > 0 else -1) if actual else 0))
        sizing = self.sizing()
        self.save_sizing(replace(sizing, roll_cost_points='0'))
        self.audit_in_transaction('sizing_external_round_excluded', dict(
            actual=actual, sizing_before=asdict(sizing),
            reason='external inventory changed; full manual realized P&L unavailable'))

    def protection(self):
        try:
            row = self.store.db.execute('SELECT payload FROM profit_protection WHERE id=1').fetchone()
            value = Protection(**json.loads(row[0])).validate()
            base = self.state()
            if base.added:
                raise ValueError('永豐2禁止加碼')
            direction = (1 if base.expected > 0 else -1) if base.expected else 0
            if base.expected and (value.h3_direction != direction or value.locked):
                raise ValueError('保護輪次與持倉不符')
            return value
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise LedgerError('永豐2保護帳本無效，保留原始狀態並等待核對') from exc

    def save_protection(self, value):
        value.validate()
        self.store.db.execute('INSERT OR REPLACE INTO profit_protection VALUES (1,?)',
                              (json.dumps(asdict(value)),))

    def audit_in_transaction(self, event, detail):
        self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
            (datetime.now(timezone.utc).isoformat(), event, json.dumps(detail, ensure_ascii=False)))

    def order(self, target, phase, key, planned, protection, preflight=None):
        signal_step(self.broker, phase + '_inventory', self.validate_position)
        old_state = self.state()
        previous = old_state.expected
        sizing = self.sizing()
        old_protection = self.protection()
        if not self.broker.valid_transition(previous, target-previous, phase, target):
            raise TradingError('永豐2委託口數與固定進場口數或全平庫存不符')
        protection.validate()
        payload = dict(trigger=key, phase=phase, previous=previous, target=target,
            side='buy' if target > previous else 'sell', quantity=abs(target-previous),
            reason='H3', planned_state=asdict(planned), planned_protection=asdict(protection))
        payload['sizing_before'] = asdict(sizing)
        # Check before creating the durable intent AND again at the broker gate.
        if preflight:
            preflight()
        self.store.begin(payload)
        self.store.audit('protection_prepared', payload)
        self.broker.preflight = preflight
        try:
            actual = self.broker.send_and_confirm(target-previous, phase, target)
        except RolloverPause:
            if self.broker.guard.get('pending'):
                raise
            with self.store.db:
                if self.store.pending() != payload:
                    raise LedgerError('未送委託的紀錄已變動，不解除阻擋')
                self.store.db.execute('DELETE FROM guard WHERE id=1')
                self.audit_in_transaction('protection_unsent', payload)
            raise
        if actual != target:
            raise TradingError('成交後庫存不符，保留保護委託 guard')
        price = self.broker.confirmed_price
        if price is None or number(price) <= 0:
            raise LedgerError('缺少完整成交均價，保留 guard，不更新損益')
        if phase == 'entry':
            planned = replace(planned, first_price=str(price))
            from ef_trade_runtime import now_local
            protection = replace(protection, entered_at=now_local().isoformat())
        elif phase == 'flatten' and old_state.first_price is not None:
            gross = ((number(price)-number(old_state.first_price)) * (1 if previous > 0 else -1)
                     + number(old_protection.carry_points))
            sizing = sizing.close(gross, abs(previous))
        with self.store.db:
            if (self.store.pending() != payload or self.state() != old_state
                    or asdict(self.sizing()) != payload['sizing_before']):
                raise LedgerError('成交後帳本變動，保留 guard')
            self.save(replace(planned, expected=target))
            self.save_protection(protection)
            self.save_sizing(sizing)
            self.store.db.execute('DELETE FROM guard WHERE id=1')
            self.audit_in_transaction('protection_confirmed', dict(payload=payload,
                state=asdict(self.state()), protection=asdict(protection), sizing=asdict(sizing),
                confirmed_price=str(price)))
        label = f'跟隨H3進場{abs(target)}口' if phase == 'entry' else '跟隨H3全平'
        message = f'【永豐2｜H3跟單】{label}：{previous:+d} → {target:+d}口，成交與庫存已確認。'
        self.notify(message)

    def on_signal(self, event, *, now=None):
        parsed = signal(event, now)
        if parsed is None:
            return False
        if not self.consume_signal(parsed):
            return False
        key, direction, _ = parsed

        def preflight():
            if signal(event) is None:
                raise RolloverPause('H3訊號已過期，本筆尚未送出')

        next_protection = Protection(h3_direction=direction)
        try:
            preflight()
            self.protection()
            self.sizing()
            signal_step(self.broker, 'cancel_working_orders',
                        lambda: self.broker.cancel_working_orders(key, preflight))
            preflight()
            if getattr(self.broker, 'close_all_months', False) is True:
                self.broker.close_signal_months(key, preflight)
            signal_step(self.broker, 'signal_inventory', lambda: self.adopt_signal_inventory(key))
            if self.state().expected:
                self.order(0, 'flatten', key, State(round_key=key), next_protection, preflight)
            if direction:
                self.order(direction * self.broker.unit, 'entry', key, State(round_key=key),
                           next_protection, preflight)
            else:
                with self.store.db:
                    self.save(State(round_key=key))
                    self.save_protection(next_protection)
                    self.audit_in_transaction('protection_round_flat', dict(key=key))
        except RolloverPause:
            if (not signal_retry_used(self.broker)
                    and not self.store.pending() and not self.broker.guard.get('pending')):
                with self.store.db:
                    self.store.db.execute("DELETE FROM signals WHERE id=? AND status='consumed'", (key,))
            raise
        return True

    def on_price(self, price, bar_at, received_at, preflight=None):
        # Price-based exits are disabled. Keep this harmless compatibility hook
        # so old callers cannot submit a profit-protection order.
        return False
