"""Durable expiry-day close/open workflow; uncertain orders are never resent."""
from dataclasses import asdict
from datetime import date, datetime, time as daytime, timezone
import json
import uuid

import shioaji_tmf_target as shared
from ef_trade_runtime import append_order, now_local
from rollover import ManualRollover
from runtime import LedgerError
from strategy import State
from trade_records import entry_record, append_entry


# User-specified dates are cross-checked against broker delivery_date, including
# the holiday-adjusted September 2027 date. Future months use broker metadata.
SETTLEMENT_DATES = dict(zip(
    ['202610', '202611', '202612'] + [f'2027{m:02d}' for m in range(1, 13)],
    ['2026-10-21', '2026-11-18', '2026-12-16', '2027-01-20', '2027-02-17',
     '2027-03-17', '2027-04-21', '2027-05-19', '2027-06-16', '2027-07-21',
     '2027-08-18', '2027-09-16', '2027-10-20', '2027-11-17', '2027-12-15']))
ROLLOVER_PHASES = {'roll_close', 'roll_open'}


class AutomaticRollover(ManualRollover):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS h3_auto_rollover '
                                  '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')

    def plan(self):
        row = self.store.db.execute('SELECT payload FROM h3_auto_rollover WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save_plan(self, plan):
        self.store.db.execute('INSERT OR REPLACE INTO h3_auto_rollover VALUES (1,?)',
                              (json.dumps(plan),))

    def validate_plan(self, plan):
        try:
            old_state = self.engine.state_type(**plan['old_state']).validate()
            valid = (plan['stage'] in {'close', 'open', 'done'}
                     and type(plan['quantity']) is int
                     and old_state.expected == plan['quantity']
                     and isinstance(plan['key'], str) and plan['key'].startswith('auto-roll:')
                     and isinstance(plan['old'], str) and isinstance(plan['new'], str)
                     and plan['old'] != plan['new'])
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise LedgerError('自動換月進度帳本無效，停止送單')

    def audit(self, event, detail):
        self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                              (datetime.now(timezone.utc).isoformat(), event, json.dumps(detail)))

    def due(self):
        row = self.store.db.execute('SELECT month,expiry FROM h3_contract_metadata WHERE code=?',
                                    (self.engine.symbol,)).fetchone()
        if row and row[0] in SETTLEMENT_DATES and row[1] != SETTLEMENT_DATES[row[0]]:
            raise LedgerError('券商到期日與已設定結算日不符，停止換月並請核對日期')
        return super().due()

    def close_deadline(self, old):
        row = self.store.db.execute('SELECT expiry FROM h3_contract_metadata WHERE code=?', (old,)).fetchone()
        if row is None:
            raise LedgerError('缺少舊月到期日，停止換月')
        return datetime.combine(date.fromisoformat(row[0]), daytime(13, 30))

    def check_deadline(self, plan):
        # Both legs start in the 13:25–13:30 window. Completed orders may still
        # be reconciled later; missed windows never submit expired contracts.
        if self.clock() >= self.close_deadline(plan['old']):
            raise LedgerError('已錯過結算日13:25至13:30自動換月送單窗口；不送過期合約，請核對庫存')

    def backup(self):
        from contextlib import closing
        import sqlite3
        self.backups.mkdir(parents=True, exist_ok=True)
        path = self.backups / ('api_key.before_auto_rollover_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite')
        with closing(sqlite3.connect(path)) as destination:
            self.store.db.backup(destination)
        return str(path)

    def start(self, target):
        if self.store.pending() or self.broker.guard.get('pending'):
            raise LedgerError('有未確認委託，停止自動換月；不重送')
        row = self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
        state = self.engine.state()
        if json.loads(row[1]) != asdict(state):
            raise LedgerError('換月準備期間帳本變動')
        old = self.engine.symbol
        shared._refresh_status(self.broker.api)
        positions = shared._read_positions(self.broker.api)
        shared.validate_tmf_account(self.broker.api, positions, contract_code=old)
        if shared._net_position(positions) != state.expected:
            raise LedgerError('自動換月前庫存與帳本不符，不擅自接管或調整部位')
        shared._contract(self.broker.api, target)
        plan = dict(key='auto-roll:' + uuid.uuid4().hex, old=old, new=target,
                    quantity=state.expected, old_state=asdict(state), stage='close',
                    started_at=self.clock().isoformat())
        if state.expected:
            self.check_deadline(plan)
        plan['backup'] = self.backup()
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            if self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone() != row or self.store.pending():
                raise LedgerError('換月準備期間帳本變動，不送單')
            self.save_plan(plan)
            self.audit('pyramid_auto_rollover_started', plan)
        self.announce(f'開始自動換月：{old} → {target}，維持{state.expected:+d}口；先平舊月，確認後才進次月。')
        return plan

    def announce(self, message):
        text = '【API_KEY｜H3自動換月】' + message
        print(text, flush=True)
        # Failure to notify must not strand a fully confirmed close/open pair.
        try:
            self.engine.notify(text)
        except Exception:
            print('換月通知失敗，依持久化進度繼續核對', flush=True)

    def switch_flat(self, plan):
        state = self.engine.state_type(round_key=plan['key'])
        updated = dict(plan, stage='done', completed_at=self.clock().isoformat())
        if self.broker.position() != 0:  # Strict old-month inventory/order checks.
            raise LedgerError('空手切換前實際庫存已變動，不覆蓋帳本')
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            if self.plan() != plan or self.store.pending() or self.engine.state().expected != 0:
                raise LedgerError('空手換月時帳本或委託改變')
            self.store.db.execute('UPDATE pyramid_state SET symbol=?,payload=? WHERE id=1',
                                  (plan['new'], json.dumps(asdict(state))))
            self.save_plan(updated)
            self.audit('pyramid_auto_rollover_done', updated)
        self.engine.symbol = self.broker.contract_code = plan['new']
        self.announce(f'空手切換完成：{plan["new"]}；未下單，等待新H3訊號。')

    def execute_leg(self, plan):
        self.check_deadline(plan)
        close = plan['stage'] == 'close'
        code = plan['old'] if close else plan['new']
        previous, target = (plan['quantity'], 0) if close else (0, plan['quantity'])
        phase = 'roll_close' if close else 'roll_open'
        delta = target - previous
        payload = dict(trigger=plan['key'] + ':' + phase, phase=phase, contract_code=code,
                       previous=previous, target=target, side='buy' if delta > 0 else 'sell', quantity=abs(delta))

        def preflight():
            self.broker.check()
            self.check_deadline(plan)
            shared._refresh_status(self.broker.api)
            positions = shared._read_positions(self.broker.api)
            shared.validate_tmf_account(self.broker.api, positions, contract_code=code)
            if (self.plan() != plan or shared._net_position(positions) != previous
                    or self.engine.symbol != code or self.engine.state().expected != previous):
                raise LedgerError('換月送單前庫存或進度改變，停止本筆')

        preflight()
        self.store.begin(payload)
        self.store.audit('pyramid_rollover_prepared', payload)
        # From this durable intent onwards any uncertainty is reconciled using
        # the unique intent_id and original trade_id, never by submitting again.
        shared.execute_target_position(target, api=self.broker.api, sj=self.broker.sj,
            strict_tmf=True, guard=self.broker.guard, persist_guard=self.broker.persist,
            before_order=preflight, contract_code=code, intent_id=payload['trigger'])
        self.recover_leg(plan)

    def recover_leg(self, plan):
        payload = self.store.pending()
        if not payload or payload.get('phase') not in ROLLOVER_PHASES:
            raise LedgerError('缺少原換月委託紀錄，停止核對')
        phase = 'roll_close' if plan['stage'] == 'close' else 'roll_open'
        code = plan['old'] if phase == 'roll_close' else plan['new']
        previous, target = (plan['quantity'], 0) if phase == 'roll_close' else (0, plan['quantity'])
        side = 'buy' if target > previous else 'sell'
        quantity = abs(target - previous)
        row = self.store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
        guard = json.loads(row[0]) if row else {}
        pending = guard.get('pending') or guard.get('last_resolved', {})
        if (payload != dict(trigger=plan['key'] + ':' + phase, phase=phase, contract_code=code,
                            previous=previous, target=target, side=side, quantity=quantity)
                or pending.get('intent_id') != payload['trigger'] or not pending.get('trade_id')
                or pending.get('contract_code') != code or pending.get('previous_position') != previous
                or pending.get('target_position') != target or pending.get('quantity') != quantity
                or pending.get('side') != side):
            raise LedgerError('原換月委託證據不明或不符；保留保護，不重送')
        shared._refresh_status(self.broker.api)
        trades = self.broker.api.list_trades()
        if not isinstance(trades, list):
            raise LedgerError('換月委託查詢無效，不重送')
        matches = [t for t in trades if shared._trade_id(t) == pending['trade_id']]
        if len(matches) != 1:
            raise LedgerError('找不到唯一原換月委託，不重送')
        trade = matches[0]
        shared._refresh_status(self.broker.api, trade=trade)
        order = shared._position_value(trade, 'order')
        action = shared._position_value(order, 'action')
        filled = shared._position_value(shared._position_value(trade, 'status'), 'deal_quantity')
        if (shared._status_text(trade).lower() not in {'filled', 'cancelled'}
                or type(filled) is not int or filled != quantity
                or shared._position_code(shared._position_value(trade, 'contract')) != code
                or str(getattr(action, 'value', action)).lower() != side
                or shared._position_value(order, 'quantity') != quantity):
            raise LedgerError('原換月委託未完整成交或內容不符，不開下一腿、不重送')
        positions = shared._read_positions(self.broker.api)
        shared.validate_tmf_account(self.broker.api, positions, contract_code=code)
        if shared._net_position(positions) != target:
            raise LedgerError('換月成交與庫存不一致，不開下一腿、不重送')
        entry = None
        if phase == 'roll_open':
            entry = entry_record(shared.OrderResult(previous, target, target, side, quantity, trade),
                                 payload['trigger'], self.clock().isoformat())
            if entry['fill_status'] != 'complete':
                raise LedgerError('次月換月成交價明細不足，不解除保護')
        state = self.engine.state_type(expected=target, first_price=str(entry['entry_price']) if entry else None,
                      added=plan['old_state']['added'] if target else False, round_key=plan['key'])
        state.validate()
        updated = dict(plan, stage='open' if phase == 'roll_close' else 'done')
        if phase == 'roll_open':
            updated['completed_at'] = self.clock().isoformat()
        resolved = dict(pending, status=shared._status_text(trade).lower(), filled_quantity=quantity, actual_position=target)
        new_guard = {k: v for k, v in guard.items() if k != 'pending'}
        new_guard['last_resolved'] = resolved
        state_row = self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
        if state_row[0] != code or self.engine.state().expected != previous:
            raise LedgerError('換月前策略部位與原委託不符，不覆蓋帳本')
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            if (self.plan() != plan or self.store.pending() != payload
                    or self.store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone() != row
                    or self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone() != state_row):
                raise LedgerError('換月核對期間紀錄變動，不解除保護')
            self.store.db.execute('UPDATE pyramid_state SET symbol=?,payload=? WHERE id=1',
                                  (plan['new'], json.dumps(asdict(state))))
            self.store.db.execute('INSERT OR REPLACE INTO shioaji_order_state VALUES (1,?)', (json.dumps(new_guard),))
            self.store.db.execute('DELETE FROM guard WHERE id=1')
            self.save_plan(updated)
            self.audit('pyramid_auto_rollover_leg_confirmed', dict(payload=payload, evidence=resolved, entry=entry))
            if updated['stage'] == 'done':
                self.audit('pyramid_auto_rollover_done', updated)
        self.broker.guard = new_guard
        self.engine.symbol = self.broker.contract_code = plan['new']
        if entry:
            append_entry(self.broker.records / 'entries.csv', entry)
        append_order(self.broker.records / 'live_order_attempts.csv', event=phase, trigger=payload['trigger'],
                     previous_position=previous, target_position=target, actual_position=target,
                     side=side, quantity=quantity, detail=f'{code}換月成交及庫存已確認')
        self.announce(f'{code} {"舊月全平" if phase == "roll_close" else "次月進場"}已確認；部位={target:+d}口。')

    def synchronize(self, *, allow_adopt=True):
        plan = self.plan()
        if plan is not None:
            self.validate_plan(plan)
        active = plan and plan['stage'] != 'done'
        target = None if active else self.due()
        if not active and target is None:
            if self.store.pending() and self.store.pending().get('phase') in ROLLOVER_PHASES:
                raise LedgerError('換月委託缺少有效進度，不重送')
            return True
        if not allow_adopt:
            return False
        if not active:
            plan = self.start(target)
        if plan['quantity'] == 0:
            self.switch_flat(plan)
            return True
        # Recover the original leg first. Only a leg with no submitted intent
        # may initiate an order. Committed stages survive all process restarts.
        if self.store.pending():
            self.recover_leg(plan)
            plan = self.plan()
        while plan['stage'] != 'done':
            self.execute_leg(plan)
            plan = self.plan()
        self.announce(f'換月完成：{plan["new"]}，維持{plan["quantity"]:+d}口；繼續正常H3策略。')
        return True
