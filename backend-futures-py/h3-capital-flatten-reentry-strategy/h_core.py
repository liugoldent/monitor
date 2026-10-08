"""H signal consumption and durable single-flight order state (no vendor imports)."""
from __future__ import annotations
import json
import re
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from h_signal import parse_h_event_direction


class TradingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Contract:
    quote_code: str
    order_code: str
    settle_ym: str
    inventory_code: str
    inventory_prefix: str

    def validate(self):
        if not all((self.quote_code, self.order_code, self.inventory_code, self.inventory_prefix)):
            raise TradingError('請先設定群益報價、委託與庫存商品代碼')
        if not re.fullmatch(r'\d{6}', self.settle_ym) or not 1 <= int(self.settle_ym[4:]) <= 12:
            raise TradingError('契約年月須為 YYYYMM')
        if not self.inventory_code.startswith(self.inventory_prefix):
            raise TradingError('庫存代碼與微台庫存前綴不一致')


def parse_signal(event, now=None, max_age=30):
    direction = parse_h_event_direction(event)
    if direction is None:
        return None
    if not all(isinstance(event.get(k), int) and not isinstance(event[k], bool)
               for k in ('chat_id', 'message_id')):
        return None
    try:
        received = datetime.fromisoformat(event['received_at'])
    except (KeyError, TypeError, ValueError):
        return None
    # Relay's naive timestamps are documented as Asia/Taipei local time.
    if received.tzinfo is None:
        from datetime import timedelta
        received = received.replace(tzinfo=timezone(timedelta(hours=8)))
    timestamp = received.timestamp()
    age = (now or datetime.now(timezone.utc)).timestamp() - timestamp
    if not 0 <= age <= max_age:
        return None
    return f"{event['chat_id']}:{event['message_id']}", direction, timestamp


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, direction INTEGER, received REAL, status TEXT);
            CREATE TABLE IF NOT EXISTS guard (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, event TEXT, detail TEXT);
        ''')
        self.db.commit()

    def audit(self, event, detail):
        with self.db:
            self.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                            (datetime.now(timezone.utc).isoformat(), event, json.dumps(detail, ensure_ascii=False)))

    def consume(self, key, direction, received):
        with self.db:
            if self.db.execute('SELECT 1 FROM signals WHERE id=?', (key,)).fetchone():
                return False
            last = self.db.execute("SELECT direction,received FROM signals WHERE status != 'duplicate' ORDER BY rowid DESC LIMIT 1").fetchone()
            duplicate = last and last[0] == direction and 0 <= received - last[1] <= 10
            self.db.execute('INSERT INTO signals VALUES (?,?,?,?)',
                            (key, direction, received, 'duplicate' if duplicate else 'consumed'))
        return not duplicate

    def pending(self):
        row = self.db.execute('SELECT payload FROM guard WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def begin(self, payload):
        with self.db:
            if self.pending():
                raise TradingError('有未確認委託；需先人工核對，不重送')
            self.db.execute('INSERT INTO guard VALUES (1,?)', (json.dumps(payload),))

    def complete(self):
        with self.db:
            self.db.execute('DELETE FROM guard WHERE id=1')


def parse_inventory(result, account, login_id, contract):
    if result.StatusCode == 1 and result.RawData == f'001,查無資料,{account}':
        return 0
    if result.StatusCode != 0:
        raise TradingError(f'庫存查詢錯誤：{result.StatusCode}')
    raw = result.RawData
    if not isinstance(raw, str):
        raise TradingError('庫存原始回傳格式錯誤')
    positions = []
    for record in raw.rstrip('#').split('#'):
        if not record.strip():
            continue
        fields = [f.strip() for f in record.split(',')]
        if len(fields) != 10:
            raise TradingError('庫存格式未確認，不能視為空手')
        market, acc, product, side, quantity, day_qty, price, fee, tax, who = fields
        if acc != account or who != login_id:
            raise TradingError('庫存回傳帳戶不符')
        if not product.startswith((contract.inventory_prefix, contract.order_code, 'TMF')):
            continue
        aliases = {contract.inventory_code, contract.quote_code, contract.order_code + contract.settle_ym,
                   'TMF' + contract.settle_ym}
        if market != 'TF' or product not in aliases:
            raise TradingError('有其他月份或無法識別的微台庫存，停止送單')
        if side not in ('B', 'S') or not quantity.isdigit() or not day_qty.isdigit():
            raise TradingError('微台庫存買賣別／數量尚未確認')
        if int(day_qty):
            raise TradingError('存在當沖庫存，須先核對一般與當沖平倉規則')
        if int(quantity):
            positions.append((1 if side == 'B' else -1, int(quantity)))
    if len({side for side, qty in positions}) > 1:
        raise TradingError('微台同時有多空庫存，停止送單')
    return sum(side * qty for side, qty in positions)


def execute_h(broker, direction, store, trigger):
    if direction not in (-1, 1):
        raise TradingError('訊號方向錯誤')
    if store.pending():
        raise TradingError('上筆委託未確認，停止送單')
    for phase, target in (('flatten', 0), ('entry', direction)):
        previous = broker.position()
        delta = target - previous
        if not delta:
            continue
        payload = dict(trigger=trigger, phase=phase, previous=previous, target=target,
                       side='buy' if delta > 0 else 'sell', quantity=abs(delta))
        store.begin(payload)  # Must be durable before calling the broker.
        store.audit('prepared', payload)
        result = broker.send_and_confirm(delta, phase, target)
        if result != target:
            raise TradingError('成交後庫存不符；停止，不重送')
        store.audit('confirmed', payload)
        store.complete()
    return direction
