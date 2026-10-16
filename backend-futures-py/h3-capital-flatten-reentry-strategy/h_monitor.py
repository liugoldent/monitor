"""Watch fresh local Relay H signals; live execution is explicit and interactive."""
from __future__ import annotations
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from h_core import Contract, OrderRejected, Store, TradingError, execute_h, parse_signal
from local_env import load_env, selected_account
from capital_notifications import Discord, read_webhook, stop_message

BASE = Path(__file__).resolve().parent


def read_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if config.get('entry_quantity') != 1:
        raise TradingError('H3 修復進場的基礎口數須為1，後續符合條件最多2口')
    config['recovery_sizing'] = True
    source = Path(config['signal_source'])
    config['signal_source'] = source if source.is_absolute() else (BASE / source).resolve()
    config['contract'] = Contract(**config['contract'])
    return config


def diagnose(broker, store):
    # Use the adapter's paced/retried inventory path, never a second raw query.
    position = broker.position()
    broker.check_outstanding()
    reports = [r for r in broker.reports if broker.is_micro(r)]
    fields = ('KeyNo', 'SeqNo', 'MarketType', 'Type', 'OrderErr', 'BuySell',
              'ComId', 'ComId1', 'YearMonth1', 'OrderNo', 'Qty', 'ExecutionNo',
              'Date', 'Time', 'TradeDate', 'ErrorMsg')
    result = dict(mode='read_only', account_suffix=broker.account[-4:],
        quote_code=broker.contract.quote_code, settle_ym=broker.contract.settle_ym,
        micro_position=position, replay_complete=broker.replay_complete.is_set(),
        report_count=len(broker.reports), pending_order=bool(store.pending()), orders_sent=0,
        report_account_exact_match=bool(reports),
        reports=[{k: broker.redact(r.get(k, '')) for k in fields} for r in reports])
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    (BASE / 'runtime' / 'diagnostic.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('此模式只查詢，未送出委託。', flush=True)


def watch(config, store, broker=None, *, poll=0.5):
    if config.get('recovery_sizing'):
        from h3_recovery_sizing import watch as recovery_watch
        notify = Discord(config['notification_url'], store) if broker else lambda message: None
        return recovery_watch(config, store, broker, poll=poll, notify=notify)
    source = config['signal_source']
    entry_quantity = config.get('entry_quantity', 1)
    if type(entry_quantity) is not int or entry_quantity < 1:
        raise TradingError('進場口數須為正整數')
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
                    try:
                        execute_h(broker, direction, store, key, entry_quantity=entry_quantity)
                    except OrderRejected as exc:
                        print(f'{key}：已確認新單拒絕，{exc}；此訊號不重送，繼續等待新訊號。', flush=True)
                        continue
                    print(f'{key}：已確認先平倉，再進場 {direction * entry_quantity:+d} 口', flush=True)
                else:
                    detail = {'key': key, 'direction': direction, 'entry_quantity': entry_quantity,
                              'rule': 'flatten_then_entry', 'send': False}
                    store.audit('paper_signal', detail)
                    print(f'預覽：{key} → 先平微台，再進場 {direction * entry_quantity:+d}口（未送單）', flush=True)
        time.sleep(poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description='群益 H 微台先平倉再進場')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='正式委託；需本機登入及啟動確認')
    mode.add_argument('--diagnose', action='store_true', help='只查帳戶、微台商品與庫存')
    mode.add_argument('--review-pending', action='store_true', help='本人核對歷史新單拒絕；不送單')
    mode.add_argument('--test-buy-one', action='store_true', help='本人互動確認的單次正式微台買進1口MKT/IOC')
    parser.add_argument('--rejection-evidence', help='核對舊拒單的唯讀證據JSON；正式測試預設使用已保存的10/12證據')
    parser.add_argument('--config', default=str(BASE / 'h-config.json'))
    parser.add_argument('--service', action='store_true', help='Windows啟動腳本專用，使用.env、不互動')
    parser.add_argument('--check-config', action='store_true', help='只檢查本機設定，不登入、不送單')
    args = parser.parse_args(argv)
    if args.service and not args.live:
        raise TradingError('--service 必須與 --live 一起使用')
    if args.review_pending or args.test_buy_one:
        if not args.check_config and not sys.stdin.isatty():
            raise TradingError('核對舊單及正式測試須由本人在互動終端執行，不接受背景或管線輸入')
        if args.review_pending and not args.rejection_evidence:
            raise TradingError('--review-pending 須指定 --rejection-evidence；不可直接清除委託紀錄')
    load_env(BASE / '.env')
    config = read_config(args.config)
    if args.live or args.check_config:
        config['notification_url'] = read_webhook(BASE.parent / '.env')
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
            from h3_recovery_sizing import validate_inputs
            validate_inputs(config)
            print('H3 Capital configuration OK; no login or order attempted.')
            return
    runtime = BASE / 'runtime'
    runtime.mkdir(exist_ok=True)
    import msvcrt
    lock_file = (runtime / ('live.lock' if args.live or args.diagnose or args.review_pending or args.test_buy_one else 'paper.lock')).open('a+b')
    lock_file.seek(0)
    if lock_file.read(1) == b'':
        lock_file.write(b'0')
        lock_file.flush()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    store = Store(runtime / ('live.sqlite' if args.live or args.diagnose or args.review_pending or args.test_buy_one else 'paper.sqlite'))
    broker = None
    try:
        if args.live or args.diagnose or args.review_pending or args.test_buy_one:
            if args.live or args.test_buy_one:
                config['contract'].validate()
            from capital_broker import CapitalBroker
            broker = CapitalBroker(config, store)
            user = os.getenv('CAPITAL_LOGIN_ID', '').strip() or input('群益登入 ID：').strip()
            password = os.getenv('CAPITAL_PASSWORD', '') or getpass.getpass('群益密碼（不顯示）：')
            try:
                broker.login(user, password, diagnose=args.diagnose or args.review_pending or args.test_buy_one)
            finally:
                password = None
            if args.diagnose:
                diagnose(broker, store)
                return
            if args.review_pending:
                from capital_operations import review_pending
                review_pending(broker, store, args.rejection_evidence, input)
                return
            if args.test_buy_one:
                from capital_operations import test_buy_one_with_review
                evidence_path = args.rejection_evidence or runtime / 'read-only-order-check-20261012.json'
                test_buy_one_with_review(broker, store, evidence_path, input)
                return
            c = config['contract']
            print(f'正式帳戶末碼 {broker.account[-4:]}；委託 {c.order_code} {c.settle_ym}；第1口跟新H3、不等回撤；第2口2.5%→1.25%；市價 IOC。', flush=True)
            print('每筆浩克3新訊號：先平倉，完整成交及庫存確認後再新倉；啟動跳過歷史訊號。')
            if not args.service and input('本人確認啟動請輸入 START H：').strip() != 'START H':
                print('未啟動自動送單。')
                return
        watch(config, store, broker)
    except KeyboardInterrupt:
        print('已停止監控；停止不會自動平倉。')
        if args.live and broker and config.get('notification_url'):
            Discord(config['notification_url'], store)('【群益 H3 修復進場｜停止】\n手動停止監控；未自動平倉或撤單。')
    except Exception as exc:
        message = broker.redact(str(exc)) if broker and isinstance(exc, TradingError) else (
            str(exc) if isinstance(exc, TradingError) else 'SDK或系統例外；請查看本機終端')
        store.audit('stopped', {'error': type(exc).__name__, 'message': message,
                                'pending_order': bool(store.pending())})
        print(f'群益H3停止：{message}', flush=True)
        if args.live and broker and config.get('notification_url'):
            Discord(config['notification_url'], store)(stop_message(
                exc, pending=store.pending(), redact=broker.redact))
        raise
    finally:
        if broker:
            broker.close()
        store.db.close()
        lock_file.close()


if __name__ == '__main__':
    os.chdir(BASE)
    main()
