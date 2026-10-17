"""Shared H3 recovery policy for Capital, KGI and Shioaji. No SDK imports or startup orders."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from h_core import OrderRejected, TradingError, execute_h, parse_signal
from h3_entry_watch import (Curve, ENTRY, RECOVERY, RULE_VERSION, WatchError,
                            build_snapshot, number)

BASE = Path(__file__).resolve().parent
SEED_PATH = BASE / 'telegram-relay-runtime' / 'h3_recovery_seed_v2.json'
PRICE_PATH = BASE / 'tv_doc' / 'webhook_data_1min.csv'
RULE_TEXT = '第1口從新H3訊號持續跟隨，不等回撤；先回前高，後續新一輪2.5%→≤1.25%才2口；歸零恢復1口。'


def validate_inputs(config):
    seed_path = Path(config.get('recovery_seed', SEED_PATH))
    price_path = Path(config.get('recovery_prices', PRICE_PATH))
    try:
        seed = json.loads(seed_path.read_text(encoding='utf-8'))
        if (seed['version'] != RULE_VERSION or number(seed['entry_threshold']) != ENTRY
                or number(seed['recovery_threshold']) != RECOVERY
                or number(seed['cost_points_one_way']) != Decimal('2.4')
                or len(seed['source_sha256']) != 64 or not price_path.is_file()):
            raise ValueError()
        Curve(seed['curve'])
    except (OSError, KeyError, ValueError, TypeError):
        raise TradingError('H3 2.5%/1.25% 歷史基準或行情檔缺漏，禁止啟動送單') from None
    return seed_path, price_path, seed['source_sha256']


class RecoveryPolicy:
    def __init__(self, store, symbol, source_hash):
        self.store = store
        store.db.execute('CREATE TABLE IF NOT EXISTS h3_recovery_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        store.db.commit()
        row = store.db.execute('SELECT payload FROM h3_recovery_state WHERE id=1').fetchone()
        if row is None:
            old = store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kgi_mdd_state'").fetchone()
            if old:
                legacy = store.db.execute('SELECT payload FROM kgi_mdd_state WHERE id=1').fetchone()
                if legacy and json.loads(legacy[0]).get('open_trade') is not None:
                    raise TradingError('凱基舊版仍有已記帳持倉，請先核對；不自動移轉或平倉')
            state = dict(version=RULE_VERSION, symbol=symbol, source_hash=source_hash,
                         active=False, unlocked=False, boosted=False, expected=0,
                         entry_high_count=None, last_key=None, last_high_count=None)
            with store.db:
                store.db.execute('INSERT INTO h3_recovery_state VALUES (1,?)', (json.dumps(state),))
        self.symbol, self.source_hash = symbol, source_hash
        self.state()

    def state(self):
        try:
            s = json.loads(self.store.db.execute('SELECT payload FROM h3_recovery_state WHERE id=1').fetchone()[0])
            if (s['version'] != RULE_VERSION or s['symbol'] != self.symbol
                    or s['source_hash'] != self.source_hash
                    or any(type(s[k]) is not bool for k in ('active','unlocked','boosted'))
                    or type(s['expected']) is not int or abs(s['expected']) > 2
                    or (not s['active'] and (s['unlocked'] or s['boosted'] or s['expected']))
                    or (s['boosted'] and not s['unlocked'])
                    or abs(s['expected']) not in (0, 2 if s['boosted'] else 1)
                    or (s['active'] and (type(s['entry_high_count']) is not int or s['entry_high_count'] < 0))
                    or (s['last_high_count'] is not None and (type(s['last_high_count']) is not int or s['last_high_count'] < 0))):
                raise ValueError()
            return s
        except (ValueError, KeyError, TypeError):
            raise TradingError('H3 修復進場帳本不完整或商品／歷史基準變更，停止核對') from None

    def validate_position(self, actual):
        if actual != self.state()['expected']:
            raise TradingError('券商庫存與 H3 修復進場帳本不符；請核對舊倉或人工變動，不自動平倉')

    def plan(self, snapshot):
        s = self.state()
        if (snapshot['rule_version'] != RULE_VERSION or snapshot['source_sha256'] != self.source_hash
                or snapshot['side'] not in (-1,1)):
            raise TradingError('H3 影子曲線版本、方向或基準不符')
        if not snapshot['is_new'] or snapshot['event_key'] == s['last_key']:
            return s, 0, 'duplicate'
        high = snapshot['high_count']
        if s['last_high_count'] is not None and high < s['last_high_count']:
            raise TradingError('H3 影子曲線前高紀錄倒退，停止核對')
        dd = number(snapshot['dd_points'])
        previous_high = s['last_high_count']
        s['last_high_count'] = high
        if not s['active']:
            s.update(active=True, unlocked=dd == 0, boosted=False, entry_high_count=high)
            return s, 1, 'first_entry'
        if dd == 0 or high > s['entry_high_count']:
            s['unlocked'] = True
        if dd == 0 or (previous_high is not None and high > previous_high):
            s['boosted'] = False
        if dd > 0 and s['unlocked'] and snapshot['qualifies'] and snapshot['eligible']:
            s['boosted'] = True
        return s, 2 if s['boosted'] else 1, 'following'

    def confirmed(self, payload, planned):
        db = self.store.db
        with db:
            if self.store.pending() != payload or self.state()['expected'] != payload['previous']:
                raise TradingError('委託 guard、庫存帳本與成交不符，不解除阻擋')
            saved = dict(planned, expected=payload['target'], last_key=payload['trigger'])
            db.execute('UPDATE h3_recovery_state SET payload=? WHERE id=1', (json.dumps(saved),))
            db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), 'h3_recovery_confirmed', json.dumps(dict(order=payload,state=saved))))
            db.execute('DELETE FROM guard WHERE id=1')


def process_event(config, store, policy, broker, event, *, notify=lambda message: None):
    signal = parse_signal(event)
    if signal is None or not store.consume(*signal):
        return None
    key, direction, _ = signal
    seed_path, price_path, _ = validate_inputs(config)
    try:
        snap = build_snapshot(seed_path, Path(config['signal_source']), price_path,
                              datetime.fromisoformat(event['received_at']), key, direction)
    except WatchError:
        raise TradingError('H3 歷史或訊號當時行情不完整，未送本次委託；請核對') from None
    planned, quantity, reason = policy.plan(snap)
    store.audit('h3_recovery_decision', dict(key=key, quantity=quantity, reason=reason, shadow=snap, send=broker is not None))
    if not quantity:
        return 0
    if broker is None:
        return quantity  # Preview never creates a fictitious account position.
    policy.validate_position(broker.position())
    if parse_signal(event) is None:
        store.audit('h3_recovery_expired', dict(key=key))
        return 0
    broker.entry_quantity = quantity
    try:
        result = execute_h(broker, direction, store, key, entry_quantity=quantity,
                           confirmed=lambda payload: policy.confirmed(payload, planned))
    except OrderRejected:
        policy.validate_position(broker.position())
        notify('【H3 修復進場｜整筆拒單】本訊號不重送，等待新的 H3 訊號。')
        raise
    notify(f'【H3 修復進場｜成交確認】\n方向：{"多" if result > 0 else "空"}；{abs(result)}口\n'
           f'單口影子回撤：{Decimal(snap["dd_points"]):,.2f}點（{Decimal(snap["ratio"]):.2%}）\n' + RULE_TEXT)
    return quantity


def watch(config, store, broker=None, *, poll=0.5, notify=lambda message: None):
    source = Path(config['signal_source'])
    _, _, source_hash = validate_inputs(config)
    contract = config['contract']
    symbol = getattr(contract, 'symbol', None) or (contract.order_code + contract.settle_ym)
    policy = RecoveryPolicy(store, symbol, source_hash)
    if store.pending():
        raise TradingError('有未確認委託，不啟動 H3 修復進場監控')
    if broker:
        policy.validate_position(broker.position())
    stat = source.stat()
    offset, identity = stat.st_size, stat.st_ino
    print('H3 修復進場監控：啟動不下單、不補歷史單；' + RULE_TEXT, flush=True)
    notify('【H3 修復進場｜監控啟動】\n啟動不下單；等待新的 H3 訊號確認。\n' + RULE_TEXT)
    while True:
        if broker:
            broker.check()
        stat = source.stat()
        if stat.st_ino != identity or stat.st_size < offset:
            offset, identity = stat.st_size, stat.st_ino
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
                try:
                    quantity = process_event(config, store, policy, broker, event, notify=notify)
                    if quantity is not None:
                        print(f'H3 修復進場：本次 {quantity}口' + ('（預覽未送單）' if broker is None else ''), flush=True)
                except OrderRejected:
                    print('H3 本次整筆拒單，不重送，繼續等待新訊號。', flush=True)
        time.sleep(poll)
