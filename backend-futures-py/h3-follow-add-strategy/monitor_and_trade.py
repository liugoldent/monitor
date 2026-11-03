"""API_KEY: follow fresh H3 with H_UNIT; add one at profit >=500 and a crossing."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
LEGACY = BACKEND / 'h3-flatten-reentry-strategy'
sys.path.insert(0, str(BACKEND / 'h3-common'))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BASE))

from h_core import Store, TradingError
from runtime import Engine, LedgerError, RolloverPause
from inventory import InventoryReporter
from feeds import CsvTail, timestamp, fresh, current_price
from strategy import number
from ef_trade_runtime import Notifications, now_local, position_text
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
from broker import PyramidBroker
from reconcile import reconcile_filled_order
from rollover import ledger_symbol
from auto_rollover import AutomaticRollover, ROLLOVER_PHASES
import auto_trade
from goodway_guerrilla import GOODWAY_GUERRILLA_SYMBOLS, fetch_goodway_history

SOURCE = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'
PRICES = BACKEND / 'tv_doc/webhook_data_1min.csv'
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
    for path in (SOURCE, PRICES):
        if not path.is_file():
            raise TradingError('找不到策略訊號／游擊／行情輸入檔：' + path.name)
    check_legacy()


def poll_guerrilla(engine, ticker, history, prices, offset):
    """Only the newest live bar/revision is actionable; no history replay."""
    if SOURCE.stat().st_size != offset:
        return  # Let the next loop process all newly arrived H3 events first.
    now = now_local()
    rollover = engine.broker.rollover
    if isinstance(rollover, AutomaticRollover):
        plan = rollover.plan()
        if plan and plan['stage'] == 'done' and plan['new'] == engine.symbol:
            boundary = rollover.close_deadline(plan['old'])
            if now.date() == boundary.date():
                try:
                    ready = now >= boundary and timestamp(prices.latest['TradingView Time']) >= boundary
                except (TypeError, KeyError, ValueError):
                    ready = False
                if not ready:
                    # MXF1! may still quote the expiring month until 13:30.
                    # Exits remain enabled; never add using the old-month price.
                    engine.reset_observations(ticker)
                    return
    if not history:
        engine.reset_observations(ticker)
        return
    latest = max(history)
    try:
        stamp = datetime.fromtimestamp(latest / 1000, ZoneInfo('Asia/Taipei')).replace(tzinfo=None)
        value = number(history[latest]['high'])
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        engine.reset_observations(ticker)
        return
    if not fresh(stamp, now):
        engine.reset_observations(ticker)
        return
    state = engine.state()
    previous_at = state.sample_at.get(ticker)
    if previous_at and (stamp - timestamp(previous_at)).total_seconds() > 180:
        engine.reset_observations(ticker)
    price = current_price(prices.latest, now)
    def preflight():
        check_now = now_local()
        if not fresh(stamp, check_now) or current_price(prices.latest, check_now) is None:
            raise TradingError('加碼行情或游擊已過期，不追單')
    engine.store.audit('guerrilla_observed', dict(ticker=ticker, bar_at=stamp.isoformat(),
                       high=str(value), price=str(price) if price is not None else None))
    engine.on_sample(ticker, value, stamp.isoformat(), price, preflight)


def startup_notice(state, unit):
    return ('【API_KEY｜H3游擊策略啟動】\n'
            f'帳本部位：{position_text(state.expected)}；本輪已加碼：{"是" if state.added else "否"}。\n'
            '沿用已保存部位與加碼紀錄，不補歷史訊號；換月窗口內依保存進度自動換倉。\n'
            '結算日13:25自動先平舊月、再以同方向同口數進次月；無須手動換倉。\n'
            f'每筆新H3多空訊號（含同方向）先按券商實際庫存全平，確認空手後再進H_UNIT={unit}口；空手訊號只全平。\n'
            '加碼：第一口當下浮盈≥500點＋股威近月或次月游擊high穿越；'
            '多單向上穿越−1250，空單向下穿越＋1250；每輪僅加1口。')


def watch(engine, poll=2):
    # Capture the live cursor before processing rollover, so signals
    # arriving during the wait still receive the normal 30-second age check.
    stat = SOURCE.stat()
    offset, identity = stat.st_size, stat.st_ino
    prices = CsvTail(PRICES, latest_only=True)
    # Historical observations are deliberately not actionable on startup.
    prices.read()
    while engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
        engine.broker.check()
        time.sleep(poll)
    inventory = InventoryReporter(engine)
    inventory.startup(now_local())
    engine.validate_position(require_match=False)
    engine.reset_observations()
    engine.notify(startup_notice(engine.state(), engine.broker.unit))
    # API polling is asynchronous so an eight-second HTTP timeout cannot delay H3 exits.
    with ThreadPoolExecutor(max_workers=2) as executor:
        watch_loop(engine, prices, offset, identity, executor, poll, inventory)


def watch_loop(engine, prices, offset, identity, executor, poll, inventory):
    pending = {}
    next_poll = 0
    feed_interval = max(5, float(os.getenv('H3_GUERRILLA_POLL_SECONDS', '30')))
    following_live_round = bool((engine.state().round_key or '').startswith(('manual-roll:', 'auto-roll:')))
    while True:
        engine.broker.check()
        previous_symbol = engine.symbol
        if engine.broker.rollover is not None and not engine.broker.rollover.synchronize():
            time.sleep(poll)
            continue
        if engine.symbol != previous_symbol:
            following_live_round = True
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
                    if engine.on_signal(event):
                        following_live_round = True
                except RolloverPause:
                    offset = previous_offset
                    break  # The next loop processes automatic rollover first.
        inventory.daily(now_local())
        prices.read()
        for ticker, future in list(pending.items()):
            if not future.done():
                continue
            del pending[ticker]
            try:
                history = future.result()
            except Exception as exc:
                engine.reset_observations(ticker)
                engine.store.audit('guerrilla_fetch_failed', dict(ticker=ticker, error=type(exc).__name__))
                continue
            if following_live_round:
                try:
                    poll_guerrilla(engine, ticker, history, prices, offset)
                except RolloverPause:
                    pass
        if time.monotonic() >= next_poll:
            for ticker in GOODWAY_GUERRILLA_SYMBOLS:
                if ticker not in pending:
                    pending[ticker] = executor.submit(fetch_goodway_history, ticker,
                                                      datetime.now(ZoneInfo('Asia/Taipei')))
            next_poll = time.monotonic() + feed_interval
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
                    notify('【API_KEY｜H3游擊策略復原中】' + reason
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
        print('API_KEY H3游擊策略本機設定通過；本次未登入、未送單。', flush=True)
        return
    from filelock import FileLock
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK), timeout=0):
        check_legacy()
        store = Store(STATE_PATH)
        notify = Notifications(lambda: os.getenv('DISCORD_H3_GUERRILLA_WEBHOOK_URL', ''),
                               RECORDS / 'notifications.jsonl')
        try:
            supervise(store, notify, max(0.1, float(os.getenv('H3_REENTRY_POLL_SECONDS', '0.25'))))
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
