"""Independent KGI H3 service; default is paper, --diagnose cannot send orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from shared_h import Store, TradingError, load_env
from kgi_broker import Contract, KgiBroker
from mdd_sizing import Discord, number, read_webhook, watch

BASE = Path(__file__).resolve().parent


def read_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    # B/S selects direction only; this policy is fixed to base 1 / boosted 2.
    config['entry_quantity'] = read_unit()
    if config['entry_quantity'] != 1:
        raise TradingError('回撤加口版本固定1口起跑，請將 KGI_UNIT 設為1；達3%後自動2口')
    config['mdd_sizing'] = True
    config['mdd_one_way_cost'] = str(number(os.getenv('KGI_MDD_ONE_WAY_COST_TWD', '24')))
    source = Path(config['signal_source'])
    config['signal_source'] = source if source.is_absolute() else (path.parent / source).resolve()
    config['contract'] = Contract(**config['contract'])
    config['contract'].validate()
    timeout = config.get('confirmation_timeout', 20)
    if type(timeout) not in (int, float) or not 5 <= timeout <= 60:
        raise TradingError('confirmation_timeout 須為 5 至 60 秒')
    config['confirmation_timeout'] = timeout
    if not config['signal_source'].is_file():
        raise TradingError('找不到本機 Relay 訊號檔')
    return config


def read_unit():
    value = os.getenv('KGI_UNIT', '').strip()
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise TradingError('KGI_UNIT 須填大於零的整數口數，例如 1、2、3')
    return int(value)


def credentials():
    keys = ('KGI_LOGIN_ID', 'KGI_PASSWORD', 'KGI_ACCOUNT', 'KGI_BROKER_ID')
    values = [os.getenv(key, '') if key == 'KGI_PASSWORD' else os.getenv(key, '').strip() for key in keys]
    if not all(values):
        raise TradingError('請在本目錄 .env 填妥 KGI_LOGIN_ID、KGI_PASSWORD、KGI_ACCOUNT、KGI_BROKER_ID')
    simulation = os.getenv('KGI_SIMULATION', '').strip().lower()
    if simulation not in ('true', 'false'):
        raise TradingError('KGI_SIMULATION 須明確填 true 或 false')
    return (*values, simulation == 'true')


def account_key(account, broker_id, simulation):
    identity = f'{simulation}:{broker_id}:{account}'.encode('utf-8')
    return ('test-' if simulation else 'production-') + hashlib.sha256(identity).hexdigest()[:16]


def main(argv=None):
    parser = argparse.ArgumentParser(description='凱基 H3：1口跟單，回撤3%後2口，歸零後1口；預設只預覽')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='登入並依新訊號送單')
    mode.add_argument('--diagnose', action='store_true', help='登入、查詢帳戶與庫存，不下單')
    parser.add_argument('--service', action='store_true', help='與 --live 一起使用，不互動')
    parser.add_argument('--check-config', action='store_true', help='只檢查本地設定，不登入')
    parser.add_argument('--config', default=str(BASE / 'h-config.json'))
    args = parser.parse_args(argv)
    if args.service and not args.live:
        raise TradingError('--service 必須與 --live 一起使用')
    load_env(BASE / '.env')
    config = read_config(args.config)
    if not args.diagnose:
        config['notification_url'] = read_webhook(BASE.parent / '.env')
    creds = credentials() if args.live or args.diagnose or args.check_config else None
    if args.check_config:
        print('H3 KGI configuration OK; 1->2 contracts at 3% closed-equity drawdown; Discord key configured; no login or order attempted.')
        return
    if args.live and not args.service:
        environment = '測試' if creds[-1] else '正式'
        print(f'凱基{environment}環境，帳號末碼 {creds[2][-4:]}，{config["contract"].symbol}，基礎1口／回撤3%後2口，市價 IOC。')
        if input('確認開始依 H3 新訊號送單，請輸入 START KGI H：').strip() != 'START KGI H':
            print('未啟動。')
            return
    runtime = BASE / 'runtime'
    runtime.mkdir(exist_ok=True)
    # One independent guard per broker/account/environment; diagnose shares the live lock.
    key = account_key(creds[2], creds[3], creds[4]) if creds else 'paper'
    import msvcrt
    lock_file = (runtime / f'{key}.lock').open('a+b')
    store = broker = None
    previous_directory = Path.cwd()
    try:
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        store = Store(runtime / f'{key}.sqlite')
        if args.live and store.pending():
            raise TradingError('有未確認委託，請先人工核對凱基庫存與委託；不得刪除狀態重送')
        if creds:
            # SDK writes certificates/product/token files relative to its import CWD.
            # Keep all such private artifacts under the ignored, account-specific runtime.
            sdk_directory = runtime / f'{key}-sdk'
            sdk_directory.mkdir(exist_ok=True)
            os.chdir(sdk_directory)
            broker = KgiBroker(config, store)
            broker.login(*creds)
            if args.diagnose:
                print(f'凱基帳戶末碼 {broker.account[-4:]}；{broker.contract.symbol} 淨庫存 {broker.position():+d} 口。')
                print(f'未確認委託阻擋：{bool(store.pending())}；此模式未送單。')
                return
            print('凱基 H3 已登入；回撤加口規則：1口起跑、達3%後2口、回撤歸零後1口；啟動跳過歷史訊號。', flush=True)
        watch(config, store, broker)
    except KeyboardInterrupt:
        print('已停止監控；不會自動平倉或撤單。')
        if broker and config.get('notification_url'):
            Discord(config['notification_url'], store)('【凱基 H3 回撤加口｜停止】\n手動停止監控；未自動平倉或撤單。')
    except Exception as exc:
        if store:
            # Vendor exceptions may contain account/credential payloads; keep only their type.
            store.audit('stopped', {'error': type(exc).__name__})
            if broker and config.get('notification_url'):
                Discord(config['notification_url'], store)(f'【凱基 H3 回撤加口｜異常停止】\n錯誤類型：{type(exc).__name__}。請核對庫存、委託及本機紀錄；不重送委託。')
        raise
    finally:
        try:
            if broker:
                broker.close()
        finally:
            os.chdir(previous_directory)
            if store:
                store.db.close()
            lock_file.close()


if __name__ == '__main__':
    os.chdir(BASE)
    main()
