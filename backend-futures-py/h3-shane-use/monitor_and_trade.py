"""Independent configured-unit H3 follower for Shane's Shioaji account."""
import argparse
import json
import os
from pathlib import Path
import time

import shane_bootstrap
from shane_bootstrap import BASE, BACKEND
import shane_account as account
from shane_account import ShaneBroker
from shane_runtime import ShaneEngine
from shane_status import StatusReporter, failure_status
from h_core import Store
from console_status import ConsoleStatus
from runtime import LedgerError, RolloverPause
from rollover import ledger_symbol
from auto_rollover import AutomaticRollover, ROLLOVER_PHASES
from ef_trade_runtime import Notifications
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
import shioaji_tmf_target as shared

SOURCE = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'
RUNTIME = BASE / 'runtime'
RECORDS = BASE / 'records'
STATE_PATH = RUNTIME / 'shane.sqlite'
LOCK = RUNTIME / 'monitor.lock'
ENV_PATH = BASE / '.env'


def load_env():
    if not ENV_PATH.is_file():
        raise ValueError('缺少 h3-shane-use/.env')
    allowed = {'API_KEY', 'SECRET_KEY', 'PERSON_ID', 'CA_PATH', 'SHANE_UNIT',
               'H3_SHANE_ACCOUNT_ID', 'H3_SHANE_ENABLE_ORDERS',
               'DISCORD_H3_SHANE_WEBHOOK_URL', 'H3_SHANE_POLL_SECONDS'}
    values = {}
    for line in ENV_PATH.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            key = key.strip()
            if key in allowed:
                values[key] = value.strip().strip('"').strip("'")
    # Never fall back to credentials exported by the host or the shared .env.
    for key in allowed:
        os.environ.pop(key, None)
    os.environ.update(values)


def check_config():
    account.credentials()
    account.position_unit()
    if not SOURCE.is_file():
        raise ValueError('缺少 H3 訊號檔：' + str(SOURCE))
    for key in ('API_KEY', 'API_KEY2', 'API_KEY3'):
        if key != 'API_KEY' and os.getenv(key) and os.getenv(key) == os.getenv('API_KEY'):
            raise ValueError('Shane 的 API_KEY 與既有永豐帳戶相同')


def watch(engine, poll):
    console = ConsoleStatus('Shane H3跟單', account.BROKER_SIMULATION)
    stat = SOURCE.stat()
    offset, identity = stat.st_size, stat.st_ino
    while not engine.broker.rollover.synchronize():
        engine.broker.check()
        time.sleep(poll)
    engine.validate_position(require_match=False)
    engine.notify(f'【Shane｜H3跟單啟動】simulation={account.BROKER_SIMULATION}；'
                  f'帳本部位 {engine.state().expected:+d} 口；'
                  f'每筆新H3多空訊號（含同方向）先按券商實際庫存全平，確認空手後再進 SHANE_UNIT={engine.broker.unit} 口；空手訊號只全平。')
    while True:
        engine.broker.check()
        if not engine.broker.rollover.synchronize():
            time.sleep(poll)
            continue
        console.heartbeat(engine)
        stat = SOURCE.stat()
        if stat.st_ino != identity or stat.st_size < offset:
            raise LedgerError('H3 訊號檔被替換或截斷，停止核對')
        with SOURCE.open('rb') as handle:
            handle.seek(offset)
            while True:
                before = offset
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
                    offset = before
                    break
        time.sleep(poll)


def run_session(store, notify, poll):
    try:
        pending = store.pending()
        rollover_pending = bool(pending and pending.get('phase') in ROLLOVER_PHASES)
        if pending and not rollover_pending:
            raise LedgerError('Shane 有未確認委託；須核對原委託，不重送')
        api = account.initialize()
        import shioaji as sj
        watchdog = BrokerReconnectWatchdog(120)
        api.set_event_callback(watchdog.on_event)
        broker = ShaneBroker(api, sj, store, watchdog, records=RECORDS,
                             allow_pending=rollover_pending)
        symbol = ledger_symbol(store, shared.tmf_contract_code(api))
        broker.contract_code = symbol
        engine = ShaneEngine(store, broker, symbol,
                             lambda text: notify(text.replace('【API_KEY｜H3游擊策略】', '【Shane｜H3跟單】')))
        broker.rollover = AutomaticRollover(engine, RUNTIME)
        watch(engine, poll)
    finally:
        account.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args(argv)
    load_env()
    check_config()
    if args.check_config:
        print('Shane H3 設定通過；未登入、未送單。', flush=True)
        return
    if os.getenv('H3_SHANE_ENABLE_ORDERS', '').lower() != 'true':
        raise ValueError('H3_SHANE_ENABLE_ORDERS 必須明確設為 true')
    from filelock import FileLock
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RECORDS.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK), timeout=0):
        store = Store(STATE_PATH)
        console = ConsoleStatus('Shane H3跟單', account.BROKER_SIMULATION)
        console.write('服務啟動，準備連線及核對；進入監控後每60秒輸出心跳。')
        notify = console.notifications(Notifications(lambda: os.getenv('DISCORD_H3_SHANE_WEBHOOK_URL', ''),
                               RECORDS / 'notifications.jsonl'))
        status = StatusReporter()
        try:
            while True:
                try:
                    run_session(store, notify, max(0.1, float(os.getenv('H3_SHANE_POLL_SECONDS', '0.25'))))
                except Exception as exc:
                    pending = bool(store.pending())
                    event, message = failure_status(
                        exc, simulation=account.BROKER_SIMULATION, pending=pending)
                    store.audit(event, dict(reason=message, pending=pending))
                    status.report(message)
                    time.sleep(60 if isinstance(exc, LedgerError) else 5)
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
