"""API_KEY3 adapter for the same durable H3 recovery executor as KGI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
sys.path.insert(0, str(BASE.parent / 'h3-capital-flatten-reentry-strategy'))
from h_core import OrderRejected, TradingError
import shioaji_tmf_target as shared
from ef_trade_runtime import append_order, now_local
from trade_records import append_entry, entry_record


class ShioajiRecoveryBroker:
    def __init__(self, api, sj, store, watchdog, *, records=BASE / 'records'):
        self.api, self.sj, self.store, self.watchdog = api, sj, store, watchdog
        self.records = Path(records)
        self.entry_quantity = 1
        store.db.execute('CREATE TABLE IF NOT EXISTS shioaji_order_state '
                         '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        store.db.commit()
        row = store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
        self.guard = json.loads(row[0]) if row else {}
        if self.guard.get('pending'):
            raise TradingError('永豐 API_KEY3 有未確認委託，請核對；不重送')

    def persist(self):
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO shioaji_order_state VALUES (1,?)',
                                  (json.dumps(self.guard),))

    def check(self):
        disconnected, _ = self.watchdog.status()
        if disconnected:
            raise TradingError('永豐連線中斷，停止本輪；重啟後只等待新訊號')

    def position(self):
        self.check()
        shared._refresh_status(self.api)
        positions = shared._read_positions(self.api)
        shared.validate_tmf_account(self.api, positions)
        return shared._net_position(positions)

    def rejected_evidence(self, payload):
        pending = self.guard.get('pending', {})
        trade_id = pending.get('trade_id')
        if not trade_id:
            return None
        trades = self.api.list_trades()
        if not isinstance(trades, list):
            return None
        matches = [t for t in trades if shared._trade_id(t) == trade_id]
        if len(matches) != 1:
            return None
        trade = matches[0]
        shared._refresh_status(self.api, trade=trade)
        status = shared._status_text(trade).lower()
        filled = shared._position_value(shared._position_value(trade, 'status'), 'deal_quantity')
        if status not in {'failed', 'inactive', 'cancelled'} or type(filled) is not int or filled != 0:
            return None
        if (pending.get('previous_position') != payload['previous']
                or pending.get('target_position') != payload['target']
                or pending.get('quantity') != payload['quantity']
                or self.position() != payload['previous']):
            return None
        shared._resolve_pending(self.api, self.guard, self.persist)
        return dict(trade_id=trade_id, status=status, filled_quantity=0,
                    actual_position=payload['previous'])

    def send_and_confirm(self, delta, phase, target):
        payload = self.store.pending()
        if (not payload or payload['phase'] != phase or payload['target'] != target
                or payload['previous'] + delta != target or payload['quantity'] != abs(delta)):
            raise TradingError('永豐 H3 委託與持久化 guard 不符')
        if phase == 'entry' and (self.entry_quantity not in (1, 2)
                                or abs(delta) != self.entry_quantity or target != delta):
            raise TradingError('永豐 H3 只允許空手後進場1或2口')
        if phase not in {'flatten', 'entry'} or (phase == 'flatten' and target != 0):
            raise TradingError('永豐 H3 下單階段錯誤')

        def before_order():
            if self.position() != payload['previous']:
                raise TradingError('送單前永豐庫存變動，停止核對')

        before_order()
        try:
            result = shared.execute_target_position(
                target, api=self.api, sj=self.sj, strict_tmf=True,
                before_order=before_order, guard=self.guard, persist_guard=self.persist)
        except shared.BrokerOrderError:
            evidence = self.rejected_evidence(payload)
            if evidence:
                raise OrderRejected('永豐整筆零成交拒單已核對', evidence) from None
            raise
        filled = shared._position_value(shared._position_value(result.trade, 'status'), 'deal_quantity')
        if (not result.confirmed or result.previous_position != payload['previous']
                or result.quantity != abs(delta) or type(filled) is not int or filled != abs(delta)
                or result.actual_position != target):
            raise TradingError('永豐 H3 未取得完整成交與一致庫存，保留 guard')
        if phase == 'entry':
            entry = entry_record(result, payload['trigger'], now_local().isoformat())
            self.store.audit('shioaji_entry_fills', entry)
            append_entry(self.records / 'entries.csv', entry)
        append_order(self.records / 'live_order_attempts.csv', event=phase,
                     trigger=payload['trigger'], target_position=target,
                     previous_position=payload['previous'], actual_position=result.actual_position,
                     side=result.side, quantity=result.quantity, detail='成交及庫存已確認')
        return result.actual_position
