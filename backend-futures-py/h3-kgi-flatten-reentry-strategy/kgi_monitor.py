"""Independent KGI H3 service; default is paper, --diagnose cannot send orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from shared_h import Store, TradingError, load_env
from kgi_broker import Contract, KgiBroker
from mdd_sizing import (Discord, MddPolicy, manual_production_buy_one, number, read_webhook,
                        startup_simulation_buy_one, watch as legacy_watch)

BASE = Path(__file__).resolve().parent


def watch(config, store, broker=None, *, poll=0.5):
    if config.get('recovery_sizing'):
        from h3_recovery_sizing import watch as recovery_watch
        notify = Discord(config['notification_url'], store) if broker else lambda message: None
        return recovery_watch(config, store, broker, poll=poll, notify=notify)
    return legacy_watch(config, store, broker, poll=poll)


def read_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    # B/S selects direction only; this policy is fixed to base 1 / boosted 2.
    config['entry_quantity'] = read_unit()
    if config['entry_quantity'] != 1:
        raise TradingError('H3 修復進場固定1口起跑，請將 KGI_UNIT 設為1；後續符合條件最多2口')
    config['mdd_sizing'] = False  # Legacy actual-account 3% sizing is superseded.
    config['recovery_sizing'] = True
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


def stop_detail(exc, *, stage, pending):
    # Only our own, fixed-text TradingError messages are suitable for Discord.
    # Native/vendor exceptions can carry credentials or account payloads.
    reason = str(exc) if type(exc) is TradingError else 'SDK或系統例外；詳細內容請在本機終端核對'
    return dict(error=type(exc).__name__, reason=reason, stage=stage,
                pending_order=bool(pending))


def main(argv=None):
    parser = argparse.ArgumentParser(description='凱基 H3：第1口跟新訊號；第2口2.5%→1.25%修復加碼；預設只預覽')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='登入並依新訊號送單')
    mode.add_argument('--diagnose', action='store_true', help='登入、查詢帳戶與庫存，不下單')
    parser.add_argument('--service', action='store_true', help='與 --live 一起使用，不互動')
    parser.add_argument('--check-config', action='store_true', help='只檢查本地設定，不登入')
    startup_mode = parser.add_mutually_exclusive_group()
    startup_mode.add_argument('--startup-simulation-buy-one', action='store_true',
                        help='僅測試環境：啟動先買進1口微台 MKT/IOC，每帳號只測一次')
    startup_mode.add_argument('--startup-production-buy-one', action='store_true',
                        help='本人互動確認的單次正式買進1口MKT/IOC；不支援背景服務')
    parser.add_argument('--config', default=str(BASE / 'h-config.json'))
    args = parser.parse_args(argv)
    if args.service and not args.live:
        raise TradingError('--service 必須與 --live 一起使用')
    if args.startup_simulation_buy_one and (args.diagnose or not (args.live or args.check_config)):
        raise TradingError('啟動模擬測試須搭配 --live，或以 --check-config 只檢查設定')
    if args.startup_production_buy_one and (args.service or args.diagnose or not (args.live or args.check_config)):
        raise TradingError('正式單次測試只允許本人互動操作，不支援 --service、預覽或診斷模式')
    if args.startup_production_buy_one and not args.check_config and not sys.stdin.isatty():
        raise TradingError('正式單次測試須在本人可輸入確認的終端執行，不接受背景或管線輸入')
    load_env(BASE / '.env')
    config = read_config(args.config)
    if config.get('recovery_sizing') and (args.startup_simulation_buy_one or args.startup_production_buy_one):
        raise TradingError('H3跟隨模式不接受啟動即買進測試，避免繞過等待新H3訊號的規則')
    if not args.diagnose:
        config['notification_url'] = read_webhook(BASE.parent / '.env')
    creds = credentials() if args.live or args.diagnose or args.check_config else None
    if args.startup_simulation_buy_one and creds[-1] is not True:
        raise TradingError('啟動買進1口僅供模擬測試；KGI_SIMULATION 必須為true，正式環境禁止')
    if args.startup_production_buy_one and creds[-1] is not False:
        raise TradingError('手動正式測試要求 KGI_SIMULATION=false；模擬測試請使用模擬選項')
    if args.check_config:
        if config.get('recovery_sizing'):
            from h3_recovery_sizing import validate_inputs
            validate_inputs(config)
        print('H3 KGI configuration OK; base1 follows fresh H3 without drawdown gate; addon at 2.5%->1.25%; cap2; no login or order attempted.')
        if args.startup_simulation_buy_one:
            print('Simulation-only startup BUY 1 TMF / MKT / IOC enabled; configuration check only.')
        if args.startup_production_buy_one:
            print('Manual production BUY 1 TMF / MKT / IOC configured; interactive confirmation required; check only, no login or order.')
        return
    if args.live and not args.service:
        environment = '測試' if creds[-1] else '正式'
        print(f'凱基{environment}環境，帳號末碼 {creds[2][-4:]}，{config["contract"].symbol}，第1口跟新H3、不等回撤；第2口2.5%→1.25%，市價 IOC。')
        if args.startup_simulation_buy_one:
            print('已指定模擬啟動測試：首次啟動先買進1口微台，MKT／IOC，之後繼續H3監控。')
        if not args.startup_production_buy_one and input('確認開始依 H3 新訊號送單，請輸入 START KGI H：').strip() != 'START KGI H':
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
    stage = "startup_state_check"
    try:
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        store = Store(runtime / f'{key}.sqlite')
        if creds:
            # SDK writes certificates/product/token files relative to its import CWD.
            # Keep all such private artifacts under the ignored, account-specific runtime.
            sdk_directory = runtime / f'{key}-sdk'
            sdk_directory.mkdir(exist_ok=True)
            os.chdir(sdk_directory)
            broker = KgiBroker(config, store)
            stage = "broker_login_and_inventory"
            broker.login(*creds)
            if args.live and store.pending():
                stage = "reconcile_rejected_pending"
                broker.recover_rejected_pending()
            if args.diagnose:
                print(f'凱基帳戶末碼 {broker.account[-4:]}；{broker.contract.symbol} 淨庫存 {broker.position():+d} 口。')
                print(f'未確認委託阻擋：{bool(store.pending())}；此模式未送單。')
                return
            if args.startup_simulation_buy_one:
                stage = "startup_simulation_test"
                policy = MddPolicy(store, config['contract'].symbol, one_way_cost=config['mdd_one_way_cost'])
                placed = startup_simulation_buy_one(broker, policy, simulation=creds[-1],
                                                    notify=Discord(config['notification_url'], store))
                print('啟動模擬買進1口已確認成交。' if placed else '啟動模擬測試先前已完成，本次不重複送單。', flush=True)
            if args.startup_production_buy_one:
                stage = "manual_production_test"
                policy = MddPolicy(store, config['contract'].symbol, one_way_cost=config['mdd_one_way_cost'])
                placed = manual_production_buy_one(broker, policy, simulation=creds[-1], confirm=input,
                                                    notify=Discord(config['notification_url'], store))
                print('本人確認的正式測試買進1口已確認成交。' if placed else '正式單次測試已完成，本次不重送。', flush=True)
            print('凱基 H3 已登入；第1口跟新H3、不等回撤；先回前高，後續新一輪2.5%→1.25%才2口，歸零回1口；不補歷史訊號。', flush=True)
        stage = "h3_watch"
        watch(config, store, broker)
    except KeyboardInterrupt:
        print('已停止監控；不會自動平倉或撤單。')
        if broker and config.get('notification_url'):
            Discord(config['notification_url'], store)('【凱基 H3 回撤加口｜停止】\n手動停止監控；未自動平倉或撤單。')
    except Exception as exc:
        if store:
            detail = stop_detail(exc, stage=stage, pending=store.pending())
            store.audit('stopped', detail)
            print(f'凱基H3停止：{detail["reason"]}；階段={stage}；未確認委託={detail["pending_order"]}', flush=True)
            if broker and config.get('notification_url'):
                Discord(config['notification_url'], store)(
                    f'【凱基 H3 回撤加口｜異常停止】\n錯誤類型：{detail["error"]}\n'
                    f'原因：{detail["reason"]}\n階段：{stage}\n'
                    f'本機未確認委託：{detail["pending_order"]}（不代表券商即時狀態）\n'
                    '請核對庫存、委託及本機紀錄；不重送委託。')
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
