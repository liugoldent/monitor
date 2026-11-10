"""Cancel the strategy account's current-month TMF orders on a fresh H3 signal."""
import time

from h_core import TradingError
import shioaji_tmf_target as shared

TERMINAL = {'filled', 'cancelled', 'failed', 'inactive'}
CANCELLABLE = {'submitted', 'presubmitted', 'partfilled'}


def cancel_working_orders(broker, trigger, preflight, *, attempts=20, delay=0.25):
    label = broker.account_label

    def audit(event, **detail):
        broker.store.audit('h3_cancel_'+event, dict(account=label, trigger=trigger,
            contract=broker.contract_code, **detail))

    def check():
        broker.check()
        preflight()
        if broker.store.pending() or broker.guard.get('pending'):
            raise TradingError(f'{label} 有未確認策略委託，不自動取消掛單')

    def working():
        trades = broker.api.list_trades()
        if not isinstance(trades, list):
            raise TradingError(f'{label} 掛單清單無效，不送單')
        found = {}
        for trade in trades:
            if shared._position_code(shared._position_value(trade, 'contract')) != broker.contract_code:
                continue
            if shared._status_text(trade).lower() in TERMINAL:
                continue
            account = shared._position_value(shared._position_value(trade, 'order'), 'account')
            identity = tuple(shared._position_value(account, field) for field in ('broker_id', 'account_id'))
            expected = tuple(shared._position_value(broker.api.futopt_account, field) for field in ('broker_id', 'account_id'))
            if not all(identity) or not all(expected):
                raise TradingError(f'{label} 掛單帳戶不明，不自動取消')
            if identity != expected:
                continue
            key = shared._trade_id(trade)
            if not key or key in found or shared._status_text(trade).lower() not in CANCELLABLE:
                raise TradingError(f'{label} 掛單尚未確認可取消，不送單')
            found[key] = trade
        return found

    check()
    if not broker.contract_code or not broker.contract_code.startswith('TMF') or broker.contract_code in {'TMFR1', 'TMFR2'}:
        raise TradingError(f'{label} 策略月份未確認，不取消掛單')
    shared._refresh_status(broker.api)
    orders = working()  # Validate the full batch before cancelling any order.
    if not orders:
        return
    audit('requested', trade_ids=list(orders))
    for trade_id, trade in orders.items():
        check()
        try:
            broker.api.cancel_order(trade, timeout=5000)
        except Exception:
            audit('failed', trade_id=trade_id)
            raise TradingError(f'{label} 取消掛單失敗，本筆不送單') from None
    for _ in range(attempts):
        check()
        shared._refresh_status(broker.api)
        trades = broker.api.list_trades()
        if not isinstance(trades, list):
            raise TradingError(f'{label} 取消後委託清單無效，不送單')
        evidence = {}
        for key in orders:
            matches = [t for t in trades if shared._trade_id(t) == key]
            if len(matches) != 1:
                raise TradingError(f'{label} 取消後找不到唯一原委託，不送單')
            evidence[key] = shared._status_text(matches[0]).lower()
        # Filled during cancellation is also final; caller re-reads actual inventory.
        if all(status in TERMINAL for status in evidence.values()):
            if working():
                raise TradingError(f'{label} 取消期間新增掛單，本筆不送單')
            audit('confirmed', orders=evidence)
            return
        time.sleep(delay)
    audit('timeout', trade_ids=list(orders))
    raise TradingError(f'{label} 取消掛單未確認結束，本筆不送單')
