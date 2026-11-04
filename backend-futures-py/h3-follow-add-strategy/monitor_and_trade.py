"""API_KEY: historical H3 drawdown over 1000 points uses Unit+1 until recovery."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
LEGACY = BACKEND / 'h3-flatten-reentry-strategy'
sys.path.insert(0, str(BACKEND / 'h3-common'))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BASE))

from h_core import Store, TradingError
from console_status import ConsoleStatus
from runtime import LedgerError, RolloverPause
from mdd_runtime import MddEngine as Engine, historical_mdd
from inventory import InventoryReporter
from ef_trade_runtime import Notifications, now_local, position_text
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
from broker import PyramidBroker
from reconcile import reconcile_filled_order
from rollover import ledger_symbol
from auto_rollover import AutomaticRollover, ROLLOVER_PHASES
import auto_trade

SOURCE = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'
RUNTIME = BASE / 'runtime'
STATE_PATH = RUNTIME / 'api_key.sqlite'
RECORDS = BASE / 'records/api_key'
# Keep the migration lock; the account-one ledger never reuses account-three state.
LOCK = LEGACY / 'runtime/monitor.lock'


def load_env():
    path = BACKEND / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def check_legacy():
    old = LEGACY / 'runtime/state.json'
    if old.is_file():
        data = json.loads(old.read_text(encoding='utf-8'))
        if (data.get('guard', {}).get('pending') or data.get('current_entry')
                or data.get('last_signal', {}).get('status') == 'pending'):
            raise TradingError('舊H3策略仍有持倉或待核對委託，不自動接管')
    old_db = LEGACY / 'runtime/recovery.sqlite'
    if old_db.is_file():
        db = sqlite3.connect(old_db.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'guard' in tables and db.execute('SELECT 1 FROM guard').fetchone():
                raise TradingError('舊H3策略存在未確認委託')
            for table in ('h3_recovery_state', 'shioaji_order_state'):
                if table in tables:
                    for (payload,) in db.execute(f'SELECT payload FROM {table}'):
                        state = json.loads(payload)
                        if state.get('expected') or state.get('pending'):
                            raise TradingError('舊H3策略帳本仍有部位或未確認委託，請先核對')
        finally:
            db.close()


def check_config():
    if len(auto_trade.required('API_KEY')) < 10:
        raise TradingError('API_KEY 格式無效（少於10字元）')
    auto_trade.required('SECRET_KEY')
    auto_trade.position_unit()
    ca = Path(os.getenv('CA_PATH') or BACKEND / 'Sinopac.pfx')
    if not ca.is_file():
        raise TradingError('找不到 API_KEY 憑證')
    auto_trade.required('PERSON_ID')
    for path in (SOURCE,):
        if not path.is_file():
            raise TradingError('找不到策略訊號／游擊／行情輸入檔：' + path.name)
    check_legacy()


def startup_notice(state, unit):
    return ('【API_KEY｜H3 MDD加口策略啟動】\n'
            f'帳本部位：{position_text(state.expected)}；Unit={unit}。\n'
            '每筆新H3訊號先全平，再依MDD狀態進場；空手訊號只全平。\n'
            '每次進場讀取 h_trade.csv，以完整H3輪次的單口毛點數計算回撤。\n'
            '回撤超過1000點用Unit+1，維持到回前高；結算日13:25按原口數自動換月。')


def watch(engine, poll=2):
    # Capture the live cursor before processing rollover, so signals
    # arriving during the wait still receive the normal 30-second age check.
    stat = SOURCE.stat()
    offset, identity = stat.st_size, stat.st_ino
    while engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
        engine.broker.check()
        time.sleep(poll)
    inventory = InventoryReporter(engine)
    inventory.startup(now_local())
    engine.validate_position(require_match=False)
    engine.reset_observations()
    engine.notify(startup_notice(engine.state(), engine.broker.unit))
    watch_loop(engine, offset, identity, poll, inventory)


def watch_loop(engine, offset, identity, poll, inventory):
    console = ConsoleStatus('永豐1 H3 MDD加口')
    while True:
        engine.broker.check()
        if engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
            time.sleep(poll)
            continue
        console.heartbeat(engine)
        stat = SOURCE.stat()
        if stat.st_ino != identity or stat.st_size < offset:
            raise TradingError('Relay訊號檔被替換或截斷，停止核對')
        with SOURCE.open('rb') as handle:
            handle.seek(offset)
            while True:
                previous_offset = offset
                raw = handle.readline()
                if not raw or not raw.endswith(b'\n'):
                    break
                offset = handle.tell()
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                try:
                    console.received(event)
                    engine.on_signal(event)
                except RolloverPause:
                    offset = previous_offset
                    break  # The next loop processes automatic rollover first.
        inventory.daily(now_local())
        time.sleep(poll)


def run_session(store, notify, poll):
    """One connected session. A retry never submits the uncertain order again."""
    try:
        check_legacy()
        api = auto_trade.initialize_broker_session()
        watchdog = BrokerReconnectWatchdog(float(os.getenv('H3_REENTRY_RECONNECT_TIMEOUT_SECONDS', '120')))
        api.set_event_callback(watchdog.on_event)
        rollover_pending = bool(store.pending() and store.pending().get('phase') in ROLLOVER_PHASES)
        if store.pending() and not rollover_pending:
            backup = RUNTIME / ('api_key.before_recovery_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite')
            evidence = reconcile_filled_order(store, api, backup, RECORDS)
            print('原委託成交與庫存已核對，未重送：' + json.dumps(evidence), flush=True)
        broker = PyramidBroker(api, auto_trade._sj, store, watchdog, records=RECORDS,
                               allow_pending=rollover_pending)
        broker.unit = auto_trade.position_unit()
        symbol = ledger_symbol(store, auto_trade.shared.tmf_contract_code(api))
        broker.contract_code = symbol
        engine = Engine(store, broker, symbol, notify)
        broker.rollover = AutomaticRollover(engine, RUNTIME)
        watch(engine, poll)
    finally:
        auto_trade.close_broker_session()


def supervise(store, notify, poll):
    """Keep the process alive while reconnecting or waiting for reconciliation."""
    last_failure = None
    last_notice_at = None
    retry = max(1, min(60, float(os.getenv('H3_RECOVERY_RETRY_SECONDS', '5'))))
    while True:
        try:
            run_session(store, notify, poll)
            raise RuntimeError('監控迴圈意外結束')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (TradingError, auto_trade.shared.BrokerOrderError)) else type(exc).__name__
            retry_seconds = max(60, retry) if isinstance(exc, LedgerError) else retry
            pending = bool(store.pending())
            failure = (reason, pending)
            notice_at = time.monotonic()
            if failure != last_failure or last_notice_at is None or notice_at - last_notice_at >= 300:
                store.audit('pyramid_recovering', dict(reason=reason, pending=pending, retry_seconds=retry_seconds))
                try:
                    notify('【API_KEY｜H3 MDD加口策略復原中】' + reason
                           + f'；程序仍在，{retry_seconds:g}秒後重新連線核對；未確認委託不重送。')
                except Exception:
                    print('復原通知失敗，服務仍繼續重試', flush=True)
                last_failure = failure
                last_notice_at = notice_at
                print(f'H3等待核對：{reason}；{retry_seconds:g}秒後重新核對，pending={pending}', flush=True)
            time.sleep(retry_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true', help='只檢查本機設定，不登入、不下單')
    args = parser.parse_args(argv)
    load_env()
    check_config()
    if args.check_config:
        mdd = historical_mdd()
        print(f'H3 歷史回撤 {mdd.drawdown:g} 點；進場 {auto_trade.position_unit() + int(mdd.boosted)} 口。')
        print('API_KEY H3 MDD加口策略本機設定通過；本次未登入、未送單。', flush=True)
        return
    from filelock import FileLock
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK), timeout=0):
        check_legacy()
        store = Store(STATE_PATH)
        console = ConsoleStatus('永豐1 H3 MDD加口')
        console.write('服務啟動，準備連線及核對；進入監控後每60秒輸出心跳。')
        notify = console.notifications(Notifications(lambda: os.getenv('DISCORD_H3_GUERRILLA_WEBHOOK_URL', ''),
                               RECORDS / 'notifications.jsonl'))
        try:
            supervise(store, notify, max(0.1, float(os.getenv('H3_REENTRY_POLL_SECONDS', '0.25'))))
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
