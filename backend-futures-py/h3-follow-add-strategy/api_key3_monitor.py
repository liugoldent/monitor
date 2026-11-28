"""Run the isolated API_KEY3 conditional H2/H3 strategy, or check it offline."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
sys.path[:0] = [str(BASE), str(BACKEND / 'h3-common'), str(BACKEND)]

from api_key3_history import MarketPrices, TAIPEI, h2_history, h3_history
from api_key3_strategy import ConditionalPortfolio, active_h2, active_h3
from auto_rollover import AutomaticRollover, ROLLOVER_PHASES
from broker import PyramidBroker
from ef_trade_runtime import Notifications
from h3_signal_retry import handle_signal, reconnect_broker
from h_core import Store, TradingError
from reconcile import reconcile_filled_order
from rollover import ledger_symbol
from runtime import LedgerError, RolloverPause
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
from signal_journal import SignalJournal
import shioaji_tmf_target as shared

RUNTIME = BASE / 'runtime/api_key3'
RECORDS = BASE / 'records/api_key3'
SOURCE_H2 = BACKEND / 'telegram-relay-records/h2_signal_events.jsonl'
SOURCE_H3 = BACKEND / 'telegram-relay-records/h3_signal_events.jsonl'
HISTORY_H3 = BACKEND / 'tv_doc/h_trade.csv'
MARKET = BACKEND / 'tv_doc/webhook_data_1min.csv'


class Key3Broker(PyramidBroker):
    account_label = 'API_KEY3'

    def __init__(self, api, *args, **kwargs):
        self.verified_identity = account_identity(api)
        super().__init__(api, *args, **kwargs)

    def check(self):
        super().check()
        if account_identity(self.api) != self.verified_identity:
            raise LedgerError('API_KEY3 連線期貨帳號已變動，停止策略委託')


def load_env(path):
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def credentials():
    key, secret, person = (os.getenv(k, '').strip() for k in ('API_KEY3', 'SECRET_KEY3', 'PERSON_ID3'))
    if len(key) < 10 or not secret or not person:
        raise LedgerError('請設定 API_KEY3、SECRET_KEY3、PERSON_ID3')
    if key in (os.getenv('API_KEY'), os.getenv('API_KEY2')):
        raise LedgerError('API_KEY3 與另一個交易帳號使用相同金鑰')
    ca = Path(os.getenv('CA_PATH3') or os.getenv('CA_PATH') or BACKEND / 'Sinopac.pfx')
    if not ca.is_absolute():
        ca = BACKEND / ca
    if not ca.is_file():
        raise LedgerError('找不到永豐憑證，請檢查 CA_PATH3')
    return key, secret, person, ca


def expected_account():
    account = os.getenv('API_KEY3_ACCOUNT_ID', '').strip()
    if not account:
        raise LedgerError('請在 backend-futures-py/.env 設定 API_KEY3_ACCOUNT_ID，核對第三帳號後才登入')
    other_ids = ('API_KEY_ACCOUNT_ID', 'H3_PROTECTION_ACCOUNT_ID', 'H3_SHANE_ACCOUNT_ID')
    if account in {os.getenv(name, '').strip() for name in other_ids}:
        raise LedgerError('API_KEY3 期貨帳號與其他已設定的永豐策略重疊')
    return account


def account_identity(api):
    return tuple(shared._position_value(api.futopt_account, field) for field in ('broker_id', 'account_id'))


def connect(store, config):
    expected = expected_account()
    import shioaji as sj

    key, secret, person, ca = config
    api = sj.Shioaji(simulation=False)
    try:
        api.login(key, secret)
        broker_id, account = account_identity(api)
        if not account or not broker_id or str(account) != expected:
            raise LedgerError('永豐帳號與 API_KEY3_ACCOUNT_ID 不符，或沒有可用期貨帳號')
        identity = str(broker_id), str(account)
        api.activate_ca(ca_path=str(ca), ca_passwd=person, person_id=person)
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS api_key3_account '
                             '(id INTEGER PRIMARY KEY CHECK(id=1), broker TEXT, account TEXT)')
            row = store.db.execute('SELECT broker,account FROM api_key3_account WHERE id=1').fetchone()
            if row is not None and row != identity:
                raise LedgerError('永豐帳號與 API_KEY3 專用帳本不符')
            store.db.execute('INSERT OR IGNORE INTO api_key3_account VALUES (1,?,?)', identity)
    except Exception:
        logout(api)
        raise
    return api, sj


def logout(api):
    try:
        api.logout()
    except Exception:
        print('API_KEY3 舊連線登出失敗', flush=True)


def seed_path():
    configured = os.getenv('API_KEY3_H2_HISTORY')
    local = RUNTIME / 'h2.csv'
    return Path(configured) if configured else local if local.is_file() else Path.home() / 'Downloads/h2.csv'


def histories(cutoff, path):
    return {'h2': h2_history(path, cutoff), 'h3': h3_history(HISTORY_H3, cutoff)}


def show_history(curves):
    for name, curve in curves.items():
        at = datetime.fromtimestamp(curve.at, TAIPEI).isoformat()
        print(f'{name.upper()} 已回溯 {curve.closed} 筆｜回撤 {curve.drawdown:f}點｜'
              f'連輸 {curve.losses}筆｜虛擬方向 {curve.direction:+d}｜最後紀錄 {at}', flush=True)
    h2, h3 = curves['h2'], curves['h3']
    print(f'H2 條件 {"已啟動" if active_h2(h2, h3) else "待觸發"}｜'
          f'H3 條件 {"已啟動" if active_h3(h3) else "待觸發"}', flush=True)


def watch(engine, journals, poll):
    while True:
        engine.broker.check()
        if engine.broker.rollover.synchronize():
            while True:
                available = [(j, item) for j in journals if (item := j.peek()) is not None]
                if not available:
                    break
                journal, (event, next_offset) = min(available, key=lambda pair: (
                    SignalJournal.arrival(pair[1][0]), pair[0].route != 'h'))
                try:
                    handle_signal(engine, event)
                except RolloverPause:
                    break
                journal.offset = next_offset
        time.sleep(poll)


def run_session(store, notify, config, seeds, market, started_at, journals, poll):
    api, sj = connect(store, config)
    try:
        watchdog = BrokerReconnectWatchdog(float(os.getenv('H3_REENTRY_RECONNECT_TIMEOUT_SECONDS', '120')))
        api.set_event_callback(watchdog.on_event)
        pending = store.pending()
        rollover_pending = bool(pending and pending.get('phase') in ROLLOVER_PHASES)
        if pending and not rollover_pending:
            backup = RUNTIME / f'before_recovery_{time.time_ns()}.sqlite'
            reconcile_filled_order(store, api, backup, RECORDS, allow_rejected=True)
        broker = Key3Broker(api, sj, store, watchdog, records=RECORDS, allow_pending=rollover_pending)
        broker.unit = 1
        broker.reconnect_for_signal = lambda: reconnect_broker(
            broker, lambda: logout(broker.api), lambda: connect(store, config)[0])
        symbol = ledger_symbol(store, shared.tmf_contract_code(api))
        broker.contract_code = symbol
        engine = ConditionalPortfolio(store, broker, symbol, notify, seeds=seeds,
                                      market=market, started_at=started_at)
        broker.rollover = AutomaticRollover(engine, RUNTIME)
        actual = None if rollover_pending else broker.startup_position()
        show_history(engine.curves())
        target = engine.strategy_positions().net
        inventory = '換月原委託待核對' if actual is None else f'券商 {actual:+d}口'
        notify(f'【API_KEY3】已啟動 H2／H3 條件跟單｜策略目標 {target:+d}口｜{inventory}')
        watch(engine, journals, poll)
    finally:
        logout(broker.api if 'broker' in locals() else api)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true', help='回溯歷史並檢查本機設定；不登入')
    parser.add_argument('--h2-history', type=Path, help='TradingView H2 逐筆匯出 CSV')
    args = parser.parse_args(argv)
    load_env(BACKEND / '.env')
    config = credentials()
    started_at = time.time()
    h2_path = args.h2_history or seed_path()
    for path in (SOURCE_H2, SOURCE_H3, MARKET):
        if not path.is_file():
            raise LedgerError(f'缺少輸入檔：{path.name}')
    if args.check_config:
        show_history(histories(started_at, h2_path))
        try:
            expected_account()
        except LedgerError as exc:
            print(f'API_KEY3 歷史檢查完成，實盤設定待補：{exc}', flush=True)
        else:
            print('API_KEY3 本機設定及回溯檢查通過，未登入永豐。', flush=True)
        return

    from filelock import FileLock

    RUNTIME.mkdir(parents=True, exist_ok=True)
    with FileLock(str(RUNTIME / 'monitor.lock'), timeout=0):
        store = Store(RUNTIME / 'api_key3.sqlite')
        try:
            exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='api_key3_curves'").fetchone()
            seeded = exists and store.db.execute('SELECT 1 FROM api_key3_curves WHERE id=1').fetchone()
            seeds = None if seeded else histories(started_at, h2_path)
            notify = Notifications(lambda: os.getenv('DISCORD_INCREASE_INVESTMENT', ''),
                                   RECORDS / 'notifications.jsonl')
            market = MarketPrices(MARKET)
            journals = (SignalJournal(SOURCE_H3, 'h', replay=True), SignalJournal(SOURCE_H2, 'h2', replay=True))
            poll = max(.1, float(os.getenv('H3_REENTRY_POLL_SECONDS', '0.25')))
            last_notice, last_time = None, 0
            while True:
                try:
                    run_session(store, notify, config, seeds, market, started_at, journals, poll)
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, TradingError) else type(exc).__name__
                    now = time.monotonic()
                    if reason != last_notice or now - last_time >= 300:
                        notify(f'【API_KEY3】等待核對：{reason}')
                        last_notice, last_time = reason, now
                    time.sleep(5)
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
