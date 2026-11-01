"""Account-two single-contract H3 following monitor; no price-based exits."""
import argparse
import json
import os
import time

import protection_bootstrap
from protection_bootstrap import BASE, BACKEND
from protection_account import ProtectionBroker
import protection_account as account
from protection_runtime import ProtectionEngine
from protection_rollover import ProtectionRollover
from h_core import Store, TradingError
from runtime import LedgerError, RolloverPause
from rollover import ledger_symbol
from auto_rollover import ROLLOVER_PHASES
from ef_trade_runtime import Notifications
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog

SOURCE = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'


def mode_paths(simulation):
    mode = 'simulation' if simulation else 'live'
    return (BASE / 'runtime' / mode, BASE / 'records' / mode,
            BASE / 'runtime' / mode / f'api_key2.{mode}.sqlite')


RUNTIME, RECORDS, STATE_PATH = mode_paths(account.BROKER_SIMULATION)
# Same lock as the previous account-two strategy. Never run both concurrently.
LEGACY = BACKEND / 'ef-morning-weekend-hedge-strategy'
LOCK = LEGACY / 'runtime/monitor.lock'
WEBHOOK_ENV = 'DISCORD_H3_TP_WEBHOOK_URL'


def load_env():
    path = BACKEND / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def check_legacy():
    # Never overwrite or assume ownership of the old account-two book.
    for path in (LEGACY / 'runtime/state.json', LEGACY / 'runtime/guard.json'):
        if path.is_file():
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if value:
                raise LedgerError('永豐2仍有舊EF狀態；須核對並封存後才能啟用新H3策略')
    old = LEGACY / 'runtime/live_state.json'
    if old.is_file():
        value = json.loads(old.read_text(encoding='utf-8-sig'))
        attempt = value.get('attempt') or {}
        if (value.get('position') != 0 or value.get('target') != 0
                or attempt.get('status') not in {None, 'no_order_needed', 'confirmed'}):
            raise LedgerError('永豐2舊EF帳本有部位或未確認委託，須先核對')
    # Legacy runtime files differ by version; unknown databases also require review.
    if (LEGACY / 'runtime').exists():
        for path in (LEGACY / 'runtime').glob('*.sqlite*'):
            if path.stat().st_size:
                raise LedgerError('永豐2仍有舊EF資料庫，先核對封存，不自動接管')


def check_config():
    account.credentials()
    account.position_unit()
    account.required('H3_PROTECTION_ACCOUNT_ID')
    webhook = account.required(WEBHOOK_ENV)
    if not webhook.startswith('https://'):
        raise ValueError('DISCORD_H3_TP_WEBHOOK_URL 須為 HTTPS')
    for key in ('API_KEY', 'API_KEY3'):
        if os.getenv(key) and os.getenv(key) == os.getenv('API_KEY2'):
            raise ValueError('API_KEY2 與其他帳戶金鑰相同，停止設定檢查')
    if not SOURCE.is_file():
        raise ValueError('缺少輸入檔：'+SOURCE.name)
    check_legacy()


def startup_notice(engine):
    state = engine.state()
    return ('【永豐2｜H3跟單啟動】\n'
        f'模式：Shioaji simulation={account.BROKER_SIMULATION}（{"模擬委託" if account.BROKER_SIMULATION else "真實下單"}）。\n'
        f'核對部位{state.expected:+d}口。\n'
        f'每輪跟H3進場{engine.broker.unit}口（H_UNIT_KEY2），不加碼；H3平倉就全平，反向先全平再依設定口數進場。\n'
        '已移除浮盈500點／回吐30%自動平倉及同輪停利鎖限制。\n'
        '尚未設定日K均線出場；重啟不補歷史訊號、不接管舊倉；'
        '結算日沿用13:25自動換月。')


def watch(engine, poll=2):
    stat = SOURCE.stat()
    offset, identity = stat.st_size, stat.st_ino
    while not engine.broker.rollover.synchronize():
        engine.broker.check()
        time.sleep(poll)
    engine.validate_position()
    notice = startup_notice(engine)
    print(notice, flush=True)
    engine.notify(notice)
    while True:
        engine.broker.check()
        if not engine.broker.rollover.synchronize():
            time.sleep(poll)
            continue
        stat = SOURCE.stat()
        if stat.st_ino != identity or stat.st_size < offset:
            raise TradingError('Relay訊號檔被替換或截斷，停止核對')
        with SOURCE.open('rb') as f:
            f.seek(offset)
            while True:
                before = offset
                raw = f.readline()
                if not raw or not raw.endswith(b'\n'):
                    break
                offset = f.tell()
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                try:
                    engine.on_signal(event)
                except RolloverPause:
                    offset = before
                    break
        time.sleep(poll)


def run_session(store, notify, poll):
    try:
        check_legacy()
        pending = store.pending()
        roll_pending = bool(pending and pending.get('phase') in ROLLOVER_PHASES)
        if pending and not roll_pending:
            raise LedgerError('永豐2有未確認委託；保留原委託與停利計畫，須核對，不重送')
        api, sj = account.initialize()
        watchdog = BrokerReconnectWatchdog(120)
        api.set_event_callback(watchdog.on_event)
        broker = ProtectionBroker(api, sj, store, watchdog, records=RECORDS,
                                  allow_pending=roll_pending)
        symbol = ledger_symbol(store, account.shared.tmf_contract_code(api))
        broker.contract_code = symbol
        engine = ProtectionEngine(store, broker, symbol, notify)
        broker.rollover = ProtectionRollover(engine, RUNTIME)
        watch(engine, poll)
    finally:
        account.close()


def supervise(store, notify, poll):
    last, last_at = None, 0
    while True:
        try:
            run_session(store, notify, poll)
            raise RuntimeError('監控迴圈意外結束')
        except Exception as exc:
            # Do not put provider exceptions (possibly credentials) in notices.
            reason = str(exc) if isinstance(exc, TradingError) else type(exc).__name__
            delay = 60 if isinstance(exc, LedgerError) else 5
            if reason != last or time.monotonic()-last_at >= 300:
                store.audit('protection_recovering', dict(reason=reason, pending=bool(store.pending())))
                try:
                    notify('【永豐2｜H3跟單異常】'+reason+
                           f'；{delay}秒後重新核對，未確認委託不重送。')
                except Exception:
                    print('永豐2異常通知失敗，保留原始帳本', flush=True)
                print('永豐2等待核對：'+reason, flush=True)
                last, last_at = reason, time.monotonic()
            time.sleep(delay)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args(argv)
    load_env()
    check_config()
    if args.check_config:
        print(f'永豐2 H3跟單設定通過；simulation={account.BROKER_SIMULATION}；通知使用 DISCORD_H3_TP_WEBHOOK_URL；未登入、未送單。')
        return
    if os.getenv('H3_PROTECTION_ENABLE_ORDERS', '').lower() != 'true':
        raise ValueError('尚未啟用永豐2委託：H3_PROTECTION_ENABLE_ORDERS 必須明確設為 true')
    from filelock import FileLock
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RECORDS.mkdir(parents=True, exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK), timeout=0):
        check_legacy()
        store = Store(STATE_PATH)
        notify = Notifications(lambda: os.getenv(WEBHOOK_ENV, ''), RECORDS/'notifications.jsonl')
        try:
            supervise(store, notify, max(0.1, float(os.getenv('H3_PROTECTION_POLL_SECONDS', '0.25'))))
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
