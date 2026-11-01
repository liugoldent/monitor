"""Read-only broker reconciliation for the user's manual expiry-day roll.

Only the ledger changes here. All automated orders keep using the adopted
concrete contract, including before TMFR1 has switched to that month.
"""
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime, time as daytime, timezone
import json
from pathlib import Path
import sqlite3
import time

import shioaji_tmf_target as shared
from ef_trade_runtime import now_local
from runtime import LedgerError
from strategy import State, number


def ledger_symbol(store, fallback):
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='pyramid_state'").fetchone()
    row = store.db.execute('SELECT symbol FROM pyramid_state WHERE id=1').fetchone() if exists else None
    return row[0] if row else fallback


class ManualRollover:
    def __init__(self, engine, backups, *, clock=now_local, monotonic=time.monotonic):
        self.engine, self.store, self.broker = engine, engine.store, engine.broker
        self.backups, self.clock, self.monotonic = Path(backups), clock, monotonic
        self.candidate = None
        self.notice = None
        self.notice_at = None
        self.store.db.execute('CREATE TABLE IF NOT EXISTS h3_contract_metadata '
                              '(code TEXT PRIMARY KEY, month TEXT NOT NULL, expiry TEXT NOT NULL)')
        self.store.db.commit()
        self.remember_contracts()

    def remember_contracts(self):
        # Broker dates include holiday adjustments. Do not infer third Wednesdays.
        with self.store.db:
            for contract in self.broker.api.Contracts.Futures.TMF:
                code = shared._position_code(contract)
                month = shared._position_value(contract, 'delivery_month')
                expiry = shared._position_value(contract, 'delivery_date')
                if isinstance(expiry, datetime):
                    expiry = expiry.date().isoformat()
                elif isinstance(expiry, date):
                    expiry = expiry.isoformat()
                if code in {'TMFR1', 'TMFR2'} or not code.startswith('TMF'):
                    continue
                if not isinstance(month, str) or len(month) != 6 or not month.isdigit():
                    continue
                try:
                    date.fromisoformat(expiry)
                except (TypeError, ValueError):
                    continue
                self.store.db.execute('INSERT OR REPLACE INTO h3_contract_metadata VALUES (?,?,?)',
                                      (code, month, expiry))

    def due(self):
        now = self.clock()
        row = self.store.db.execute('SELECT month,expiry FROM h3_contract_metadata WHERE code=?',
                                    (self.engine.symbol,)).fetchone()
        if row is None:
            raise LedgerError('缺少帳本合約的券商到期日，不能確認手動換月')
        month, expiry = row
        day = date.fromisoformat(expiry)
        if now.date() < day or (now.date() == day and now.time().replace(tzinfo=None) < daytime(13, 25)):
            return None
        year, month_number = divmod(int(month), 100)
        next_month = f'{year + (month_number == 12):04d}{month_number % 12 + 1:02d}'
        matches = self.store.db.execute('SELECT code,expiry FROM h3_contract_metadata WHERE month=?',
                                        (next_month,)).fetchall()
        if len(matches) != 1 or now.date() >= date.fromisoformat(matches[0][1]):
            raise LedgerError('次月合約或換月期限不明，停止自動銜接')
        return matches[0][0]

    def waiting(self, reason):
        self.candidate = None
        tick = self.monotonic()
        if reason != self.notice or self.notice_at is None or tick - self.notice_at >= 300:
            message = '【API_KEY｜H3等待手動換月】' + reason + '；暫停送單，完成換倉核對後自動繼續。'
            print(message, flush=True)
            self.store.audit('pyramid_rollover_waiting', dict(reason=reason))
            self.engine.notify(message)
            self.notice, self.notice_at = reason, tick
        return False

    def snapshot(self, target):
        shared._refresh_status(self.broker.api)
        positions = shared._read_positions(self.broker.api)
        shared.validate_tmf_account(self.broker.api, positions, contract_code=target)
        live = [p for p in positions if shared._position_code(p).startswith('TMF')
                and shared._position_quantity(p)]
        if len(live) != 1:
            raise shared.BrokerOrderError('等待舊月全平且次月只有一筆單向庫存')
        position = live[0]
        actual = shared._position_side(position) * shared._position_quantity(position)
        if not 1 <= abs(actual) <= 2:
            raise shared.BrokerOrderError('次月部位須為單向1或2口')
        # With one lot, inventory cost is the new round's cost basis. For two
        # lots the first fill is unknown, and the round has already used its add.
        price = None
        if abs(actual) == 1:
            try:
                price = str(number(shared._position_value(position, 'price'), positive=True))
            except ValueError:
                pass  # Missing cost disables adds, never exits.
        return dict(contract=target, actual=actual, first_price=price)

    def synchronize(self, *, allow_adopt=True):
        target = self.due()
        if target is None:
            return True
        if not allow_adopt:
            return False
        if self.store.pending() or self.broker.guard.get('pending'):
            raise LedgerError('手動換月前仍有策略未確認委託，不覆蓋帳本或重送')
        row = self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
        old = State(**json.loads(row[1])).validate()
        try:
            evidence = self.snapshot(target)
        except shared.BrokerOrderError:
            return self.waiting('尚未確認舊月已清空、次月單向庫存及所有微台委託已結束')
        tick = self.monotonic()
        if self.candidate is None or self.candidate[0] != evidence:
            self.candidate = (evidence, tick)
            return False
        if tick - self.candidate[1] < 2:
            return False
        self.backups.mkdir(parents=True, exist_ok=True)
        backup = self.backups / ('api_key.before_rollover_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite')
        with closing(sqlite3.connect(backup)) as destination:
            self.store.db.backup(destination)
        # Read again after backup; do not commit a stale inventory snapshot.
        try:
            if self.snapshot(target) != evidence:
                return self.waiting('次月庫存仍在變動，等待換倉完成')
        except shared.BrokerOrderError:
            return self.waiting('換倉核對期間庫存或委託改變')
        state = State(expected=evidence['actual'], first_price=evidence['first_price'],
                      added=abs(evidence['actual']) == 2,
                      round_key=f'manual-roll:{target}:{self.clock().isoformat()}')
        state.validate()
        detail = dict(previous_contract=row[0], previous_state=asdict(old),
                      evidence=evidence, new_state=asdict(state), backup=str(backup),
                      checked_at=self.clock().isoformat())
        with self.store.db:
            # Acquire the writer lock before checking that no other writer won.
            self.store.db.execute('BEGIN IMMEDIATE')
            current = self.store.db.execute('SELECT symbol,payload FROM pyramid_state WHERE id=1').fetchone()
            guard_row = self.store.db.execute('SELECT payload FROM shioaji_order_state WHERE id=1').fetchone()
            if (current != row or self.store.pending()
                    or (guard_row and json.loads(guard_row[0]).get('pending'))):
                raise LedgerError('換月核對期間帳本或委託改變，不覆蓋')
            self.store.db.execute('UPDATE pyramid_state SET symbol=?,payload=? WHERE id=1',
                                  (target, json.dumps(asdict(state))))
            self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)',
                (datetime.now(timezone.utc).isoformat(), 'pyramid_manual_rollover', json.dumps(detail)))
        self.engine.symbol = self.broker.contract_code = target
        self.candidate = None
        message = (f'【API_KEY｜H3手動換月銜接完成】{row[0]} → {target}；'
                   f'核對部位={state.expected:+d}口；已備份舊帳本，銜接本身未送單。'
                   '後續新H3訊號照常處理，平倉／進場／加碼皆使用此月份。')
        if state.first_price is None and not state.added:
            message += '缺少庫存成本，本輪不加碼，仍正常跟隨平倉。'
        print(message, flush=True)
        self.engine.notify(message)
        return True
