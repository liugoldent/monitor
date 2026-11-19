"""API_KEY execution using the existing confirmed Shioaji TMF adapter."""
from __future__ import annotations

from recovery_broker import ShioajiRecoveryBroker
from h_core import TradingError, OrderRejected
from ef_trade_runtime import append_order, now_local
from trade_records import entry_record, append_entry
from mdd_sizing import average_fill
from runtime import RolloverPause
from h3_signal_retry import SignalRetry, signal_step
import shioaji_tmf_target as shared


class PyramidBroker(ShioajiRecoveryBroker):
    account_label = 'API_KEY'
    preflight = None
    first_fill = None
    confirmed_price = None
    contract_code = None
    rollover = None

    def cancel_working_orders(self, trigger, preflight):
        from cancel_working import cancel_working_orders
        cancel_working_orders(self, trigger, preflight)

    def startup_position(self):
        # Manual orders wait for a fresh H3 signal; startup never cancels them.
        self.check()
        if self.store.pending() or self.guard.get('pending'):
            raise TradingError(f'{self.account_label} 有未確認委託，停止啟動核對')
        shared._refresh_status(self.api)
        positions = shared._read_positions(self.api)
        shared.validate_tmf_account(self.api, positions, contract_code=self.contract_code,
                                    allow_working_orders=True)
        return shared._net_position(positions)

    def position(self):
        self.check()
        shared._refresh_status(self.api)
        positions = shared._read_positions(self.api)
        route = dict(contract_code=self.contract_code) if self.contract_code else {}
        shared.validate_tmf_account(self.api, positions, ignore_expired_rod=True, **route)
        return shared._net_position(positions)

    def check_rollover(self):
        if self.rollover is not None and not self.rollover.synchronize(allow_adopt=False):
            raise RolloverPause('結算換月處理中，暫停一般策略送單')

    def send_and_confirm(self, delta, phase, target):
        payload = self.store.pending()
        if (not payload or payload['phase'] != phase or payload['target'] != target
                or payload['previous'] + delta != target or payload['quantity'] != abs(delta)):
            raise TradingError('游擊策略委託與 guard 不符')
        previous = payload['previous']
        if not self.valid_transition(previous, delta, phase, target):
            raise TradingError('策略委託口數與進場設定或全平庫存不符')

        def before_order():
            self.check_rollover()
            if self.position() != previous:
                raise TradingError('送單前 API_KEY 庫存改變，停止核對')
            if self.preflight is not None:
                self.preflight()

        self.first_fill = None
        self.confirmed_price = None
        original_evidence = self.guard.get('last_resolved')
        retry_enabled = isinstance(getattr(self, 'signal_retry', None), SignalRetry)
        retry_submitted = False

        def submit():
            before_order()
            return shared.execute_target_position(
                target, api=self.api, sj=self.sj, strict_tmf=True,
                ignore_expired_rod=True,
                before_order=before_order, guard=self.guard, persist_guard=self.persist,
                intent_id=payload['trigger'], expected_previous=previous,
                **(dict(contract_code=self.contract_code) if self.contract_code else {}))

        def recover():
            nonlocal original_evidence, retry_submitted
            pending = self.guard.get('pending')
            if not pending:
                if self.guard.get('last_resolved') != original_evidence:
                    raise TradingError('原委託證據已變動，本筆停止重送')
            else:
                result = self.reconcile_signal_order(payload, pending)
                if result is not None:
                    return result
                original_evidence = self.guard['last_resolved']
            retry_submitted = True
            return submit()

        try:
            result = signal_step(self, phase, submit, recover=recover)
        except RolloverPause:
            raise
        except Exception as exc:
            if retry_enabled:
                # An error before submission may leave the strategy intent in
                # SQLite. Release it only when no broker request is outstanding.
                if not self.guard.get('pending') and self.guard.get('last_resolved') == original_evidence:
                    self.store.resolve_rejected(payload, dict(reason=type(exc).__name__, unsent=True))
                elif retry_submitted and isinstance(exc, shared.BrokerOrderError):
                    evidence = self.rejected_evidence(payload)
                    if evidence:
                        self.store.resolve_rejected(payload, evidence)
            elif isinstance(exc, shared.BrokerOrderError):
                evidence = self.rejected_evidence(payload)
                if evidence:
                    raise OrderRejected(f'整筆零成交拒單已核對（{exc}）', evidence) from None
            raise
        filled = shared._position_value(shared._position_value(result.trade, 'status'), 'deal_quantity')
        if (not result.confirmed or result.previous_position != previous
                or result.quantity != abs(delta) or type(filled) is not int or filled != abs(delta)
                or result.actual_position != target):
            raise TradingError('未確認完整成交與一致庫存，保留 guard')
        entry = entry_record(result, payload['trigger'], now_local().isoformat())
        if entry['fill_status'] == 'complete':
            self.confirmed_price = average_fill(entry)
        if phase in {'entry', 'add'}:
            self.first_fill = entry['entry_price'] if entry['fill_status'] == 'complete' else None
            self.store.audit('pyramid_fills', dict(phase=phase, **entry))
            append_entry(self.records / 'entries.csv', entry)
        append_order(self.records / 'live_order_attempts.csv', event=phase,
                     trigger=payload['trigger'], target_position=target, previous_position=previous,
                     actual_position=result.actual_position, side=result.side, quantity=result.quantity,
                     detail='成交及庫存已確認')
        return result.actual_position

    def reconcile_signal_order(self, payload, pending):
        """Resolve the original order before allowing its one zero-fill retry."""
        previous, target = payload['previous'], payload['target']
        side, quantity = payload['side'], payload['quantity']
        if ((pending.get('previous_position'), pending.get('target_position'),
             pending.get('side'), pending.get('quantity'), pending.get('intent_id'),
             pending.get('contract_code'))
                != (previous, target, side, quantity, payload['trigger'], self.contract_code)):
            raise TradingError('原委託與本次訊號不符，本筆停止重送')
        trade_id = pending.get('trade_id')
        if not trade_id:
            raise TradingError('原委託未取得單號，無法排除已送達，本筆停止重送')
        shared._refresh_status(self.api)
        trades = self.api.list_trades()
        matches = [trade for trade in trades if shared._trade_id(trade) == trade_id] if isinstance(trades, list) else []
        if len(matches) != 1:
            raise TradingError('無法唯一核對原委託，本筆停止重送')
        trade = matches[0]
        shared._refresh_status(self.api, trade=trade)
        order = shared._position_value(trade, 'order')
        action = shared._position_value(order, 'action')
        if (shared._position_code(shared._position_value(trade, 'contract')) != self.contract_code
                or str(getattr(action, 'value', action)).lower() != side
                or shared._position_value(order, 'quantity') != quantity):
            raise TradingError('券商原委託的合約、方向或口數不符，本筆停止重送')
        status = shared._status_text(trade).lower()
        filled = shared._position_value(shared._position_value(trade, 'status'), 'deal_quantity')
        if type(filled) is not int or not (
                (filled == 0 and status in {'cancelled', 'failed', 'inactive'})
                or (filled == quantity and status in {'filled', 'cancelled'})):
            raise TradingError('原委託未終結、部分成交或成交口數不明，本筆停止重送')
        shared._resolve_pending(self.api, self.guard, self.persist, contract_code=self.contract_code)
        resolved = self.guard['last_resolved']
        if resolved['filled_quantity'] != filled:
            raise TradingError('原委託成交證據變動，本筆停止重送')
        if filled:
            return shared.OrderResult(previous, target, resolved['actual_position'], side, quantity, trade)
        return None

    def valid_transition(self, previous, delta, phase, target):
        return ((phase == 'flatten' and target == 0 and previous != 0 and delta == -previous)
                or (phase == 'entry' and previous == 0
                    and abs(target) == abs(delta) == getattr(self, 'entry_quantity', self.unit))
                or (phase == 'add' and not hasattr(self, 'entry_quantity')
                    and previous != 0 and delta == (1 if previous > 0 else -1)
                    and target == previous + delta))
