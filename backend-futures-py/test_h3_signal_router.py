"""Temporary SQLite fixtures and fake brokers only; never connect to trading APIs."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from h3_signal_router import Router, TZ, tg_signal


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vendor = self.root/'vendor.tde'
        self.tg = self.root/'tg.jsonl'
        self.tg.touch()
        self.now = datetime.now(TZ).replace(microsecond=0)
        self.connection = sqlite3.connect(self.vendor)
        self.addCleanup(self.connection.close)
        self.connection.executescript('''
            CREATE TABLE HISTORY (DATE,TIME,ACTION,POSITION,SIGNAL_ID,BLOCK);
            CREATE TABLE HISTORY_SETTING (BLOCK INTEGER PRIMARY KEY,SIGNAL_ID,GROUP_NAME);
            INSERT INTO HISTORY_SETTING VALUES (1,123,'浩克3V3');
            INSERT INTO HISTORY_SETTING VALUES (2,124,'另一個策略');
        ''')
        self.connection.commit()
        self.router = self.open_router()

    def open_router(self):
        router = Router(self.vendor, self.tg, self.root/'records')
        self.addCleanup(router.db.close)
        return router

    def local(self, direction, *, at=None, block=1, action='訊號部位通知 (浩克3V3 )', position=None):
        at = at or self.now
        position = position or f'B={int(direction == 1)} S={int(direction == -1)} '
        self.connection.execute('INSERT INTO HISTORY VALUES (?,?,?,?,?,?)',
            (at.strftime('%Y/%m/%d'), at.strftime('%H:%M:%S'), action, position,
             123 if block == 1 else 124, block))
        self.connection.commit()

    def telegram(self, direction, *, at=None, message_id=1, sender='taiwan_mxf_bot', partial=False):
        at = at or self.now
        event = dict(event='received', route='h', sender_username=sender,
                     chat_id=5, message_id=message_id, telegram_message_at=at.isoformat(),
                     received_at=at.isoformat(),
                     text=f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})')
        with self.tg.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + ('' if partial else '\n'))

    def events(self):
        return [json.loads(row) for row in self.router.output.read_text(encoding='utf-8').splitlines()]

    def test_initial_baseline_never_trades_existing_history_or_tg(self):
        self.local(1)
        self.telegram(1)
        self.router.step(self.now)
        self.assertEqual(self.events(), [])

    def test_local_first_and_delayed_tg_21_seconds_is_confirmation(self):
        self.router.step(self.now)
        self.local(-1)
        self.router.step(self.now)
        self.telegram(-1, at=self.now+timedelta(seconds=21))
        self.router.step(self.now+timedelta(seconds=21))
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]['source_kind'], 'concord_sqlite')

    def test_simultaneous_sources_prefer_local(self):
        self.router.step(self.now)
        self.local(1)
        self.telegram(1)
        self.router.step(self.now)
        self.assertEqual([e['source_kind'] for e in self.events()], ['concord_sqlite'])

    def test_missing_tg_file_does_not_block_local(self):
        self.router.step(self.now)
        self.tg.unlink()
        self.local(-1)
        self.router.step(self.now)
        self.assertEqual(self.events()[0]['direction'], -1)
        self.assertIn('telegram', self.router.errors)

    def test_tg_fallback_when_vendor_unavailable_and_late_db_never_reverses(self):
        self.router.step(self.now)
        original = self.router.vendor
        self.router.vendor = self.root/'missing.tde'
        self.telegram(-1)
        self.router.step(self.now)
        self.assertEqual(self.events()[0]['source_kind'], 'telegram')
        self.router.vendor = original
        self.local(1, at=self.now-timedelta(seconds=5))
        self.local(-1, at=self.now-timedelta(seconds=1))
        self.router.step(self.now)
        self.assertEqual(len(self.events()), 1)
        self.assertNotIn('concord_sqlite', self.router.errors)

    def test_locked_vendor_does_not_block_tg(self):
        self.router.step(self.now)
        self.connection.execute('BEGIN EXCLUSIVE')
        self.telegram(1)
        try:
            self.router.step(self.now)
            self.assertEqual(self.events()[0]['source_kind'], 'telegram')
        finally:
            self.connection.rollback()

    def test_old_tg_cannot_reverse_a_newer_local_direction(self):
        self.router.step(self.now)
        self.local(1)
        self.router.step(self.now)
        later = self.now+timedelta(seconds=5)
        self.local(-1, at=later)
        self.router.step(later)
        self.telegram(1, at=later+timedelta(seconds=21))
        self.router.step(later+timedelta(seconds=21))
        self.assertEqual([e['direction'] for e in self.events()], [1, -1])

    def test_real_local_a_b_a_and_flat_are_retained(self):
        self.router.step(self.now)
        for i, direction in enumerate((1, -1, 1, 0)):
            at = self.now+timedelta(seconds=i)
            self.local(direction, at=at)
            self.router.step(at)
        self.assertEqual([e['direction'] for e in self.events()], [1, -1, 1, 0])

    def test_repeated_direction_does_not_start_new_round(self):
        self.router.step(self.now)
        self.local(1)
        self.router.step(self.now)
        self.local(1, at=self.now+timedelta(seconds=20))
        self.router.step(self.now+timedelta(seconds=20))
        self.assertEqual(len(self.events()), 1)

    def test_stale_future_wrong_strategy_order_report_and_ambiguous_are_ignored(self):
        self.router.step(self.now)
        self.local(1, at=self.now-timedelta(seconds=31))
        self.local(-1, at=self.now+timedelta(seconds=1))
        self.local(1, block=2)
        self.local(1, action='成交回報')
        self.local(1, position='B=1 S=1')
        self.local(1, position='B=2 S=0')
        self.router.step(self.now)
        self.assertEqual(self.events(), [])

    def test_untrusted_tg_and_incomplete_line_are_ignored(self):
        self.router.step(self.now)
        self.telegram(1, sender='imposter')
        self.telegram(-1, message_id=2, partial=True)
        self.router.step(self.now)
        self.assertEqual(self.events(), [])
        with self.tg.open('a') as handle:
            handle.write('\n')
        self.router.step(self.now)
        self.assertEqual([e['direction'] for e in self.events()], [-1])

    def test_legacy_flat_tg_notices_preserved_and_conflicts_rejected(self):
        base = dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                    chat_id=5, message_id=1, received_at=self.now.isoformat())
        for text in ('浩克3V3 交易訊號通知 (B=0 S=0)',
                     '浩克3V3 交易訊號通知 小型台指近一訊號部位為：空手',
                     '浩克3V3 訊號通知 部位為：無部位', '浩克3V3 訊號通知 部位為：0口'):
            self.assertEqual(tg_signal(dict(base, text=text))['direction'], 0)
        for text in ('浩克3V3 訊號通知 部位為：空手 (B=1 S=1)',
                     '浩克3V3 訊號通知 部位為：空手 多1口 空1口'):
            self.assertIsNone(tg_signal(dict(base, text=text)))

    def test_tg_is_backup_even_when_database_is_readable_but_has_no_matching_signal(self):
        self.local(1, at=self.now-timedelta(seconds=180))
        self.router.step(self.now)
        self.telegram(-1)
        self.router.step(self.now)
        self.assertEqual([e['source_kind'] for e in self.events()], ['telegram'])

    def test_old_tg_is_not_freshened_by_router(self):
        self.router.step(self.now)
        self.telegram(1, at=self.now-timedelta(seconds=31))
        self.router.step(self.now)
        self.assertEqual(self.events(), [])

    def test_restart_preserves_cursors_and_dedup(self):
        self.router.step(self.now)
        self.local(-1)
        self.router.step(self.now)
        self.router.db.close()
        self.router = self.open_router()
        self.telegram(-1, at=self.now+timedelta(seconds=21))
        self.router.step(self.now+timedelta(seconds=21))
        self.assertEqual(len(self.events()), 1)

    def test_outbox_recovers_commit_before_append_and_partial_append(self):
        self.router.step(self.now)
        self.local(1)
        self.router.poll_local(self.now)  # Commit without appending the journal.
        self.router.db.close()
        with (self.root/'records/h3_signal_events.jsonl').open('ab') as handle:
            handle.write(b'{"partial":')
        self.router = self.open_router()
        self.assertEqual([e['signal_key'] for e in self.events()], ['h3-dual:1'])

    def test_truncation_rebaselines_local_and_keeps_tg_available(self):
        self.local(1)
        self.router.step(self.now)
        self.connection.execute('DELETE FROM HISTORY')
        self.connection.commit()
        self.telegram(-1)
        self.router.step(self.now)
        self.assertNotIn('concord_sqlite', self.router.errors)
        self.assertEqual(self.router.get('local_cursor'), 0)
        self.assertEqual([e['source_kind'] for e in self.events()], ['telegram'])
        self.local(1, at=self.now+timedelta(seconds=1))
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual([e['direction'] for e in self.events()], [-1, 1])

    def test_reboot_device_change_preserves_legacy_cursors(self):
        self.local(1, at=self.now-timedelta(seconds=60))
        self.telegram(1, at=self.now-timedelta(seconds=60))
        self.router.step(self.now)
        identity = self.router.get('local_identity')
        tg = self.router.get('tg_cursor')
        identity[0] += 1
        tg['identity'][0] += 1
        tg.pop('anchor')  # Migrate the pre-fix persisted state without resetting it.
        with self.router.db:
            self.router.put('local_identity', identity)
            self.router.put('tg_cursor', tg)
            self.router.db.execute("DELETE FROM meta WHERE key='local_anchor'")
        self.router.db.close()
        self.router = self.open_router()
        self.local(-1)
        self.telegram(-1)
        self.router.step(self.now)
        self.assertEqual(self.router.errors, {})
        self.assertEqual([e['direction'] for e in self.events()], [-1])
        self.assertEqual(self.router.get('local_cursor'), 2)
        self.assertEqual(self.router.get('tg_cursor')['offset'], self.tg.stat().st_size)
        self.assertIsNotNone(self.router.get('local_anchor'))
        self.assertIn('anchor', self.router.get('tg_cursor'))

    def test_database_replacement_skips_existing_rows_and_keeps_outbox(self):
        self.router.step(self.now)
        self.local(1)
        self.router.step(self.now)
        replacement = self.root/'replacement.tde'
        with sqlite3.connect(replacement) as other:
            self.connection.backup(other)
            other.execute("UPDATE HISTORY SET POSITION='B=0 S=1'")
            other.execute('INSERT INTO HISTORY SELECT * FROM HISTORY')
        self.connection.close()
        replacement.replace(self.vendor)
        self.connection = sqlite3.connect(self.vendor)
        self.addCleanup(self.connection.close)
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual(self.router.errors, {})
        self.assertEqual([e['direction'] for e in self.events()], [1])
        self.assertEqual(self.router.get('local_cursor'), 2)
        self.local(-1, at=self.now+timedelta(seconds=2))
        self.router.step(self.now+timedelta(seconds=2))
        self.assertEqual([e['signal_key'] for e in self.events()], ['h3-dual:1', 'h3-dual:2'])

    def test_legacy_tg_cursor_gets_anchor_even_without_new_lines(self):
        self.router.step(self.now)
        saved = self.router.get('tg_cursor')
        saved.pop('anchor')
        saved['identity'][0] += 1
        with self.router.db:
            self.router.put('tg_cursor', saved)
        self.router.step(self.now)
        self.assertIn('anchor', self.router.get('tg_cursor'))
        self.assertEqual(self.router.get('tg_cursor')['offset'], saved['offset'])
        self.assertEqual(self.events(), [])

    def test_database_rewrite_with_same_inode_and_row_count_rebaselines(self):
        self.local(1)
        self.router.step(self.now)
        self.connection.execute("UPDATE HISTORY SET POSITION='B=0 S=1'")
        self.connection.commit()
        self.router.step(self.now)
        self.assertEqual(self.router.errors, {})
        self.assertEqual(self.events(), [])
        self.assertEqual(self.router.db.execute('SELECT direction FROM local_seen').fetchone()[0], -1)
        self.local(1, at=self.now+timedelta(seconds=1))
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual([e['direction'] for e in self.events()], [1])

    def test_tg_replacement_skips_existing_content_then_reads_new_signal(self):
        self.router.step(self.now)
        replacement = self.root/'replacement.jsonl'
        replacement.write_text('', encoding='utf-8')
        replacement.replace(self.tg)
        self.telegram(1)
        self.router.step(self.now)
        self.assertEqual(self.router.errors, {})
        self.assertEqual(self.events(), [])
        self.telegram(-1, at=self.now+timedelta(seconds=1), message_id=2)
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual([e['direction'] for e in self.events()], [-1])

    def test_tg_truncate_and_regrow_beyond_saved_offset_skips_old_content(self):
        self.telegram(1)
        self.router.step(self.now)
        self.tg.write_bytes(b'')
        self.telegram(-1, message_id=22)
        self.assertGreater(self.tg.stat().st_size, self.router.get('tg_cursor')['offset'])
        self.router.step(self.now)
        self.assertEqual(self.router.errors, {})
        self.assertEqual(self.events(), [])
        self.telegram(1, at=self.now+timedelta(seconds=1), message_id=23)
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual([e['direction'] for e in self.events()], [1])

    def test_tg_truncation_recovers_without_replaying_partial_baseline(self):
        self.telegram(1)
        self.router.step(self.now)
        self.tg.write_bytes(b'')
        self.telegram(-1, partial=True)
        self.router.step(self.now)
        with self.tg.open('a') as handle:
            handle.write('\n')
        self.router.step(self.now)
        self.assertEqual(self.events(), [])
        self.telegram(1, at=self.now+timedelta(seconds=1), message_id=2)
        self.router.step(self.now+timedelta(seconds=1))
        self.assertEqual([e['direction'] for e in self.events()], [1])


    def test_two_accounts_execute_local_once_and_ignore_delayed_tg(self):
        backend = Path(__file__).resolve().parent
        for path in (backend/'h3-capital-flatten-reentry-strategy', backend/'h3-follow-add-strategy',
                     backend/'h3-profit-protection-strategy'):
            sys.path.insert(0, str(path))
        from h_core import Store
        from runtime import Engine, signal
        from protection_runtime import ProtectionEngine

        class Broker:
            unit = 1
            first_fill = '22000'
            preflight = None
            def __init__(self):
                self.current = 0
                self.guard = {}
                self.orders = []
            def check_rollover(self): pass
            def position(self): return self.current
            def valid_transition(self, *args): return True
            def send_and_confirm(self, delta, phase, target):
                if self.preflight: self.preflight()
                self.orders.append(delta)
                self.current = target
                return target

        engines = []
        for i, engine_type in enumerate((Engine, ProtectionEngine)):
            store = Store(self.root/f'account-{i}.sqlite')
            self.addCleanup(store.db.close)
            engines.append(engine_type(store, Broker(), 'TMFR6'))
        self.router.step(self.now)
        self.local(-1)
        self.router.step(self.now)
        first = self.events()[0]
        for engine in engines:
            self.assertTrue(engine.on_signal(first))
            self.assertFalse(engine.on_signal(first))
            self.assertEqual(engine.broker.orders, [-1])
        self.telegram(-1, at=self.now+timedelta(seconds=21))
        self.router.step(self.now+timedelta(seconds=21))
        self.assertEqual(len(self.events()), 1)
        # Zero position remains an actionable, validated canonical event.
        flat = dict(first, direction=0, text='浩克3V3 訊號通知 (B=0 S=0)', signal_key='h3-dual:2')
        self.assertEqual(signal(flat)[1], 0)
        for engine in engines:
            self.assertTrue(engine.on_signal(flat))
            self.assertEqual(engine.broker.orders, [-1, 1])
        malformed = dict(first, direction=1)
        self.assertIsNone(signal(malformed))


if __name__ == '__main__':
    unittest.main()
