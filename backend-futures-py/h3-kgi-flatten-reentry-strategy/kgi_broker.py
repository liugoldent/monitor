"""KGISuperPy 2.1.2 adapter. Imports/login are lazy; no order retry is permitted."""
from __future__ import annotations
import importlib.metadata
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from shared_h import OrderRejected, TradingError

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
    def __init__(self, native, store=None):
        self.native = native
        self.store = store
        self.phase = None

    def __getattr__(self, name):
        return getattr(self.native, name)

    def Order(self, *args):
        if len(args) != 17 or args[0] != 0 or args[12] != 3 or self.phase not in ('flatten', 'entry'):
            raise TradingError('SDK 委託介面不符，停止送單')
        args = list(args)
        args[12] = 1 if self.phase == 'flatten' else 0
        if self.store is not None:
            self.store.audit('kgi_submission', dict(pending=self.store.pending(),
                request_id=str(args[2]), symbol=args[6], side=args[7], quantity=args[11]))
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
        self.mdd_sizing = config.get('mdd_sizing', False)
        self.last_fill = None
        if type(self.entry_quantity) is not int or self.entry_quantity < 1:
            raise TradingError('凱基進場口數須為正整數')
        self.store = store
        self.output = output
        self.api = None
        self.simulation = None
        self.disconnected = threading.Event()
        self.last_submission = None
        self._next_inventory_query = 0.0
        self._inventory_interval = 6

    def login(self, user, password, account, broker_id, simulation):
        self.contract.validate()
        if type(simulation) is not bool:
            raise TradingError('凱基登入環境須明確指定測試或正式')
        self.simulation = simulation
        if importlib.metadata.version('kgisuperpy') != SDK_VERSION:
            raise TradingError(f'請使用 kgisuperpy=={SDK_VERSION}；SDK 介面需重新核對')
        import kgisuperpy as kgi
        self.kgi = kgi
        self.account, self.broker_id = account, broker_id
        self.login_id = user
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
        self._next_inventory_query = time.monotonic() + 6
        info = self.api.login_info().get('期貨', {})
        if info != {'account': account, 'broker_id': broker_id}:
            raise TradingError('凱基交易帳號設定未成功')
        self.core = self.api.FutOrder.create_order.__self__
        self.bridge = ExplicitPositionBridge(self.core._Order, self.store)
        self.core._Order = self.bridge
        self.api.FutOrder.set_event(lambda _event: None)
        self.check()
        # SDK get_Fcontracts / RetrieveFProduct is an OVERSEAS futures list.
        # It cannot validate domestic TMF. Contract.validate strictly limits this
        # adapter to TMF with a matching monthly symbol. This is local validation,
        # not a claim that the broker has accepted an order for this contract.
        self.store.audit('kgi_domestic_contract_checked', {
            'product': self.contract.product, 'symbol': self.contract.symbol,
            'settle_ym': self.contract.settle_ym, 'source': 'local_TMF_contract_rule',
            'broker_product_metadata_verified': False,
        })
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
                    # SDK 2.1.2 can leave order_status=None after a native
                    # rejection. Only the linked full report proves it terminal.
                    if self.trade_rejection(trade) is None:
                        raise TradingError('有未結束或不明微台委託，停止送單')

    def wait_inventory(self, until, deadline=None):
        while time.monotonic() < until:
            self.check()
            if deadline is not None and time.monotonic() >= deadline:
                raise TradingError('凱基庫存確認逾時，停止，不重送')
            remaining = until - time.monotonic()
            if deadline is not None:
                remaining = min(remaining, deadline - time.monotonic())
            time.sleep(min(.05, max(0, remaining)))
        if deadline is not None and time.monotonic() >= deadline:
            raise TradingError('凱基庫存確認逾時，停止，不重送')

    def position(self, *, deadline=None):
        # KGIsuperpy returns None after waiting for a missing query response.
        # Retry only read-only queries, never malformed responses or orders.
        for attempt in range(1, 4):
            self.wait_inventory(getattr(self, '_next_inventory_query', 0), deadline)
            self.check()
            if deadline is not None and time.monotonic() >= deadline:
                raise TradingError('凱基庫存確認逾時，停止，不重送')
            reason = 'no_response'
            try:
                self._next_inventory_query = time.monotonic() + self._inventory_interval
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
            self._next_inventory_query = max(self._next_inventory_query, time.monotonic() + 6)

    def redact(self, message):
        text = str(message)
        for value in (getattr(self, 'account', ''), getattr(self, 'broker_id', ''), getattr(self, 'login_id', '')):
            if value:
                text = text.replace(value, '[redacted]')
        return text

    def rejected_report(self, payload, *, identities=None, legacy_window=None):
        """Require a full native new-order rejection, never just a failed SDK operation."""
        core = getattr(self, 'core', None)
        rows = getattr(core, 'order_list', None)
        deals = getattr(core, 'deal_list', None)
        if not isinstance(rows, list) or not isinstance(deals, list):
            return None
        expected_pe = ('O', '0') if payload['phase'] == 'entry' else ('C', '1')
        matches = {}
        for row in list(rows):
            if not isinstance(row, dict):
                return None
            code = str(row.get('ErrCode', '')).strip()
            cnt = str(row.get('CNT', '')).strip()
            if not (str(row.get('ActNo', '')).strip() == self.account
                    and str(row.get('BrokerID', '')).strip() == self.broker_id
                    and row.get('OrderFunc') == 'I' and code not in ('', '0')
                    and row.get('Symbol') == self.contract.symbol and cnt
                    and row.get('BS') == ('B' if payload['side'] == 'buy' else 'S')
                    and str(row.get('BeforeQty', '')).strip() == '0'
                    and str(row.get('AfterQty', '')).strip() == '0'
                    and row.get('TimeInForce') == 'I' and row.get('PriceFlag') == '1'
                    and row.get('PositionEffect') in expected_pe):
                continue
            if identities is not None and cnt not in identities:
                continue
            if legacy_window is not None:
                # The pre-upgrade manual test has no stored CNT. Restrict its
                # migration to this exact, operator-observed margin rejection.
                if code != '3296':
                    continue
                try:
                    stamp = datetime.strptime(str(row['TradeDate']) + str(row['ReportTime'])[:6],
                                              '%Y%m%d%H%M%S').replace(tzinfo=timezone(timedelta(hours=8)))
                except (KeyError, ValueError):
                    continue
                if not legacy_window[0] - timedelta(seconds=1) <= stamp <= legacy_window[1] + timedelta(seconds=1):
                    continue
            matches[(cnt, str(row.get('TradeDate', '')), str(row.get('ReportTime', '')))] = row
        if len(matches) != 1:
            return None
        row = next(iter(matches.values()))
        cnt = str(row['CNT']).strip()
        order_id = str(row.get('OrderNo', '')).strip()
        for deal in list(deals):
            if not isinstance(deal, dict):
                return None
            if (str(deal.get('CNT', '')).strip() == cnt
                    or (order_id and str(deal.get('OrderNo', '')).strip() == order_id)):
                return None
        return dict(cnt=cnt, order_id=order_id, error_code=str(row['ErrCode']),
                    reason=self.redact(row.get('ErrMsg', '券商拒單')),
                    symbol=self.contract.symbol, report_time=str(row.get('ReportTime', '')),
                    trade_date=str(row.get('TradeDate', '')), zero_fills=True)

    def trade_rejection(self, trade, payload=None):
        status = getattr(trade, 'order_status', None)
        deals = getattr(status, 'deals', None)
        if not isinstance(deals, list) or deals:
            return None
        failed_ids = {str(op.nid).strip() for op in (getattr(trade, 'operations', None) or [])
                      if enum_name(getattr(op, 'task', None)) == 'NewOrder'
                      and enum_name(getattr(op, 'status', None)) == 'Failed'
                      and getattr(op, 'nid', None)}
        if not failed_ids:
            return None
        if payload is None:
            rows = getattr(getattr(self, 'core', None), 'order_list', [])
            candidates = [r for r in rows if isinstance(r, dict) and str(r.get('CNT', '')).strip() in failed_ids]
            kinds = {(r.get('BS'), r.get('PositionEffect')) for r in candidates}
            if len(kinds) != 1:
                return None
            side, effect = next(iter(kinds))
            if side not in ('B', 'S') or effect not in ('O', '0', 'C', '1'):
                return None
            payload = dict(side='buy' if side == 'B' else 'sell',
                           phase='entry' if effect in ('O', '0') else 'flatten')
        return self.rejected_report(payload, identities=failed_ids)

    def recover_rejected_pending(self):
        pending = self.store.pending()
        if not pending:
            return False
        if pending.get('phase') not in ('entry', 'flatten'):
            raise TradingError('未確認委託階段不明，保留阻擋')
        identities = None
        for detail, in self.store.db.execute("SELECT detail FROM audit WHERE event='kgi_order_identity' ORDER BY id DESC"):
            saved = json.loads(detail)
            if saved.get('pending') == pending:
                identities = set(saved['identities'])
                break
        legacy_window = None
        if identities is None:
            # One narrowly scoped migration for the test the operator already
            # observed as rejected. All other unidentified guards remain blocked.
            if pending != dict(trigger='manual-production-buy-one-v1', phase='entry',
                               previous=0, target=1, side='buy', quantity=1):
                raise TradingError('舊委託缺少可關聯的券商識別，保留阻擋')
            marker = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kgi_manual_production_test'").fetchone()
            if not marker or self.store.db.execute("SELECT status FROM kgi_manual_production_test WHERE id=1").fetchone() != ('attempted',):
                raise TradingError('舊單不是尚待核對的一次性正式測試，保留阻擋')
            prepared = stopped = None
            for at, event, detail in self.store.db.execute("SELECT at,event,detail FROM audit ORDER BY id"):
                data = json.loads(detail)
                if event == 'prepared' and data == pending:
                    prepared = datetime.fromisoformat(at)
                    stopped = None
                elif prepared and event == 'stopped' and data.get('stage') == 'manual_production_test' and data.get('pending_order') is True:
                    stopped = datetime.fromisoformat(at)
            if prepared is None or stopped is None:
                raise TradingError('舊測試單缺少完整停止紀錄，保留阻擋')
            legacy_window = (prepared, stopped)
        evidence = self.rejected_report(pending, identities=identities, legacy_window=legacy_window)
        if evidence is None:
            raise TradingError('券商回補無法唯一確認整筆拒單及零成交，保留阻擋')
        self.ensure_no_working_orders()
        if self.position() != pending['previous']:
            raise TradingError('拒單核對時庫存與送單前不符，保留阻擋')
        self.ensure_no_working_orders()
        evidence['source'] = 'broker_replay_rejection'
        evidence['position'] = pending['previous']
        self.store.resolve_rejected(pending, evidence)
        self.output(f"已核對舊凱基拒單：錯誤碼 {evidence['error_code']}；零成交、庫存一致。舊訊號不重送。", flush=True)
        return True


    def send_and_confirm(self, delta, phase, target):
        self.last_fill = None
        if type(delta) is not int or not delta or phase not in ('flatten', 'entry'):
            raise TradingError('委託方向、數量或階段不符')
        if phase == 'entry' and (abs(delta) != self.entry_quantity or target != delta):
            raise TradingError('進場口數須符合 KGI_UNIT 設定')
        if self.mdd_sizing and phase == 'entry' and abs(delta) not in (1, 2):
            raise TradingError('回撤加口策略只允許1口或2口')
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
        saved_ids = None
        payload = self.store.pending()
        while time.monotonic() < deadline:
            self.check()
            operations = list(getattr(trade, 'operations', None) or [])
            identities = {str(op.nid).strip() for op in operations
                          if enum_name(getattr(op, 'task', None)) == 'NewOrder' and getattr(op, 'nid', None)}
            if payload and identities and identities != saved_ids:
                self.store.audit('kgi_order_identity', dict(pending=payload, identities=sorted(identities)))
                saved_ids = identities
            failed = any(enum_name(getattr(op, 'status', None)) == 'Failed' for op in operations)
            if failed:
                evidence = self.trade_rejection(trade, payload) if payload else None
                if evidence and not list(getattr(trade.order_status, 'deals', []) or []):
                    previous = target - delta
                    self.ensure_no_working_orders()
                    if self.position(deadline=deadline) != previous:
                        raise TradingError('拒單後庫存不符，保留委託阻擋')
                    self.ensure_no_working_orders()
                    evidence['position'] = previous
                    self.store.audit('kgi_order_rejected', dict(phase=phase, **evidence))
                    raise OrderRejected(f"凱基整筆拒單 {evidence['error_code']}：{evidence['reason']}；本訊號不重送", evidence)
                # An early Pending failure can precede the final native report.
                # Keep waiting for proof; a timeout still preserves the guard.
                time.sleep(.2)
                continue
            if filled_quantity(trade, abs(delta)):
                # Inventory can lag the fill report; wait, never submit again.
                position = self.position(deadline=deadline)
                if position == target:
                    if self.mdd_sizing:
                        from mdd_sizing import execution_fill
                        self.last_fill = execution_fill(trade, abs(delta), self.contract.symbol)
                    self.store.audit('kgi_fill', {'phase': phase, 'symbol': self.contract.symbol,
                        'order_id': str(trade.order.order_id), 'quantity': abs(delta)})
                    return position
            time.sleep(0.2)
        raise TradingError('委託／成交／庫存確認逾時，停止，不重送')

    def close(self):
        if self.api and callable(getattr(self.api, 'logout', None)):
            self.api.logout()
