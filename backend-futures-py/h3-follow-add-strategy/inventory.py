"""Read-only startup and daily 08:35 TMF inventory reports (Taipei time)."""
from __future__ import annotations

from datetime import time
import json

from ef_trade_runtime import position_text


class InventoryReporter:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store
        self.store.db.execute('CREATE TABLE IF NOT EXISTS h3_inventory_reports '
                              '(day TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL)')
        self.store.db.commit()

    def _reserve(self, now):
        with self.store.db:
            cursor = self.store.db.execute('INSERT OR IGNORE INTO h3_inventory_reports VALUES (?,?,?)',
                (now.date().isoformat(), 'attempted', json.dumps({'attempted_at': now.isoformat()})))
        return cursor.rowcount == 1

    def _report(self, now, label):
        snapshot = dict(checked_at=now.isoformat(), kind=label)
        try:
            actual = self.engine.broker.position()
            expected = self.engine.state().expected
            snapshot.update(status='success', actual=actual, expected=expected, matches=actual == expected)
            match = '一致' if actual == expected else '不一致；等待新H3訊號時按券商實際庫存全平，再依UNIT進場'
            message = (f'✅【API_KEY｜H3跟單加碼｜{label}】\n'
                       f'時間：{now:%Y-%m-%d %H:%M:%S}（台北）\n'
                       f'券商微台部位：{position_text(actual)}\n'
                       f'策略帳本：{position_text(expected)}\n'
                       f'核對：{match}\n僅查詢，未送單、未平倉。')
        except Exception as exc:
            # Broker exception text may contain credentials or personal data.
            snapshot.update(status='failed', error=type(exc).__name__)
            message = (f'🚨【API_KEY｜H3跟單加碼｜{label}失敗】\n'
                       f'時間：{now:%Y-%m-%d %H:%M:%S}（台北）\n'
                       f'錯誤：{type(exc).__name__}；請人工核對庫存。僅查詢，未送單。')
        self.store.audit('h3_inventory_report', snapshot)
        print(message, flush=True)
        self.engine.notify(message)
        return snapshot

    def _save(self, now, snapshot):
        with self.store.db:
            self.store.db.execute('UPDATE h3_inventory_reports SET status=?,payload=? WHERE day=?',
                (snapshot['status'], json.dumps(snapshot, ensure_ascii=False), now.date().isoformat()))

    def startup(self, now):
        # Every boot reports the current inventory. A post-08:35 boot also counts
        # as today's scheduled check, so the next loop does not notify twice.
        reserved = now.time() >= time(8, 35) and self._reserve(now)
        snapshot = self._report(now, '啟動查倉')
        if reserved:
            self._save(now, snapshot)
        return snapshot

    def daily(self, now):
        if now.time() < time(8, 35) or not self._reserve(now):
            return False
        snapshot = self._report(now, '08:35查倉')
        self._save(now, snapshot)
        return True
