"""KGI-only closed-trade drawdown sizing; shared H3/Capital rules stay unchanged."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from shared_h import OrderRejected, TradingError, parse_signal

POINT = Decimal('10')
THRESHOLD = Decimal('0.03')


def number(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or (result <= 0 if positive else result < 0):
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise TradingError('成交價格或成本格式無效，停止核對') from None


def read_webhook(path):
    """Read only the requested key, never import other accounts' .env settings."""
    value = ''
    try:
        lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        raise TradingError('無法讀取 backend-futures-py/.env 中的 Discord 設定') from None
    for line in lines:
        key, sep, candidate = line.strip().partition('=')
        if sep and key.strip() == 'DISCORD_KGI_H3_MDD_ADD_URL':
            value = candidate.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or parsed.hostname not in ('discord.com', 'discordapp.com')
            or not parsed.path.startswith('/api/webhooks/') or parsed.username or parsed.password):
        raise TradingError('請在 backend-futures-py/.env 設定有效的 DISCORD_KGI_H3_MDD_ADD_URL')
    return value


def execution_fill(trade, requested, symbol):
    """Snapshot the pinned SDK's confirmed Deal fields, including weighted price."""
    order_id = str(getattr(trade.order, 'order_id', '') or '').strip()
    if not order_id or str(getattr(trade.order, 'symbol', '')) != symbol:
        raise TradingError('成交委託識別或商品不符')
    deals, seen, total, amount = [], set(), 0, Decimal('0')
    for d in list(trade.order_status.deals):
        seq = str(getattr(d, 'reportseq', '') or '').strip()
        qty = number(getattr(d, 'quantity', None), positive=True)
        price = number(getattr(d, 'price', None), positive=True)
        stamp = str(getattr(d, 'ts', '') or '').strip()
        if not seq or seq in seen or qty != qty.to_integral_value() or not stamp:
            raise TradingError('成交序號、時間或口數缺漏，不能更新回撤')
        seen.add(seq)
        total += int(qty)
        amount += qty * price
        deals.append(dict(reportseq=seq, quantity=int(qty), price=str(price), ts=stamp))
    if total != requested:
        raise TradingError('成交口數未完整，不能更新回撤')
    payload = dict(order_id=order_id, symbol=symbol, quantity=total,
                   price=str(amount / total), deals=sorted(deals, key=lambda d: d['reportseq']),
                   confirmation_date=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    payload['id'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


class MddPolicy:
    def __init__(self, store, symbol, *, one_way_cost='24'):
        self.store, self.symbol = store, symbol
        self.cost = number(one_way_cost)
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS kgi_mdd_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kgi_mdd_fills (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        row = store.db.execute('SELECT payload FROM kgi_mdd_state WHERE id=1').fetchone()
        if row is None:
            state = dict(version=1, equity='0', peak='0', boosted=False, open_trade=None,
                         last_close=None, cost=str(self.cost), started_at=datetime.now(timezone.utc).isoformat())
            with store.db:
                store.db.execute('INSERT INTO kgi_mdd_state VALUES (1,?)', (json.dumps(state),))
        else:
            state = json.loads(row[0])
            if state.get('version') != 1 or Decimal(state['cost']) != self.cost:
                raise TradingError('回撤狀態版本或成本設定變更，需先核對既有損益基準')
        self.state()  # Validate saved arithmetic before any order.

    def state(self):
        s = json.loads(self.store.db.execute('SELECT payload FROM kgi_mdd_state WHERE id=1').fetchone()[0])
        try:
            equity, peak = Decimal(s['equity']), Decimal(s['peak'])
            if (not equity.is_finite() or not peak.is_finite() or peak < max(Decimal('0'), equity)
                    or type(s['boosted']) is not bool or (peak == equity and s['boosted'])):
                raise ValueError()
            opened = s['open_trade']
            if opened is not None:
                if (type(opened['direction']) is not int or opened['direction'] not in (-1, 1)
                        or type(opened['quantity']) is not int or opened['quantity'] not in (1, 2)
                        or opened['quantity'] != (2 if s['boosted'] else 1)
                        or opened['symbol'] != self.symbol):
                    raise ValueError()
                number(opened['price'], positive=True)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise TradingError('凱基回撤狀態不完整或算術不符，停止核對') from None
        return s

    def quantity(self):
        return 2 if self.state()['boosted'] else 1

    def validate_position(self, actual):
        opened = self.state()['open_trade']
        expected = opened['direction'] * opened['quantity'] if opened else 0
        if actual != expected:
            raise TradingError('券商庫存與回撤帳本不符；缺少可核對成本的舊倉或人工變動，停止送單')

    def confirmed(self, payload, fill):
        """Book a full fill and clear its order guard in the SAME SQLite commit."""
        db = self.store.db
        with db:
            if self.store.pending() != payload:
                raise TradingError('委託 guard 與成交帳本不符')
            if db.execute('SELECT 1 FROM kgi_mdd_fills WHERE id=?', (fill['id'],)).fetchone():
                raise TradingError('成交已計入回撤，停止重複入帳')
            if fill['symbol'] != self.symbol or fill['quantity'] != payload['quantity']:
                raise TradingError('成交商品或口數與計畫不符')
            s = self.state()
            old_boosted = s['boosted']
            price = number(fill['price'], positive=True)
            if payload['phase'] == 'flatten':
                opened = s['open_trade']
                self.validate_position(payload['previous'])
                if not opened or payload['target'] != 0:
                    raise TradingError('沒有已確認進場成本，不能結算舊倉')
                gross = ((price - Decimal(opened['price'])) * opened['direction']
                         * opened['quantity'] * POINT)
                costs = self.cost * 2 * opened['quantity']
                net = gross - costs
                equity = Decimal(s['equity']) + net
                peak = max(Decimal(s['peak']), equity)
                dd = peak - equity
                boosted = False if dd == 0 else old_boosted or dd >= price * POINT * THRESHOLD
                s.update(equity=str(equity), peak=str(peak), boosted=boosted, open_trade=None,
                         last_close=dict(gross=str(gross), costs=str(costs), net=str(net),
                                         price=str(price), drawdown=str(dd), ratio=str(dd / (price * POINT)),
                                         quantity=opened['quantity'], at=datetime.now(timezone.utc).isoformat()))
            elif payload['phase'] == 'entry':
                if s['open_trade'] is not None or payload['previous'] != 0:
                    raise TradingError('未確認空手，不能建立新倉帳本')
                qty = 2 if s['boosted'] else 1
                if abs(payload['target']) != qty or fill['quantity'] != qty:
                    raise TradingError('新倉口數與回撤階段不符')
                s['open_trade'] = dict(direction=1 if payload['target'] > 0 else -1,
                                       quantity=qty, price=str(price), symbol=self.symbol,
                                       fill_id=fill['id'])
            else:
                raise TradingError('未知成交階段')
            db.execute('INSERT INTO kgi_mdd_fills VALUES (?,?)', (fill['id'], json.dumps(fill)))
            db.execute('UPDATE kgi_mdd_state SET payload=? WHERE id=1', (json.dumps(s),))
            db.execute('DELETE FROM guard WHERE id=1')
            detail = dict(trigger=payload['trigger'], phase=payload['phase'], fill_id=fill['id'],
                          equity=s['equity'], peak=s['peak'], boosted=s['boosted'],
                          stage_changed=s['boosted'] != old_boosted, target=payload['target'])
            db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), 'kgi_mdd_confirmed', json.dumps(detail)))
        return s


class Discord:
    def __init__(self, url, store):
        self.url, self.store = url, store

    def __call__(self, message):
        request = urllib.request.Request(self.url, data=json.dumps(
            {'content': message[:1900], 'allowed_mentions': {'parse': []}}).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'KGI-H3-MDD/1.0'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read(1024)
        except Exception as exc:
            # Never let HTTP errors expose webhook tokens or cause order resubmission.
            self.store.audit('kgi_discord_failed', {'error': type(exc).__name__})


def summary(policy):
    s = policy.state()
    dd = Decimal(s['peak']) - Decimal(s['equity'])
    last = s['last_close']
    ratio = f"{Decimal(last['ratio']):.2%}" if last else '尚無平倉'
    return (f"累計已平倉估算淨利 {Decimal(s['equity']):,.2f} 元／高水位 {Decimal(s['peak']):,.2f} 元\n"
            f"回撤 {dd:,.2f} 元；最後平倉回撤比 {ratio}\n"
            f"下一筆 {policy.quantity()} 口；門檻3%，加碼後維持2口直到回撤歸零。\n"
            f"每口每點10元；單邊成本估算 {policy.cost} 元；不含未平倉浮盈虧。")


def execute_mdd(broker, policy, trigger, direction, notify=lambda message: None):
    if type(direction) is not int or direction not in (-1, 1):
        raise TradingError('訊號方向錯誤')
    store = policy.store
    if store.pending():
        raise TradingError('上筆委託未確認，停止，不重送')
    previous = broker.position()
    policy.validate_position(previous)
    phases = ['flatten', 'entry'] if previous else ['entry']
    messages = []
    try:
        for phase in phases:
            if phase == 'entry':
                previous = broker.position()
                policy.validate_position(previous)
                if previous:
                    raise TradingError('平倉尚未歸零，不開新倉')
                broker.entry_quantity = policy.quantity()
                target = direction * broker.entry_quantity
            else:
                target = 0
            delta = target - previous
            payload = dict(trigger=trigger, phase=phase, previous=previous, target=target,
                           side='buy' if delta > 0 else 'sell', quantity=abs(delta))
            store.begin(payload)
            store.audit('prepared', payload)
            try:
                result = broker.send_and_confirm(delta, phase, target)
            except OrderRejected as exc:
                store.resolve_rejected(payload, exc.evidence)
                notify('【凱基 H3 回撤加口｜整筆拒單】\n' + str(exc) +
                       '\n零成交且庫存一致；本訊號不重送，繼續等待新訊號。\n' + summary(policy))
                raise
            if result != target or not broker.last_fill:
                raise TradingError('成交庫存或價格未完整確認；保留 guard，不重送')
            was_boosted = policy.state()['boosted']
            s = policy.confirmed(payload, broker.last_fill)
            if phase == 'flatten':
                last = s['last_close']
                change = ('達3%｜下一筆加至2口' if s['boosted'] else '回撤歸零｜下一筆恢復1口') if was_boosted != s['boosted'] else '平倉確認'
                messages.append(f"【凱基 H3 回撤加口｜{change}】\n本筆 {last['quantity']} 口；估算淨損益 {Decimal(last['net']):+,.2f} 元\n" + summary(policy))
            else:
                messages.append(f"【凱基 H3 回撤加口｜進場確認】\n{'多' if target > 0 else '空'} {abs(target)} 口，均價 {broker.last_fill['price']}\n" + summary(policy))
            previous = target
    finally:
        # HTTP latency never sits between the close confirmation and new order.
        for message in messages:
            notify(message)
    return previous


def startup_simulation_buy_one(broker, policy, *, simulation, notify=lambda message: None):
    """One explicit startup test per simulation account, never production or retries."""
    if simulation is not True or getattr(broker, 'simulation', None) is not True:
        raise TradingError('啟動買進1口測試僅限 KGI_SIMULATION=true；正式環境禁止執行')
    store = policy.store
    if store.pending():
        raise TradingError('有未確認委託，不執行啟動測試')
    store.db.execute('''CREATE TABLE IF NOT EXISTS kgi_startup_simulation_test
                        (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL)''')
    store.db.commit()
    row = store.db.execute('SELECT status FROM kgi_startup_simulation_test WHERE id=1').fetchone()
    if row:
        if row[0] != 'confirmed':
            raise TradingError('啟動測試已有嘗試紀錄，請核對成交；不自動重送')
        store.audit('startup_simulation_buy_one_skipped', {'reason': 'already_confirmed'})
        return False
    policy.validate_position(broker.position())
    if policy.state()['open_trade'] is not None or policy.quantity() != 1:
        raise TradingError('啟動測試須空手且處於1口階段；不平舊倉、不調整既有口數')
    # Reserve before execution; a failure/crash never turns a restart into a retry.
    with store.db:
        store.db.execute("INSERT INTO kgi_startup_simulation_test VALUES (1,'attempted')")
    target = execute_mdd(broker, policy, 'startup-simulation-buy-one-v1', 1,
                         lambda message: notify('【僅模擬環境｜啟動買進1口測試】\n' + message))
    if target != 1:
        raise TradingError('啟動模擬測試成交口數不符')
    with store.db:
        store.db.execute("UPDATE kgi_startup_simulation_test SET status='confirmed' WHERE id=1")
    store.audit('startup_simulation_buy_one_confirmed', {'quantity': 1, 'price_type': 'MKT', 'time_in_force': 'IOC'})
    return True


def manual_production_buy_one(broker, policy, *, simulation, confirm, notify=lambda message: None):
    """Human-operated one-time production entry; never an unattended startup default."""
    if simulation is not False or getattr(broker, 'simulation', None) is not False:
        raise TradingError('手動正式測試須確認登入正式環境；不可套用到模擬環境')
    store = policy.store
    if store.pending():
        raise TradingError('有未確認委託，請先核對，不送正式測試單')
    store.db.execute('''CREATE TABLE IF NOT EXISTS kgi_manual_production_test
                        (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL)''')
    store.db.commit()
    row = store.db.execute('SELECT status FROM kgi_manual_production_test WHERE id=1').fetchone()
    if row:
        if row[0] != 'confirmed':
            raise TradingError('正式測試已有嘗試紀錄，請核對券商回報；不自動重送')
        answer = confirm('正式測試先前已完成，本次不重送。若要繼續H3監控，請輸入 START KGI H：')
        if answer.strip() != 'START KGI H':
            raise TradingError('使用者取消啟動，未送出新委託')
        store.audit('manual_production_buy_one_skipped', {'reason': 'already_confirmed'})
        return False
    policy.validate_position(broker.position())
    if policy.state()['open_trade'] is not None or policy.quantity() != 1:
        raise TradingError('正式單次測試只接受空手及1口階段，不平舊倉或加至既有部位')
    suffix = str(broker.account)[-4:]
    expected = f'BUY 1 {policy.symbol} MKT IOC {suffix}'
    answer = confirm(f'【正式委託】帳號末碼{suffix}，買進1口微型台指{policy.symbol}，市價MKT／IOC。'
                     f'成交後會繼續H3自動監控。確認由本人送單請輸入：{expected}\n> ')
    if answer.strip() != expected:
        raise TradingError('使用者未確認正式委託；未送單')
    with store.db:
        store.db.execute("INSERT INTO kgi_manual_production_test VALUES (1,'attempted')")
    target = execute_mdd(broker, policy, 'manual-production-buy-one-v1', 1,
                         lambda message: notify('【本人確認｜正式單次測試】\n' + message))
    if target != 1:
        raise TradingError('正式測試成交口數不符')
    with store.db:
        store.db.execute("UPDATE kgi_manual_production_test SET status='confirmed' WHERE id=1")
    store.audit('manual_production_buy_one_confirmed', {'quantity': 1, 'price_type': 'MKT', 'time_in_force': 'IOC'})
    return True


def watch(config, store, broker=None, *, poll=0.5):
    """Keep shared H3 trust/freshness/dedup rules; KGI sizes only after close."""
    source = config['signal_source']
    policy = MddPolicy(store, config['contract'].symbol, one_way_cost=config.get('mdd_one_way_cost', '24'))
    notify = Discord(config['notification_url'], store) if broker else lambda message: None
    if broker:
        if store.pending():
            raise TradingError('有未確認委託，停止啟動')
        policy.validate_position(broker.position())
        notify('【凱基 H3 回撤加口｜啟動】\n直接跟隨新 H3 訊號；先平後開，不補歷史單。\n' + summary(policy))
    stat = source.stat()
    offset, identity = stat.st_size, stat.st_ino
    print('KGI H3：等待新訊號；1口起跑、回撤達3%後2口、回撤歸零後1口。', flush=True)
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
                signal = parse_signal(event)
                if signal is None or not store.consume(*signal):
                    continue
                key, direction, _ = signal
                if broker:
                    store.audit('signal', {'key': key, 'direction': direction})
                    try:
                        target = execute_mdd(broker, policy, key, direction, notify)
                    except OrderRejected as exc:
                        print(f'{key}：{exc}；繼續等待新H3訊號。', flush=True)
                        continue
                    print(f'{key}：已確認先平倉再進場 {target:+d} 口', flush=True)
                else:
                    store.audit('paper_signal', dict(key=key, direction=direction, entry_quantity=policy.quantity(),
                        rule='closed_equity_drawdown_3pct', send=False,
                        note='paper不模擬成交損益，不推進回撤階段'))
                    print(f'預覽 {key}：方向 {direction:+d}，目前階段 {policy.quantity()} 口；未送單、不推算損益。', flush=True)
        time.sleep(poll)
