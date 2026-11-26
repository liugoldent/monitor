"""API_KEY H3 follow strategy with drawdown-based entry sizing."""
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
from h3_signal_retry import handle_signal, reconnect_broker
from console_status import ConsoleStatus
from runtime import LedgerError, RolloverPause
from portfolio_runtime import PortfolioEngine as Engine
from signal_journal import SignalJournal
from mdd_sizing import H3_ENTRY_RULE
from mdd_runtime import MddEngine
from net_positions import positions_initialized
from inventory import InventoryReporter
from ef_trade_runtime import Notifications, now_local, position_text
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
from broker import PyramidBroker
from reconcile import reconcile_filled_order
from rollover import ledger_symbol
from auto_rollover import AutomaticRollover, ROLLOVER_PHASES
import auto_trade

SOURCE = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'
H2_SOURCE = BACKEND / 'telegram-relay-records/telegram_signal_events.jsonl'
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
    ca = Path(os.getenv('CA_PATH') or BACKEND / 'Sinopac.pfx')
    if not ca.is_file():
        raise TradingError('找不到 API_KEY 憑證')
    auto_trade.required('PERSON_ID')
    for path in (SOURCE,):
        if not path.is_file():
            raise TradingError('找不到策略訊號／游擊／行情輸入檔：' + path.name)
    check_legacy()


def startup_notice(positions, unit):
    return ('【API_KEY｜H3／H2 跟單啟動｜實盤】\n'
            f'H3 {position_text(positions.h3)}｜基準 {unit} 口\n'
            f'H2 {position_text(positions.h2)}｜固定 {auto_trade.H2_UNIT} 口\n'
            f'帳戶淨部位 {position_text(positions.net)}\n{H3_ENTRY_RULE}')


def watch(engine, journals, poll=2):
    while engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
        engine.broker.check()
        time.sleep(poll)
    inventory = InventoryReporter(engine)
    inventory.startup(now_local())
    actual = engine.broker.startup_position()
    if getattr(engine, 'uses_strategy_positions', False):
        if actual != engine.strategy_positions().net:
            raise LedgerError('啟動時，券商庫存與 H3／H2 帳本不符，不送單')
        engine.notify(startup_notice(engine.strategy_positions(), engine.broker.unit))
    else:
        engine.reset_observations()
        engine.notify('【API_KEY｜H3 跟單啟動｜實盤】\n'
                      f'帳本 {position_text(engine.state().expected)}｜基準 {engine.broker.unit} 口\n'
                      f'{H3_ENTRY_RULE}')
    watch_loop(engine, journals, poll, inventory)


def watch_loop(engine, journals, poll, inventory):
    console = ConsoleStatus('永豐1 H3／H2')
    while True:
        engine.broker.check()
        if engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
            time.sleep(poll)
            continue
        console.heartbeat(engine)
        while True:
            available = [(journal, item) for journal in journals if (item := journal.peek())]
            if not available:
                break
            journal, (event, next_offset) = min(available, key=lambda pair: pair[0].arrival(pair[1][0]))
            try:
                console.received(event)
                handle_signal(engine, event)
            except RolloverPause:
                break
            journal.offset = next_offset
        inventory.daily(now_local())
        time.sleep(poll)


def run_session(store, notify, poll, journals, enable_h2):
    """One connected session. A retry never submits the uncertain order again."""
    try:
        check_legacy()
        if positions_initialized(store) and not enable_h2:
            raise LedgerError('已建立 H3／H2 帳本，須啟用 API_KEY_H2_ENABLED 才能恢復雙策略')
        api = auto_trade.initialize_broker_session()
        watchdog = BrokerReconnectWatchdog(float(os.getenv('H3_REENTRY_RECONNECT_TIMEOUT_SECONDS', '120')))
        api.set_event_callback(watchdog.on_event)
        rollover_pending = bool(store.pending() and store.pending().get('phase') in ROLLOVER_PHASES)
        if store.pending() and not rollover_pending:
            backup = RUNTIME / ('api_key.before_recovery_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite')
            evidence = reconcile_filled_order(store, api, backup, RECORDS, allow_rejected=True)
            if evidence.get('rejected'):
                reason = evidence['message'] or '券商委託已終止'
                notify(f'【API_KEY｜H3 回撤加口】原委託已確認零成交（{reason}），'
                       f'庫存 {evidence["actual"]:+d} 口與帳本一致，等待新訊號。')
            else:
                print('原委託成交與庫存已核對，未重送：' + json.dumps(evidence), flush=True)
        broker = PyramidBroker(api, auto_trade._sj, store, watchdog, records=RECORDS,
                               allow_pending=rollover_pending, close_all_months=not enable_h2)
        broker.reconnect_for_signal = lambda: reconnect_broker(
            broker, auto_trade.close_broker_session, auto_trade.initialize_broker_session)
        broker.unit = auto_trade.H3_UNIT
        symbol = ledger_symbol(store, auto_trade.shared.tmf_contract_code(api))
        broker.contract_code = symbol
        if enable_h2 and not positions_initialized(store):
            # Finish any persisted H3-only rollover using its original ledger
            # before assigning the confirmed post-roll H3 quantity to the H3 leg.
            legacy_engine = MddEngine(store, broker, symbol, notify)
            legacy_rollover = AutomaticRollover(legacy_engine, RUNTIME, follow_external_rollover=True)
            plan = legacy_rollover.plan()
            if plan and plan.get('stage') != 'done':
                legacy_rollover.synchronize()
            symbol = legacy_engine.symbol
        engine_type = Engine if enable_h2 else MddEngine
        engine = engine_type(store, broker, symbol, notify)
        broker.rollover = AutomaticRollover(engine, RUNTIME, follow_external_rollover=True)
        watch(engine, journals, poll)
    finally:
        auto_trade.close_broker_session()


def supervise(store, notify, poll, enable_h2=False):
    journals = (SignalJournal(SOURCE, 'h'),)
    if enable_h2:
        journals += (SignalJournal(H2_SOURCE, 'h2', optional=True),)
    last_failure = None
    last_notice_at = None
    retry = max(1, min(60, float(os.getenv('H3_RECOVERY_RETRY_SECONDS', '5'))))
    while True:
        try:
            run_session(store, notify, poll, journals, enable_h2)
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
                    notify('【API_KEY｜H3 回撤加口策略復原中】' + reason
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
    enable_h2 = os.getenv('API_KEY_H2_ENABLED', '').strip().lower() in {'1', 'true'}
    if args.check_config:
        print(f'API_KEY 雙策略：{"啟用" if enable_h2 else "未啟用"}')
        print(f'H3 基準 {auto_trade.H3_UNIT} 口｜H2 固定 {auto_trade.H2_UNIT} 口\n{H3_ENTRY_RULE}')
        print('API_KEY H3／H2 本機設定通過；本次未登入、未送單。', flush=True)
        return
    from filelock import FileLock
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK), timeout=0):
        check_legacy()
        store = Store(STATE_PATH)
        console = ConsoleStatus('永豐1 H3／H2')
        console.write('服務啟動，準備連線及核對；進入監控後每60秒輸出心跳。')
        notify = console.notifications(Notifications(lambda: os.getenv('DISCORD_H3_GUERRILLA_WEBHOOK_URL', ''),
                               RECORDS / 'notifications.jsonl'))
        try:
            supervise(store, notify, max(0.1, float(os.getenv('H3_REENTRY_POLL_SECONDS', '0.25'))),
                      enable_h2=enable_h2)
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
