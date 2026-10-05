"""Windows Capital DLL adapter; each order is confirmed by fills AND inventory."""
from __future__ import annotations
import os
import time
import threading
import ctypes
import re
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from h_core import TradingError, parse_inventory

BASE = Path(__file__).resolve().parent
_dll_search = os.add_dll_directory(str(BASE / 'libs'))
from SKDLLPython import SK, FUTUREPROXYORDER2

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = wintypes.LPARAM


def pump_messages():
    message = wintypes.MSG()
    for _ in range(1000):
        if not _user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            break
        _user32.TranslateMessage(ctypes.byref(message))
        _user32.DispatchMessageW(ctypes.byref(message))


def build_order(account, contract, delta, phase):
    contract.validate()
    if not isinstance(delta, int) or isinstance(delta, bool) or not delta:
        raise TradingError('送單口數須為非零整數')
    if phase not in ('flatten', 'entry'):
        raise TradingError('委託階段錯誤')
    return FUTUREPROXYORDER2(
        strStockNo=contract.order_code.encode('ansi'), strFullAccount=account.encode('ansi'),
        strSettleYM=contract.settle_ym.encode('ansi'), strPrice=b'0',
        strOrderType=b'1' if phase == 'flatten' else b'0',
        nBuySell=0 if delta > 0 else 1, nQty=abs(delta),
        nPriceFlag=0, nTradeType=1, nDayTrade=0, nReserved=0,
        strSeqNo=b'', strBookNo=b'', strStrike=b'', strSettleYM2=b'', strStrike2=b'',
        strStockNo2=b'', nCP=0, nCP2=0, nBuySell2=0,
    )


class CapitalBroker:
    def __init__(self, config, store, output=print):
        self.config, self.store, self.output = config, store, output
        self.login_id = ''
        self.account = config['account']
        self.contract = config['contract']
        self.lock = threading.RLock()
        self.reports = []
        self.proxy_reports = []
        self.proxy_ready = threading.Event()
        self.reply_ready = threading.Event()
        self.quote_ready = threading.Event()
        self.replay_complete = threading.Event()
        self.disconnected = threading.Event()
        self.fault = None
        self.waiting_target = None
        SK.OnReplyMessage(lambda _id, _msg: None)
        SK.OnConnection(self.on_connection)
        SK.OnComplete(lambda who: self.replay_complete.set() if who == self.login_id else None)
        SK.OnNewData(self.on_report)
        SK.OnProxyOrder(self.on_proxy)

    def on_connection(self, who, code):
        source = 'login' if who == self.login_id else 'quote' if who == 'SKQuote' else 'other'
        self.output(f'連線事件：code={code} source={source}')
        # DLL 2.13.59 reports gateway login success as 0 for reply/proxy.
        # Connect these sequentially so a reply event cannot arm proxy readiness.
        if code == 0 and who == self.login_id:
            if self.waiting_target == 0:
                self.reply_ready.set()
        if code == 5001 and who == self.login_id:
            self.proxy_ready.set()
        elif code == 3001 and who == self.login_id:
            self.reply_ready.set()
        elif code == 3003 and who == 'SKQuote':
            self.quote_ready.set()
        elif code in (3002, 3033, 5002, 5003, 5004, 5011):
            # Remain stopped after disconnection; restarting starts at EOF.
            self.disconnected.set()

    def on_report(self, who, report):
        if who != self.login_id:
            return
        try:
            fields = {k: str(getattr(report, k)).strip() for k in
                      ('KeyNo', 'SeqNo', 'MarketType', 'Type', 'OrderErr', 'Broker', 'CustNo',
                       'BuySell', 'ComId', 'OrderNo', 'Qty', 'ExecutionNo', 'YearMonth1',
                       'ComId1', 'ErrorMsg', 'CancelOrderMarkByExchange', 'TradeDate')}
            with self.lock:
                self.reports.append(fields)
        except Exception as exc:
            self.fault = f'回報格式錯誤：{type(exc).__name__}'

    def on_proxy(self, stamp, code, message):
        with self.lock:
            self.proxy_reports.append((stamp, code, str(message)))

    def check(self):
        pump_messages()
        if self.fault or self.disconnected.is_set():
            raise TradingError(self.fault or '群益連線中斷；停止，重新啟動時跳過停機訊號')

    def login(self, user, password, *, diagnose=False):
        self.login_id = user
        result = SK.Login(user, password, 0)
        if result.Code != 0:
            raise TradingError(f'登入失敗：{result.Code} {SK.GetMessage(result.Code)}')
        if not self.account:
            accounts = result.TFAccounts
            for index, account in enumerate(accounts, 1):
                self.output(f'{index}. 國內期貨帳號 {account.FullAccount}')
            selection = input('請選擇帳號序號：').strip()
            if not selection.isdigit() or not 1 <= int(selection) <= len(accounts):
                raise TradingError('未選擇有效帳號')
            self.account = accounts[int(selection) - 1].FullAccount
        if self.account not in {a.FullAccount for a in result.TFAccounts}:
            matches = [a for a in result.TFAccounts if a.Account == self.account
                       or (len(self.account) >= len(a.Account) and a.FullAccount.endswith(self.account))]
            if len(matches) == 1:
                # A seven-digit account uniquely identifies the selected account;
                # use the broker-provided branch prefix for every request.
                self.account = matches[0].FullAccount
            else:
                self.output('帳號比對診斷（遮蔽）：' + str({
                    'configured': '*' * max(0, len(self.account) - 4) + self.account[-4:],
                    'available': [{'full': '*' * max(0, len(a.FullAccount) - 4) + a.FullAccount[-4:],
                                   'account_length': len(a.Account), 'full_length': len(a.FullAccount),
                                   'branch_length': len(a.Branch)} for a in result.TFAccounts],
                }))
                raise TradingError('設定帳號不在登入取得的國內期貨帳戶清單；請核對完整帳號')
        if self.store.pending() and not diagnose:
            raise TradingError('上次委託未確認，需人工核對 runtime/live.sqlite；不自動清除')
        for target, event in ((0, self.reply_ready), (1, self.quote_ready), (4, self.proxy_ready)):
            self.waiting_target = target
            code = SK.ManageServerConnection('' if target == 1 else user, 0, target)
            if code != 0:
                raise TradingError(f'連線請求失敗：{code}')
            deadline = time.monotonic() + 30
            while not event.is_set() and time.monotonic() < deadline:
                self.check()
                time.sleep(.05)
            if not event.is_set():
                raise TradingError(f'連線主機 {target} 就緒逾時')
            self.waiting_target = None
        required_events = ((self.quote_ready, '商品'),) if diagnose else (
            (self.proxy_ready, '下單'), (self.reply_ready, '回報'),
            (self.quote_ready, '商品'), (self.replay_complete, '委託回補'))
        for event, label in required_events:
            deadline = time.monotonic() + 30
            while not event.is_set() and time.monotonic() < deadline:
                self.check()
                time.sleep(0.05)
            if not event.is_set():
                raise TradingError(f'{label}連線／回補逾時')
            self.check()
        if not diagnose:
            self.validate_contract()
            self.check_outstanding()
            self.position()

    def validate_contract(self):
        c = self.contract
        c.validate()
        found = []
        # T and all-session futures market inventories may duplicate contracts.
        for market in (2,):
            listing = SK.RequestStockList(market)
            for group in listing.AllTypeLists:
                found.extend(item for item in group.Items if item.strQuoteCode == c.quote_code)
        if not found:
            raise TradingError('群益商品清單找不到設定的報價代碼')
        for item in found:
            if not (item.strStockName.startswith('微台') and c.quote_code.startswith('TM')):
                raise TradingError('設定商品不是群益微型台指合約')
            expiry = ''.join(ch for ch in item.strExpiryDate if ch.isdigit())
            if c.order_code != 'FITM' or len(expiry) != 8 or expiry[:6] != c.settle_ym:
                raise TradingError('群益委託代碼／契約月份與設定不符')
            now = datetime.now(timezone(timedelta(hours=8)))
            if expiry < now.strftime('%Y%m%d') or (expiry == now.strftime('%Y%m%d') and now.strftime('%H%M') >= '1345'):
                raise TradingError('契約已到期，請核對換月設定')

    def is_micro(self, report):
        return (report['MarketType'] == 'TF'
                and report['Broker'] + report['CustNo'] == self.account
                and any(report[k].startswith((self.contract.order_code, self.contract.inventory_prefix))
                        for k in ('ComId', 'ComId1')))

    def check_outstanding(self):
        with self.lock:
            reports = list(self.reports)
        ledger = {}
        executions = set()
        for r in reports:
            if not self.is_micro(r):
                continue
            if r['OrderErr'] == 'Y':
                continue
            if r['OrderErr'] != 'N':
                raise TradingError('存在逾時或無法識別的微台回報')
            key = r['TradeDate'] + ':' + (r['OrderNo'] or r['SeqNo'] or r['KeyNo'])
            if key.endswith(':') or not r['Qty'].isdigit():
                raise TradingError('回報欠缺委託識別碼或數量')
            qty = int(r['Qty'])
            state = ledger.setdefault(key, {'ordered': None, 'ended': 0})
            if r['Type'] == 'N':
                if state['ordered'] not in (None, qty):
                    raise TradingError('委託回報數量不一致')
                state['ordered'] = qty
            elif r['Type'] == 'D':
                ident = key, r['ExecutionNo']
                if not r['ExecutionNo']:
                    raise TradingError('成交回報缺少 ExecutionNo')
                if ident not in executions:
                    executions.add(ident)
                    state['ended'] += qty
            elif r['Type'] == 'C':
                if state.get('cancel_seen'):
                    raise TradingError('重複取消回報需人工核對')
                state['cancel_seen'] = True
                state['ended'] += qty
            elif r['Type'] != 'P':
                raise TradingError('微台委託回報類型尚未支援')
        if any(s['ordered'] is None or s['ordered'] != s['ended'] for s in ledger.values()):
            raise TradingError('微台存在未結束或無法核對的委託；停止送單')

    def position(self):
        self.check()
        return parse_inventory(SK.GetOpenInterestGW(self.login_id, self.account, 0),
                               self.account, self.login_id, self.contract)

    def send_and_confirm(self, delta, phase, target):
        self.check()
        self.validate_contract()
        self.check_outstanding()
        order = build_order(self.account, self.contract, delta, phase)
        with self.lock:
            start = len(self.reports)
            proxy_start = len(self.proxy_reports)
        code, key = SK.SendFutureProxyOrder(self.login_id, order)
        self.store.audit('sdk_return', {'code': code, 'key': key, 'phase': phase})
        # Native 2.13.59 may return a nonzero submission stamp alongside ORKEY.
        # Acceptance must still come from the correlated asynchronous callback.
        if not key.strip() or (code != 0 and not key.strip().startswith('SKAPI')):
            raise TradingError(f'送單回傳未確認：{code}，停止，不重送')
        key = key.strip()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.check()
            with self.lock:
                proxies = list(self.proxy_reports[proxy_start:])
                correlated = [p for p in proxies if key in p[2]]
                identifiers = {key}
                for _stamp, _code, message in correlated:
                    identifiers.update(re.findall(r'SeqNo:\s*(\d+)', message))
                rows = [r for r in self.reports[start:] if self.is_micro(r)
                        and identifiers.intersection((r['KeyNo'], r['SeqNo']))]
            # Any proxy rejection while single-flight stops execution conservatively.
            if any(p[1] != 0 for p in proxies):
                raise TradingError('Proxy 送單回報失敗，需人工核對')
            if any(re.search(r'\[\d+\]', p[2]) for p in correlated):
                raise TradingError('此筆 ORKEY 收到券商拒單回報；停止，不重送')
            fills = {}
            for r in rows:
                if r['OrderErr'] != 'N':
                    raise TradingError('委託回報失敗／逾時；停止，不重送')
                if r['Type'] == 'D':
                    if not r['ExecutionNo'] or not r['Qty'].isdigit():
                        raise TradingError('成交資料不完整')
                    if not r['BuySell'].startswith('B' if delta > 0 else 'S'):
                        raise TradingError('成交買賣方向不符')
                    fills[r['ExecutionNo']] = int(r['Qty'])
            filled = sum(fills.values())
            if filled > abs(delta):
                raise TradingError('成交口數超出委託')
            if correlated and filled == abs(delta) and self.position() == target:
                return target
            if any(r['Type'] in ('C', 'S', 'U', 'B') for r in rows):
                raise TradingError('委託取消、部分成交或變更；停止，不重送')
            time.sleep(0.5)
        raise TradingError('30 秒內未核對到完整成交及庫存；停止，不重送')

    def close(self):
        for target in (4, 1, 0):
            if self.login_id:
                SK.ManageServerConnection('' if target == 1 else self.login_id, 1, target)
