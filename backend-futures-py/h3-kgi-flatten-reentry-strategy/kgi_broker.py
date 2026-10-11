"""KGISuperPy 2.1.2 adapter. Imports/login are lazy; no order retry is permitted."""
from __future__ import annotations
import importlib.metadata
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from shared_h import TradingError

SDK_VERSION = '2.1.2'
POSITION_COLUMNS = {'Symbol', 'BrokerId', 'Account', 'Exchange', 'ComType',
                    'ComID', 'ComYM', 'BS', 'OTQty', 'Group', 'Trader'}


@dataclass(frozen=True)
class Contract:
    product: str
    settle_ym: str
    symbol: str

    def validate(self):
        if self.product != 'TMF':
            raise TradingError('此策略只交易 TMF 微型臺指期貨')
        if not re.fullmatch(r'\d{6}', self.settle_ym) or not 1 <= int(self.settle_ym[4:]) <= 12:
            raise TradingError('契約年月須為 YYYYMM')
        expected = self.product + chr(64 + int(self.settle_ym[4:])) + self.settle_ym[3]
        if self.symbol != expected:
            raise TradingError('凱基 symbol 與契約年月不一致')
        if self.settle_ym < datetime.now().strftime('%Y%m'):
            raise TradingError('契約月份已過期，請更新設定；沒有自動換月')


def integer(value):
    if isinstance(value, bool):
        raise TradingError('數量格式錯誤')
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError()
        return int(number)
    except (ValueError, InvalidOperation):
        raise TradingError('數量格式錯誤') from None


def parse_position(frame, account, broker_id, contract):
    # SDK returns None on no response. Only a schema-bearing empty DataFrame is flat.
    if frame is None or not hasattr(frame, 'columns') or not POSITION_COLUMNS <= set(frame.columns):
        raise TradingError('凱基庫存無回應或格式不明，不能視為空手')
    positions = []
    for row in frame.to_dict('records'):
        field = lambda key: str(row[key]).strip()
        if field('Account') != account or field('BrokerId') != broker_id:
            raise TradingError('庫存回傳帳戶不符')
        if field('ComID') != contract.product:
            continue
        if (field('Exchange') != 'TIMEX' or field('ComType') != 'F'
                or field('ComYM') != contract.settle_ym or field('Symbol') != contract.symbol
                or field('Group') or field('Trader')):
            raise TradingError('有其他月份或無法識別的微台庫存，停止送單')
        if field('BS') not in ('B', 'S'):
            raise TradingError('微台庫存買賣別不明')
        qty = integer(row['OTQty'])
        if qty:
            positions.append((1 if field('BS') == 'B' else -1, qty))
    if len({side for side, _ in positions}) > 1:
        raise TradingError('微台同時有多空庫存，停止送單')
    return sum(side * qty for side, qty in positions)


class ExplicitPositionBridge:
    """Preserve SDK Trade registration/callbacks, replacing its hard-coded PF=3.

    Native Order PF: 0=open, 1=close, 2=day trade, 3=auto. See the supplied
    TradeCom example positionEffect() and kgisuperpy pushClient/pyTradeCom.py.
    All other calls/attributes delegate to the original bridge unchanged.
    """
    def __init__(self, native):
        self.native = native
        self.phase = None

    def __getattr__(self, name):
        return getattr(self.native, name)

    def Order(self, *args):
        if len(args) != 17 or args[0] != 0 or args[12] != 3 or self.phase not in ('flatten', 'entry'):
            raise TradingError('SDK 委託介面不符，停止送單')
        args = list(args)
        args[12] = 1 if self.phase == 'flatten' else 0
        return self.native.Order(*args)


def enum_name(value):
    return getattr(value, 'name', None)


def filled_quantity(trade, requested):
    status = getattr(trade, 'order_status', None)
    if status is None:
        raise TradingError('無有效委託回傳，停止，不重送')
    if any(enum_name(op.status) == 'Failed' for op in list(trade.operations)):
        raise TradingError('凱基拒單或委託失敗，停止，不重送')
    state = enum_name(status.status)
    if state in ('Failed', 'Cancelled', 'PartFilled_Cancelled'):
        raise TradingError('委託未完整成交，停止，不重送')
    deals = list(status.deals)
    seen = set()
    total = 0
    for deal in deals:
        key = str(deal.reportseq or '').strip()
        if not key or key in seen:
            raise TradingError('成交回報序號缺漏或重複，停止核對')
        seen.add(key)
        total += integer(deal.quantity)
    if total > requested or (state == 'Filled' and total != requested):
        raise TradingError('成交口數與委託不符')
    return total == requested and state == 'Filled'


class KgiBroker:
    def __init__(self, config, store, output=print):
        self.contract = config['contract']
        self.timeout = config['confirmation_timeout']
        self.entry_quantity = config.get('entry_quantity', 1)
        if type(self.entry_quantity) is not int or self.entry_quantity < 1:
            raise TradingError('凱基進場口數須為正整數')
        self.store = store
        self.output = output
        self.api = None
        self.disconnected = threading.Event()
        self.last_submission = None

    def login(self, user, password, account, broker_id, simulation):
        if importlib.metadata.version('kgisuperpy') != SDK_VERSION:
            raise TradingError(f'請使用 kgisuperpy=={SDK_VERSION}；SDK 介面需重新核對')
        import kgisuperpy as kgi
        self.kgi = kgi
        self.account, self.broker_id = account, broker_id
        self.api = kgi.login(user, password, simulation=simulation)
        if not callable(getattr(self.api, 'set_FutAccount', None)):
            raise TradingError('凱基登入或憑證驗證失敗')
        self.api.set_disconnect_cb(self.disconnected.set)
        accounts = self.api.show_account()
        if not isinstance(accounts, list) or not any(
                str(a.get('account', '')).strip() == account
                and str(a.get('broker_id', '')).strip() == broker_id
                and a.get('account_flag') == '期貨' for a in accounts):
            raise TradingError('設定帳號不在已登入的凱基期貨帳戶中')
        self.api.set_FutAccount(account)
        info = self.api.login_info().get('期貨', {})
        if info != {'account': account, 'broker_id': broker_id}:
            raise TradingError('凱基交易帳號設定未成功')
        self.core = self.api.FutOrder.create_order.__self__
        self.bridge = ExplicitPositionBridge(self.core._Order)
        self.core._Order = self.bridge
        self.api.FutOrder.set_event(lambda _event: None)
        self.check()
        # Verify product identity from KGI rather than an example's symbol.
        products = self.api._ObjOrder.get_Fcontracts(type='df')
        if products is None or not {'ComID', 'ComCName'} <= set(products.columns):
            raise TradingError('凱基商品清單無法確認')
        matches = products[products.ComID.astype(str).str.strip() == self.contract.product]
        if matches.empty or not any('微' in str(name) and '指' in str(name) for name in matches.ComCName):
            raise TradingError('凱基 TMF 商品名稱無法確認為微型臺指')
        self.ensure_no_working_orders()
        self.position()

    def check(self):
        native = getattr(self.api, '_ObjOrder', None)
        if (self.disconnected.is_set() or native is None
                or native.FIsConnected is not True or native.FIsLogon is not True):
            raise TradingError('凱基斷線或登入狀態不明，停止送單')

    def ensure_no_working_orders(self):
        # full=True includes unknown/Pending states that default get_trades omits.
        trades = self.api.FutOrder.get_trades(full=True)
        if not isinstance(trades, dict):
            raise TradingError('委託回補格式不明')
        for key, trade in list(trades.items()):
            if key == '無效單':
                continue
            symbol = str(getattr(trade.order, 'symbol', '') or '').strip()
            if not symbol or symbol.startswith(self.contract.product):
                if enum_name(trade.order_status.status) not in ('Filled', 'Cancelled', 'PartFilled_Cancelled', 'Failed'):
                    raise TradingError('有未結束或不明微台委託，停止送單')

    def position(self, *, deadline=None):
        # KGIsuperpy returns None after waiting for a missing query response.
        # Retry only read-only queries, never malformed responses or orders.
        for attempt in range(1, 4):
            self.check()
            if deadline is not None and time.monotonic() >= deadline:
                raise TradingError('凱基庫存確認逾時，停止，不重送')
            reason = 'no_response'
            try:
                frame = self.api.FutAccount.PositionSum()
            except TimeoutError:
                frame, reason = None, 'timeout'
            self.check()
            # A blocking SDK query must not confirm a fill after its deadline.
            if deadline is not None and time.monotonic() >= deadline:
                raise TradingError('凱基庫存確認逾時，停止，不重送')
            if frame is not None:
                return parse_position(frame, self.account, self.broker_id, self.contract)
            self.store.audit('kgi_inventory_query_unavailable', {
                'reason': reason, 'attempt': attempt, 'max_attempts': 3,
            })
            if attempt == 3:
                raise TradingError('凱基庫存查詢 3 次仍無回應，不能視為空手，停止')
            self.output(f'凱基庫存查詢無回應；6 秒後重查，第 {attempt + 1}/3 次', flush=True)
            until = time.monotonic() + 6
            if deadline is not None:
                until = min(until, deadline)
            while time.monotonic() < until:
                self.check()
                time.sleep(min(.05, max(0, until - time.monotonic())))

    def send_and_confirm(self, delta, phase, target):
        if type(delta) is not int or not delta or phase not in ('flatten', 'entry'):
            raise TradingError('委託方向、數量或階段不符')
        if phase == 'entry' and (abs(delta) != self.entry_quantity or target != delta):
            raise TradingError('進場口數須符合 KGI_UNIT 設定')
        self.check()
        self.ensure_no_working_orders()
        if self.position() != target - delta:
            raise TradingError('送單前庫存已變動，停止核對')
        if phase == 'flatten' and target != 0:
            raise TradingError('平倉目標須為零')
        # KGI test environment requests >1 second between order tests.
        if self.last_submission is not None:
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_submission)))
        self.check()
        self.bridge.phase = phase
        self.last_submission = time.monotonic()
        try:
            trade = self.api.FutOrder.create_order(
                action=self.kgi.Action.Buy if delta > 0 else self.kgi.Action.Sell,
                symbol=self.contract.symbol, qty=abs(delta), price=self.kgi.PriceType.MKT,
                time_in_force=self.kgi.TimeInForce.IOC)
        finally:
            self.bridge.phase = None
        if trade is None:
            raise TradingError('凱基未回傳委託，不重送')
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.check()
            if filled_quantity(trade, abs(delta)):
                # Inventory can lag the fill report; wait, never submit again.
                position = self.position(deadline=deadline)
                if position == target:
                    self.store.audit('kgi_fill', {'phase': phase, 'symbol': self.contract.symbol,
                        'order_id': str(trade.order.order_id), 'quantity': abs(delta)})
                    return position
            time.sleep(0.2)
        raise TradingError('委託／成交／庫存確認逾時，停止，不重送')

    def close(self):
        if self.api and callable(getattr(self.api, 'logout', None)):
            self.api.logout()
