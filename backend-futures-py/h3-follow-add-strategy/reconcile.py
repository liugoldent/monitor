"""Record an already fully filled order after read-only broker reconciliation.

This module has no submission path. It never replays a signal or starts trading.
"""
from dataclasses import asdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import shioaji_tmf_target as shared
from h_core import TradingError
from strategy import State
from trade_records import entry_record, append_entry


def reconcile_filled_entry(store, api, backup_path, records):
    payload = store.pending()
    if not payload or payload.get('phase') != 'entry' or payload.get('previous') != 0:
        raise TradingError('只核對空手後第一口待確認進場；帳本不符，保持阻擋')
    return reconcile_filled_order(store, api, backup_path, records)


def reconcile_filled_order(store, api, backup_path, records):
    payload = store.pending()
    row = store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
    guard = json.loads(row[0]) if row else {}
    pending = guard.get('pending') or guard.get('last_resolved', {})
    state_row = store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
    if not payload or not state_row:
        raise TradingError('缺少待核對委託或策略帳本，保持阻擋')
    current_state = State(**json.loads(state_row[1])).validate()
    previous = payload.get('previous')
    target = payload['target']
    phase = payload.get('phase')
    if (type(previous) is not int or type(target) is not int
            or current_state.expected != previous):
        raise TradingError('委託前部位與帳本不符，保持阻擋')
    quantity = abs(target - previous)
    valid = ((phase == 'entry' and previous == 0 and abs(target) == 1)
             or (phase == 'add' and abs(previous) == 1 and target == previous * 2)
             or (phase == 'flatten' and 1 <= abs(previous) <= 2 and target == 0))
    side = 'buy' if target > previous else 'sell'
    if (not valid or payload.get('quantity') != quantity or payload.get('side') != side
            or pending.get('previous_position') != previous
            or pending.get('target_position') != target or pending.get('quantity') != quantity
            or pending.get('side') != side or not pending.get('trade_id')):
        raise TradingError('兩份待確認委託紀錄不一致，保持阻擋')
    shared._refresh_status(api)
    code = (shared.tmf_contract_code(api) if state_row[0] == 'TMFR1' else state_row[0])
    try:
        shared._contract(api, code)
    except shared.BrokerOrderError as exc:
        raise TradingError('帳本商品月份未確認，保持阻擋') from exc
    if state_row[0] not in {'TMFR1', code}:
        raise TradingError('帳本商品月份不符，保持阻擋')
    if pending.get('contract_code', code) != code:
        raise TradingError('原委託月份與帳本不符，保持阻擋')
    trades = api.list_trades()
    if not isinstance(trades, list):
        raise TradingError('委託清單無效，保持阻擋')
    matches = [t for t in trades if shared._trade_id(t) == pending['trade_id']]
    if len(matches) != 1:
        raise TradingError('找不到唯一原委託，保持阻擋')
    trade = matches[0]
    shared._refresh_status(api, trade=trade)
    filled = shared._position_value(shared._position_value(trade, 'status'), 'deal_quantity')
    order = shared._position_value(trade, 'order')
    action = shared._position_value(order, 'action')
    action = str(getattr(action, 'value', action)).lower()
    if (shared._status_text(trade).lower() not in {'filled', 'cancelled'}
            or type(filled) is not int or filled != quantity
            or shared._position_code(shared._position_value(trade, 'contract')) != code
            or action != side or shared._position_value(order, 'quantity') != quantity):
        raise TradingError('原委託商品、方向或完整成交未確認，保持阻擋')
    positions = shared._read_positions(api)
    shared.validate_tmf_account(api, positions, contract_code=code)
    if shared._net_position(positions) != target:
        raise TradingError('已成交委託與目前庫存不一致，保持阻擋')
    result = shared.OrderResult(previous, target, target, side, quantity, trade)
    entry = (entry_record(result, payload['trigger'], datetime.now().isoformat())
             if phase in {'entry', 'add'} else None)
    if entry is not None and entry['fill_status'] != 'complete':
        raise TradingError('實際成交明細不足，保持阻擋')
    # Retain a recoverable copy before changing the durable strategy ledger.
    backup_path = Path(backup_path)
    if backup_path.exists():
        raise TradingError('備份已存在，請使用新的備份檔名')
    with closing(sqlite3.connect(backup_path)) as backup:
        store.db.backup(backup)
    resolved = dict(pending, status=shared._status_text(trade).lower(),
                    filled_quantity=quantity, actual_position=target)
    new_guard = {k: v for k, v in guard.items() if k != 'pending'}
    new_guard['last_resolved'] = resolved
    if phase == 'entry':
        state = State(expected=target, first_price=str(entry['entry_price']), round_key=payload['trigger'])
    elif phase == 'flatten':
        state = State(round_key=payload['trigger'])
    else:
        from dataclasses import replace
        state = replace(current_state, expected=target, added=True,
                        previous_guerrilla={}, sample_at={}, sample_value={})
    with store.db:
        # Refuse to overwrite any state changed since the initial snapshot.
        current = store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
        broker_row = store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
        if store.pending() != payload or current != state_row or broker_row != row:
            raise TradingError('核對期間帳本已變動，保持阻擋')
        store.db.execute('UPDATE pyramid_state SET symbol=?,payload=? WHERE id=1',
                         (code, json.dumps(asdict(state))))
        store.db.execute('UPDATE shioaji_order_state SET payload=? WHERE id=1', (json.dumps(new_guard),))
        store.db.execute('DELETE FROM guard WHERE id=1')
        store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
            (datetime.now(timezone.utc).isoformat(), 'pyramid_order_reconciled',
             json.dumps(dict(pending=payload, evidence=resolved, contract=code, entry=entry))))
    if entry is not None:
        append_entry(Path(records) / 'entries.csv', entry)
    return dict(contract=code, actual=target, trade_id=pending['trade_id'],
                entry_price=entry['entry_price'] if entry is not None else None)
