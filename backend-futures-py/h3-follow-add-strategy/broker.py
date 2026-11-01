"""API_KEY execution using the existing confirmed Shioaji TMF adapter."""
from __future__ import annotations

from recovery_broker import ShioajiRecoveryBroker
from h_core import TradingError, OrderRejected
from ef_trade_runtime import append_order, now_local
from trade_records import entry_record, append_entry
from runtime import RolloverPause
import shioaji_tmf_target as shared


class PyramidBroker(ShioajiRecoveryBroker):
    account_label = 'API_KEY'
    preflight = None
    first_fill = None
    contract_code = None
    rollover = None

    def position(self):
        self.check()
        shared._refresh_status(self.api)
        positions = shared._read_positions(self.api)
        route = dict(contract_code=self.contract_code) if self.contract_code else {}
        shared.validate_tmf_account(self.api, positions, **route)
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
            raise TradingError('游擊策略僅允許跟單1口、加碼1口或全平')

        def before_order():
            self.check_rollover()
            if self.position() != previous:
                raise TradingError('送單前 API_KEY 庫存改變，停止核對')
            if self.preflight is not None:
                self.preflight()

        before_order()
        self.first_fill = None
        try:
            result = shared.execute_target_position(
                target, api=self.api, sj=self.sj, strict_tmf=True,
                before_order=before_order, guard=self.guard, persist_guard=self.persist,
                **(dict(contract_code=self.contract_code) if self.contract_code else {}))
        except shared.BrokerOrderError:
            evidence = self.rejected_evidence(payload)
            if evidence:
                raise OrderRejected('API_KEY 整筆零成交拒單已核對', evidence) from None
            raise
        filled = shared._position_value(shared._position_value(result.trade, 'status'), 'deal_quantity')
        if (not result.confirmed or result.previous_position != previous
                or result.quantity != abs(delta) or type(filled) is not int or filled != abs(delta)
                or result.actual_position != target):
            raise TradingError('未確認完整成交與一致庫存，保留 guard')
        if phase in {'entry', 'add'}:
            entry = entry_record(result, payload['trigger'], now_local().isoformat())
            self.first_fill = entry['entry_price'] if entry['fill_status'] == 'complete' else None
            self.store.audit('pyramid_fills', dict(phase=phase, **entry))
            append_entry(self.records / 'entries.csv', entry)
        append_order(self.records / 'live_order_attempts.csv', event=phase,
                     trigger=payload['trigger'], target_position=target, previous_position=previous,
                     actual_position=result.actual_position, side=result.side, quantity=result.quantity,
                     detail='成交及庫存已確認')
        return result.actual_position

    def valid_transition(self, previous, delta, phase, target):
        return ((phase == 'flatten' and target == 0 and 1 <= abs(previous) <= 2)
                or (phase == 'entry' and previous == 0 and abs(target) == abs(delta) == 1)
                or (phase == 'add' and abs(previous) == abs(delta) == 1 and target == previous * 2))
