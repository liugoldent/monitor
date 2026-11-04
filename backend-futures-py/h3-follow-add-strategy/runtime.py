"""Durable policy transitions; a guard survives any uncertain execution."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

from h_core import TradingError, parse_signal
from strategy import State, flat_direction, observe


class LedgerError(TradingError):
    """Persistent ledger problems require reconciliation, not rapid reconnects."""


class RolloverPause(TradingError):
    """The settlement cutoff was reached before any order submission."""


def signal(event, now=None):
    if not isinstance(event, dict):
        return None
    from h_signal import is_dual_source_event
    if event.get('source') == 'h3_dual_source' and is_dual_source_event(event) and event['direction'] == 0:
        # The shared parser only models +/-1; validate its zero using an equivalent
        # canonical long envelope, preserving source identity and original age.
        validated = parse_signal(dict(event, direction=1, text='浩克3V3 訊號通知 (B=1 S=0)'), now=now)
        return (validated[0], 0, validated[2]) if validated else None
    parsed = parse_signal(event, now=now)
    if parsed is not None:
        return parsed
    if flat_direction(event.get('text')) != 0:
        return None
    # Reuse the shared sender, journal kind, identity and 30-second age checks.
    validated = parse_signal(dict(event, text='浩克3 訊號通知 多1口'), now=now)
    return (validated[0], 0, validated[2]) if validated is not None else None


class Engine:
    state_type = State
    strategy_label = 'H3游擊策略'

    def __init__(self, store, broker, symbol, notify=lambda message: None):
        self.store, self.broker, self.symbol, self.notify = store, broker, symbol, notify
        store.db.execute('CREATE TABLE IF NOT EXISTS pyramid_state '
                         '(id INTEGER PRIMARY KEY CHECK(id=1), symbol TEXT, payload TEXT)')
        store.db.commit()
        if store.db.execute('SELECT 1 FROM pyramid_state').fetchone() is None:
            with store.db:
                store.db.execute('INSERT INTO pyramid_state VALUES (1,?,?)',
                                 (symbol, json.dumps(asdict(self.state_type()))))
        self.state()

    def state(self):
        row = self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
        try:
            state = self.state_type(**json.loads(row[1])).validate()
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            raise LedgerError('游擊策略帳本內容無效，請核對；未進入跟單監控') from exc
        if row[0] != self.symbol:
            raise LedgerError(f'合約月份不符：帳本={row[0]}（部位={state.expected:+d}口），'
                              f'目前近月={self.symbol}；需核對換月及實際庫存，'
                              '未進入跟單監控，不自動改帳或轉倉')
        return state

    def save(self, state):
        state.validate()
        self.store.db.execute('UPDATE pyramid_state SET payload=? WHERE id=1',
                              (json.dumps(asdict(state)),))

    def validate_position(self, *, require_match=True):
        self.broker.check_rollover()
        if self.store.pending() or self.broker.guard.get('pending'):
            raise TradingError('有未確認委託，禁止重送')
        actual = self.broker.position()
        if type(actual) is not int:
            raise TradingError('券商庫存口數無效，不能視為空手')
        if require_match and actual != self.state().expected:
            raise TradingError('API_KEY 庫存與游擊策略帳本不符，不接管舊倉或自動平倉')
        return actual

    def consume_signal(self, parsed):
        # Canonical IDs deduplicate events; a distinct same-direction signal
        # starts a new cycle even within ten seconds.
        key, direction, received = parsed
        with self.store.db:
            if self.store.db.execute('SELECT 1 FROM signals WHERE id=?', (key,)).fetchone():
                return False
            self.store.db.execute('INSERT INTO signals VALUES (?,?,?,?)',
                                  (key, direction, received, 'consumed'))
        return True

    def adopt_signal_inventory(self, key):
        """Adopt a verified snapshot only for a fresh signal's flatten phase."""
        actual = self.validate_position(require_match=False)
        old = self.state()
        if actual == old.expected:
            return
        with self.store.db:
            if self.store.pending() or self.broker.guard.get('pending') or self.state() != old:
                raise LedgerError('庫存同步期間帳本或委託變動，不覆蓋')
            self.save(self.state_type(expected=actual, round_key=key))
            self.on_external_inventory(actual)
            self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                (datetime.now(timezone.utc).isoformat(), 'signal_inventory_adopted',
                 json.dumps(dict(key=key, old_state=asdict(old), actual=actual,
                                 sizing='external changes: incomplete round P&L excluded'))))

    def on_external_inventory(self, actual):
        pass

    def entry_quantity(self):
        return self.broker.unit

    def prepare_order(self, payload):
        pass

    def confirmed_order(self, old, planned, payload):
        return planned

    def order(self, target, phase, key, planned, preflight=None):
        self.validate_position()
        previous = self.state().expected
        payload = dict(trigger=key, phase=phase, previous=previous, target=target,
                       side='buy' if target > previous else 'sell', quantity=abs(target - previous))
        self.prepare_order(payload)
        self.store.begin(payload)
        self.store.audit('pyramid_prepared', payload)
        self.broker.preflight = preflight
        try:
            actual = self.broker.send_and_confirm(target - previous, phase, target)
        except RolloverPause:
            # This exception originates only from the pre-submission rollover
            # gate. Release an unsent intent, never an uncertain broker order.
            if self.broker.guard.get('pending'):
                raise
            with self.store.db:
                if self.store.pending() != payload:
                    raise LedgerError('換月暫停期間委託紀錄改變，不解除 guard')
                self.store.db.execute('DELETE FROM guard WHERE id=1')
                self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                    (datetime.now(timezone.utc).isoformat(), 'pyramid_rollover_unsent', json.dumps(payload)))
            raise
        if actual != target:
            raise TradingError('成交後庫存不符，保留 guard')
        if phase == 'entry':
            planned = replace(planned, first_price=(str(self.broker.first_fill)
                              if self.broker.first_fill is not None else None))
        with self.store.db:
            if self.store.pending() != payload or self.state().expected != previous:
                raise TradingError('委託與策略帳本不符，保留 guard')
            planned = self.confirmed_order(self.state(), planned, payload)
            self.save(replace(planned, expected=target))
            self.store.db.execute('DELETE FROM guard WHERE id=1')
            self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                (datetime.now(timezone.utc).isoformat(), 'pyramid_confirmed', json.dumps(payload)))
        label = {'flatten': '跟隨H3全平', 'entry': '跟隨H3進場', 'add': '游擊穿越加碼'}[phase]
        self.notify(f'【API_KEY｜{self.strategy_label}】{label}：{previous} → {target} 口（成交及庫存已確認）')

    def on_signal(self, event, *, now=None):
        parsed = signal(event, now)
        if parsed is None:
            return False
        if not self.consume_signal(parsed):
            return False
        key, direction, _ = parsed
        def fresh():
            if signal(event) is None:
                raise RolloverPause('H3 訊號已超過30秒，不追單')
        try:
            fresh()
            self.adopt_signal_inventory(key)
            if self.state().expected:
                self.order(0, 'flatten', key, self.state_type(round_key=key), fresh)
            if direction:
                self.order(direction * self.entry_quantity(), 'entry', key, self.state_type(round_key=key), fresh)
                if self.state().first_price is None:
                    self.notify('【API_KEY｜H3游擊策略】缺少第一口實際成交價，本輪停用加碼；出場仍跟隨H3。')
        except RolloverPause:
            # Only a proven pre-submission pause reaches here without a guard.
            # Already confirmed phases remain in the ledger; retrying the same
            # fresh signal continues from that position instead of resending.
            if not self.store.pending() and not self.broker.guard.get('pending'):
                with self.store.db:
                    self.store.db.execute("DELETE FROM signals WHERE id=? AND status='consumed'", (key,))
            raise
        return True

    def on_sample(self, ticker, value, stamp, price, preflight=None):
        if self.validate_position(require_match=False) != self.state().expected:
            # Manual exits wait for the next H3 signal; never add to a stale book.
            return False
        current = self.state()
        planned, add = observe(current, ticker, value, stamp, price)
        if planned == current:
            return False
        if add:
            direction = 1 if current.expected > 0 else -1
            self.order(current.expected + direction, 'add', 'guerrilla:' + ticker + ':' + stamp,
                       replace(planned, added=True), preflight)
        else:
            with self.store.db:
                self.save(planned)
        return add

    def reset_observations(self, ticker=None):
        # Restart/gaps need a new live baseline; never bridge a stale crossing.
        state = self.state()
        def cleared(values):
            return {k: v for k, v in values.items() if ticker is not None and k != ticker}
        with self.store.db:
            self.save(replace(state, previous_guerrilla=cleared(state.previous_guerrilla),
                              sample_at=cleared(state.sample_at), sample_value=cleared(state.sample_value)))
