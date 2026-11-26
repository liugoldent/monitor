"""H3 signal sizing and H2 fixed targets share one confirmed TMF net position."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

from h_core import TradingError
from h_signal import parse_h2_signal
from h3_signal_retry import signal_retry_used, signal_step
from mdd_runtime import MddEngine
from mdd_sizing import save_sizing
from net_positions import Positions, net_transition, positions_initialized, read_positions, save_positions
from runtime import LedgerError, RolloverPause, signal
from auto_trade import H2_UNIT


class PortfolioEngine(MddEngine):
    strategy_label = 'H3 回撤加口＋H2 固定口數'
    uses_strategy_positions = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            if not positions_initialized(self.store):
                self.store.db.execute('CREATE TABLE api_key_strategy_positions '
                                      '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
                if self.store.pending() or self.broker.guard.get('pending'):
                    raise LedgerError('舊 H3 委託尚未核對，不建立 H2 帳本')
                save_positions(self.store, Positions(h3=self.state().expected))
                # This ledger describes old broker-confirmed H3-only fills.
                # H3 sizing uses signal history, never this broker-fill ledger.
                save_sizing(self.store, replace(self.sizing(), tracking=False, carry='0'))
        self.strategy_positions()

    def strategy_positions(self):
        return read_positions(self.store)

    def validate_position(self, *, require_match=True):
        positions = self.strategy_positions()
        if positions.net != self.state().expected:
            raise LedgerError('H3／H2 合計與券商帳本不符，不送單')
        return super().validate_position(require_match=require_match)

    def on_external_inventory(self, actual):
        if actual != self.strategy_positions().net:
            raise LedgerError('次月實際庫存與 H3／H2 合計不符，不自動接管')

    def on_signal(self, event, *, now=None):
        source = 'h2' if isinstance(event, dict) and event.get('route') == 'h2' else 'h3'
        parser = parse_h2_signal if source == 'h2' else signal
        parsed = parser(event, now=now)
        if parsed is None:
            return False
        key, direction, _ = parsed

        def fresh():
            if parser(event) is None:
                raise RolloverPause(f'{source.upper()} 訊號已超過30秒，不追單')

        if not self.consume_signal(parsed):
            return False
        try:
            fresh()
            self.broker.check_rollover()
            signal_step(self.broker, 'cancel_working_orders', lambda: self.prepare_signal(key, fresh))
            signal_step(self.broker, 'net_inventory', self.validate_position)
            before = self.strategy_positions()
            note = ''
            if source == 'h3' and direction:
                plan = self.signal_entry_plan(event, direction)
                if plan is None:
                    return True
                quantity, note = plan
            else:
                quantity = H2_UNIT if source == 'h2' else 0
            after = replace(before, **{source: direction * quantity})
            if source == 'h3' and direction:
                signal_step(self.broker, 'net_inventory_after_history', self.validate_position)
            fresh()
            self.adjust_net(source, before, after, key, fresh, note)
        except RolloverPause:
            if (not signal_retry_used(self.broker)
                    and not self.store.pending() and not self.broker.guard.get('pending')):
                with self.store.db:
                    self.store.db.execute("DELETE FROM signals WHERE id=? AND status='consumed'", (key,))
            raise
        return True

    def adjust_net(self, source, before, after, key, fresh, note):
        target = after.net
        previous = before.net
        payload = dict(trigger=key, phase='net', strategy=source, previous=previous, target=target,
                       positions_before=asdict(before), positions_after=asdict(after))

        if target != previous:
            payload.update(side='buy' if target > previous else 'sell', quantity=abs(target - previous))
            net_transition(self.store, payload)
            self.store.begin(payload)
            self.store.audit('api_key_net_prepared', payload)
            self.broker.preflight = fresh
            try:
                actual = self.broker.send_and_confirm(target - previous, 'net', target)
            except RolloverPause:
                if not self.broker.guard.get('pending'):
                    self.store.resolve_rejected(payload, dict(unsent=True, reason='rollover_or_expired'))
                raise
            finally:
                self.broker.preflight = None
            if actual != target:
                raise TradingError('合計委託成交後庫存不符，保留 guard')

        with self.store.db:
            if (self.strategy_positions() != before or self.state().expected != previous
                    or self.store.pending() != (payload if target != previous else None)
                    or self.broker.guard.get('pending')):
                raise LedgerError('合計委託或帳本變動，保留保護')
            save_positions(self.store, after)
            if target != previous:
                self.save(self.state_type(expected=target, round_key=key))
                self.store.db.execute('DELETE FROM guard WHERE id=1')
            self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                (datetime.now(timezone.utc).isoformat(), 'api_key_net_confirmed', json.dumps(dict(
                    payload, broker_fill_price=(self.broker.confirmed_price if target != previous else None)))))
        action = '淨部位不變，未送單' if target == previous else '差額成交及庫存已確認'
        self.notify(f'【API_KEY｜{source.upper()}】H3 {after.h3:+d}口｜H2 {after.h2:+d}口｜'
                    f'淨部位 {previous:+d} → {target:+d}口｜{action}' + (f'｜{note}' if note else ''))
