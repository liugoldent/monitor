"""Watch fresh local Relay H signals; live execution is explicit and interactive."""
from __future__ import annotations
import argparse
import getpass
import json
import os
import time
from pathlib import Path
from h_core import Contract, Store, TradingError, execute_h, parse_signal
from local_env import load_env, selected_account

BASE = Path(__file__).resolve().parent


def read_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if config.get('entry_quantity') != 1:
        raise TradingError('這支 H 策略固定每次進場 1 口')
    source = Path(config['signal_source'])
    config['signal_source'] = source if source.is_absolute() else (BASE / source).resolve()
    config['contract'] = Contract(**config['contract'])
    return config


def diagnose(broker):
    from capital_broker import SK
    candidates = []
    for market in (2,):
        for group in SK.RequestStockList(market).AllTypeLists:
            for item in group.Items:
                if item.strStockName.startswith('微台') and item.strQuoteCode.startswith('TM'):
                    candidates.append(dict(market=market, name=item.strStockName,
                                           quote_code=item.strQuoteCode, order_code=item.strOrderCode,
                                           expiry=item.strExpiryDate))
    print('群益微型台指商品清單：')
    print(json.dumps(candidates, ensure_ascii=False, indent=2))
    (BASE / 'runtime' / 'contract_candidates.json').write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    result = SK.GetOpenInterestGW(broker.login_id, broker.account, 0)
    print('庫存查詢回傳碼：', result.StatusCode)
    print('庫存原始資料（含帳號，請勿直接分享）：', result.RawData)
    print('委託回補筆數：', len(broker.reports))
    inventory = []
    for record in result.RawData.rstrip('#').split('#'):
        fields = record.split(',')
        if len(fields) == 10:
            inventory.append({'market': fields[0], 'product': fields[2], 'side': fields[3],
                              'quantity': fields[4], 'day_quantity': fields[5]})
    (BASE / 'runtime' / 'diagnostic.json').write_text(json.dumps(
        {'inventory_status': result.StatusCode, 'inventory': inventory,
         'contracts': candidates, 'report_count': len(broker.reports)},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('此模式只查詢，未送出委託。')


def watch(config, store, broker=None, *, poll=0.5):
    source = config['signal_source']
    if not source.is_file():
        raise TradingError(f'找不到 Relay 訊號檔：{source}')
    stat = source.stat()
    offset, identity = stat.st_size, stat.st_ino
    print('已從訊號檔案結尾開始，等待新 H 訊號。', flush=True)
    while True:
        if broker:
            broker.check()
        stat = source.stat()
        if stat.st_ino != identity or stat.st_size < offset:
            # A replacement/truncation can contain historical messages: skip it.
            offset = stat.st_size
            identity = stat.st_ino
        with source.open('rb') as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line or not line.endswith(b'\n'):
                    break
                offset = handle.tell()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                signal = parse_signal(event)
                if signal is None or not store.consume(*signal):
                    continue
                key, direction, received = signal
                if broker:
                    store.audit('signal', {'key': key, 'direction': direction})
                    execute_h(broker, direction, store, key)
                    print(f'{key}：已確認先平倉，再進場 {direction:+d} 口', flush=True)
                else:
                    detail = {'key': key, 'direction': direction, 'entry_quantity': 1,
                              'rule': 'flatten_then_entry', 'send': False}
                    store.audit('paper_signal', detail)
                    print(f'預覽：{key} → 先平微台，再進場 {direction:+d}口（未送單）', flush=True)
        time.sleep(poll)


def main():
    parser = argparse.ArgumentParser(description='群益 H 微台先平倉再進場')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='正式委託；需本機登入及啟動確認')
    mode.add_argument('--diagnose', action='store_true', help='只查帳戶、微台商品清單與庫存')
    parser.add_argument('--config', default=str(BASE / 'h-config.json'))
    parser.add_argument('--service', action='store_true', help='Windows啟動腳本專用，使用.env、不互動')
    parser.add_argument('--check-config', action='store_true', help='只檢查本機設定，不登入、不送單')
    args = parser.parse_args()
    load_env(BASE / '.env')
    config = read_config(args.config)
    account = selected_account()
    if account is not None:
        config['account'] = account
    if args.service or args.check_config:
        if args.service and not args.live:
            raise TradingError('--service 必須與 --live 一起使用')
        if not all(os.getenv(k, '') for k in ('CAPITAL_LOGIN_ID', 'CAPITAL_PASSWORD')) or not account:
            raise TradingError('服務啟動需填妥登入資料及 CAPITAL_ACCOUNT_SELECT')
        config['contract'].validate()
        if not config['signal_source'].is_file():
            raise TradingError('找不到本機 Relay 訊號檔')
        if args.check_config:
            print('H3 Capital configuration OK; no login or order attempted.')
            return
    runtime = BASE / 'runtime'
    runtime.mkdir(exist_ok=True)
    import msvcrt
    lock_file = (runtime / ('live.lock' if args.live or args.diagnose else 'paper.lock')).open('a+b')
    lock_file.seek(0)
    if lock_file.read(1) == b'':
        lock_file.write(b'0')
        lock_file.flush()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    store = Store(runtime / ('live.sqlite' if args.live else 'diagnose.sqlite' if args.diagnose else 'paper.sqlite'))
    broker = None
    try:
        if args.live or args.diagnose:
            if args.live:
                config['contract'].validate()
            from capital_broker import CapitalBroker
            broker = CapitalBroker(config, store)
            user = os.getenv('CAPITAL_LOGIN_ID', '').strip() or input('群益登入 ID：').strip()
            password = os.getenv('CAPITAL_PASSWORD', '') or getpass.getpass('群益密碼（不顯示）：')
            try:
                broker.login(user, password, diagnose=args.diagnose)
            finally:
                password = None
            if args.diagnose:
                diagnose(broker)
                return
            c = config['contract']
            print(f'正式帳戶末碼 {broker.account[-4:]}；委託 {c.order_code} {c.settle_ym}；進場固定 1 口；市價 IOC。', flush=True)
            print('每筆浩克3新訊號：先平倉，完整成交及庫存確認後再新倉；啟動跳過歷史訊號。')
            if not args.service and input('本人確認啟動請輸入 START H：').strip() != 'START H':
                print('未啟動自動送單。')
                return
        watch(config, store, broker)
    except KeyboardInterrupt:
        print('已停止監控；停止不會自動平倉。')
    except Exception as exc:
        store.audit('stopped', {'error': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        if broker:
            broker.close()
        store.db.close()
        lock_file.close()


if __name__ == '__main__':
    os.chdir(BASE)
    main()
