"""Shioaji API_KEY3 runs KGI's shared H3 base1/recovery2 strategy."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from filelock import FileLock

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / 'h3-capital-flatten-reentry-strategy'))
from h_core import Store, TradingError
from h3_recovery_sizing import RULE_TEXT, validate_inputs, watch
from ef_trade_runtime import Notifications
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
from recovery_broker import ShioajiRecoveryBroker
import auto_trade

SOURCE = BACKEND / 'telegram-relay-records/telegram_signal_events.jsonl'
STATE = BASE / 'runtime/state.json'


def check_legacy_state():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding='utf-8'))
    if (state.get('guard', {}).get('pending') or state.get('current_entry')
            or state.get('last_signal', {}).get('status') == 'pending'):
        raise TradingError('永豐舊版有持倉或未確認委託，請先核對；不自動遷移或平倉')


def read_config():
    if auto_trade.position_unit() != 1:
        raise TradingError('永豐 H3 固定1口起跑、上限2口；H_UNIT 須設為1')
    config = dict(signal_source=SOURCE, contract=SimpleNamespace(symbol='TMFR1'))
    if not SOURCE.is_file():
        raise TradingError('找不到本機 Relay 訊號檔')
    validate_inputs(config)
    check_legacy_state()
    if len(auto_trade.required('API_KEY3')) < 10:
        raise TradingError('API_KEY3 格式無效（少於10字元），請更新 backend-futures-py/.env')
    auto_trade.required('SECRET_KEY3')
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true', help='只檢查本地設定，不登入、不下單')
    args = parser.parse_args(argv)
    for line in (BACKEND / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    config = read_config()
    if args.check_config:
        print('API_KEY3 H3設定檢查通過；' + RULE_TEXT + '本次未登入、未送單。', flush=True)
        return
    runtime = BASE / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    with FileLock(str(runtime / 'monitor.lock'), timeout=0):
        store = Store(runtime / 'recovery.sqlite')
        notify = Notifications(lambda: os.getenv('DISCORD_H_STRATEGY_WEBHOOK_URL', ''),
                               BASE / 'records/notifications.jsonl')
        def labeled_notify(message):
            notify('【永豐｜API_KEY3】\n' + message)
        try:
            if store.pending():
                raise TradingError('永豐 H3 有未確認委託，請先核對；不重送')
            api = auto_trade.initialize_broker_session()
            watchdog = BrokerReconnectWatchdog(float(os.getenv('H3_REENTRY_RECONNECT_TIMEOUT_SECONDS', '120')))
            api.set_event_callback(watchdog.on_event)
            broker = ShioajiRecoveryBroker(api, auto_trade._sj, store, watchdog)
            config['contract'] = SimpleNamespace(symbol=auto_trade.shared._contract(api).code)
            watch(config, store, broker,
                  poll=max(0.5, float(os.getenv('H3_REENTRY_POLL_SECONDS', '2'))),
                  notify=labeled_notify)
        except Exception as exc:
            reason = str(exc) if type(exc) is TradingError else type(exc).__name__
            store.audit('stopped', dict(error=type(exc).__name__, reason=reason,
                                        pending_order=bool(store.pending())))
            labeled_notify(f'【H3 異常停止】{reason}；請核對庫存及委託，不重送。')
            raise
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
