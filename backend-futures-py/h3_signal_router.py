"""Read Concord HISTORY first, then TG; publish one durable H3 transition feed.

No broker or Telegram connection is needed here. The vendor database is read-only.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

from h_signal import parse_h_event_direction, parse_h_flat_direction

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Taipei')
MAX_AGE = 30
CORRELATION_SECONDS = 120
NAME = '浩克3V3'
POSITION = re.compile(r'B=(0|1)\s+S=(0|1)\s*')
ACTION = re.compile(r'訊號部位通知\s*[（(]\s*浩克3V3\s*[)）]\s*')


def now_local():
    return datetime.now(TZ)


def same_file(previous, current):
    # Docker Desktop may renumber the mount device after a VM/host reboot.
    # An inode change still means replacement; content anchors detect rewrites.
    return previous is not None and previous[1] == current[1]


def local_anchor(connection, cursor):
    row = connection.execute('SELECT rowid,* FROM HISTORY WHERE rowid=?', (cursor,)).fetchone()
    return hashlib.sha256(repr(row).encode('utf-8')).hexdigest()


def telegram_anchor(handle, offset):
    position = handle.tell()
    try:
        handle.seek(max(0, offset-256))
        return hashlib.sha256(handle.read(min(offset, 256))).hexdigest()
    finally:
        handle.seek(position)


def stamp(value):
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result


def local_signal(row):
    """Only the named strategy's original position notices are actionable."""
    rowid, date, clock, action, position, signal_id, group_name = row
    match = POSITION.fullmatch(position.strip()) if isinstance(position, str) else None
    if group_name != NAME or not ACTION.fullmatch(action) or not match:
        return None
    buy, sell = map(int, match.groups())
    if buy and sell:
        return None
    try:
        at = datetime.strptime(date + ' ' + clock, '%Y/%m/%d %H:%M:%S').replace(tzinfo=TZ)
    except (ValueError, TypeError):
        return None
    return dict(direction=buy-sell, at=at.isoformat(), source_kind='concord_sqlite',
                reference=dict(rowid=rowid, signal_id=signal_id))


def tg_signal(event):
    direction = parse_h_event_direction(event)
    if direction is None:
        # Flat notices still require exactly the original trusted TG envelope.
        validated = parse_h_event_direction(dict(event, text='浩克3 訊號通知 多1口'))
        if validated is None or parse_h_flat_direction(event.get('text')) != 0:
            return None
        direction = 0
    if event.get('source') == 'h3_dual_source':
        return None
    if not all(type(event.get(k)) is int for k in ('chat_id', 'message_id')):
        return None
    try:
        at = stamp(event.get('telegram_message_at') or event['received_at'])
    except (KeyError, TypeError, ValueError):
        return None
    return dict(direction=direction, at=at.isoformat(), source_kind='telegram',
                reference=dict(chat_id=event['chat_id'], message_id=event['message_id']))


class Router:
    def __init__(self, vendor, telegram, directory):
        self.vendor, self.telegram, self.directory = Path(vendor), Path(telegram), Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.output = self.directory / 'h3_signal_events.jsonl'
        self.audit_path = self.directory / 'h3_signal_sources.jsonl'
        self.db = sqlite3.connect(self.directory / 'h3_signal_router.sqlite')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS local_seen
              (rowid INTEGER PRIMARY KEY, direction INTEGER, at REAL);
            CREATE TABLE IF NOT EXISTS outbox
              (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        self.db.commit()
        self.errors = {}
        self.published_seq = None
        self.flush()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def audit(self, decision, candidate=None, **detail):
        record = dict(observed_at=now_local().isoformat(), decision=decision, **detail)
        if candidate:
            record.update(candidate)
        with self.audit_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')

    def flush(self):
        """Recover a committed outbox after a crash without changing signal IDs."""
        last = self.published_seq or 0
        if self.published_seq is None and self.output.exists():
            with self.output.open('rb+') as handle:
                end = 0
                while raw := handle.readline():
                    if not raw.endswith(b'\n'):
                        handle.truncate(end)
                        break
                    event = json.loads(raw)
                    last += 1
                    expected = self.db.execute('SELECT payload FROM outbox WHERE seq=?', (last,)).fetchone()
                    if expected is None or json.loads(expected[0]) != event:
                        raise RuntimeError('H3 journal does not match durable outbox')
                    end = handle.tell()
        elif not self.output.exists() and self.get('journal_created', False):
            raise RuntimeError('H3 journal missing; preserve router state for reconciliation')
        pending = self.db.execute('SELECT seq,payload FROM outbox WHERE seq>? ORDER BY seq', (last,)).fetchall()
        if self.published_seq is not None and not pending:
            return
        with self.output.open('a', encoding='utf-8') as handle:
            for seq, payload in pending:
                handle.write(payload + '\n')
                last = seq
            handle.flush()
            os.fsync(handle.fileno())
        if not self.get('journal_created', False):
            with self.db:
                self.put('journal_created', True)
        self.published_seq = last

    def accept(self, candidate, now):
        at = stamp(candidate['at']).timestamp()
        age = now.timestamp() - at
        direction = candidate['direction']
        if not 0 <= age <= MAX_AGE:
            self.audit('stale_or_future', candidate)
            return
        last = self.get('last_signal')
        if candidate['source_kind'] == 'telegram':
            # A delayed old TG must not reverse a newer local round (A -> B -> A).
            confirmed = self.db.execute(
                'SELECT rowid FROM local_seen WHERE direction=? AND at BETWEEN ? AND ? LIMIT 1',
                (direction, at-CORRELATION_SECONDS, at+1)).fetchone()
            if confirmed:
                self.audit('telegram_confirms_local', candidate, local_rowid=confirmed[0])
                return
        if last and (at <= last['at'] or direction == last['direction']):
            self.audit('duplicate_or_older', candidate)
            return
        seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM outbox').fetchone()[0]
        event = dict(event='received', route='h', source='h3_dual_source',
                     signal_key=f'h3-dual:{seq}', source_kind=candidate['source_kind'],
                     source_reference=candidate['reference'], received_at=candidate['at'],
                     observed_at=now.isoformat(), direction=direction,
                     text=f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})')
        self.db.execute('INSERT INTO outbox VALUES (?,?)', (seq, json.dumps(event, ensure_ascii=False)))
        self.put('last_signal', dict(at=at, direction=direction))
        self.audit('published', candidate, signal_key=event['signal_key'])

    def poll_local(self, now):
        # Reopen every poll: see fresh commits and never use immutable=1 on a live DB.
        with closing(sqlite3.connect(self.vendor.resolve().as_uri()+'?mode=ro', uri=True, timeout=0.1)) as vendor:
            vendor.execute('PRAGMA query_only=ON')
            vendor.execute('BEGIN')  # Maximum, anchor and rows share one snapshot.
            maximum = vendor.execute('SELECT COALESCE(MAX(rowid),0) FROM HISTORY').fetchone()[0]
            cursor = self.get('local_cursor')
            stat = self.vendor.stat()
            identity = [stat.st_dev, stat.st_ino]
            saved_anchor = self.get('local_anchor')
            reset = cursor is not None and (
                maximum < cursor or not same_file(self.get('local_identity'), identity)
                or (saved_anchor is not None and local_anchor(vendor, cursor) != saved_anchor))
            # First successful connection is a live baseline, never replay old positions.
            baseline = cursor is None or reset
            rows = vendor.execute('''SELECT h.rowid,h.DATE,h.TIME,h.ACTION,h.POSITION,
                    h.SIGNAL_ID,s.GROUP_NAME FROM HISTORY h JOIN HISTORY_SETTING s
                    ON h.BLOCK=s.BLOCK AND h.SIGNAL_ID=s.SIGNAL_ID
                    WHERE h.rowid>? AND h.rowid<=? AND s.GROUP_NAME=?
                    ORDER BY h.rowid''', (0 if baseline else cursor, maximum, NAME)).fetchall()
            anchor = local_anchor(vendor, maximum)
        with self.db:
            if reset:
                self.db.execute('DELETE FROM local_seen')
            for row in rows:
                candidate = local_signal(row)
                if candidate is None:
                    continue
                self.db.execute('INSERT OR IGNORE INTO local_seen VALUES (?,?,?)',
                    (row[0], candidate['direction'], stamp(candidate['at']).timestamp()))
                if not baseline:
                    self.accept(candidate, now)
            self.put('local_cursor', maximum)
            self.put('local_identity', identity)
            self.put('local_anchor', anchor)
        if baseline:
            self.audit('local_rebaseline' if reset else 'local_baseline',
                       previous_cursor=cursor, cursor=maximum, identity=identity)

    def poll_telegram(self, now):
        with self.telegram.open('rb') as handle:
            stat = os.fstat(handle.fileno())
            identity = [stat.st_dev, stat.st_ino]
            saved = self.get('tg_cursor')
            reset = saved is not None and (
                not same_file(saved['identity'], identity) or stat.st_size < saved['offset']
                or ('anchor' in saved and telegram_anchor(handle, saved['offset']) != saved['anchor']))
            if saved is None or reset:
                with self.db:
                    self.put('tg_cursor', dict(identity=identity, offset=stat.st_size,
                                              anchor=telegram_anchor(handle, stat.st_size),
                                              discard_partial=stat.st_size > 0 and self._partial_tail(handle, stat.st_size)))
                self.audit('telegram_rebaseline' if reset else 'telegram_baseline',
                           previous_offset=saved['offset'] if saved else None, offset=stat.st_size,
                           identity=identity)
                return
            if 'anchor' not in saved or saved['identity'] != identity:
                saved = dict(saved, identity=identity, anchor=telegram_anchor(handle, saved['offset']))
                with self.db:
                    self.put('tg_cursor', saved)
            handle.seek(saved['offset'])
            # Bound a burst so a large Telegram backlog cannot starve local polling.
            for _ in range(500):
                raw = handle.readline()
                if not raw or not raw.endswith(b'\n'):
                    break
                try:
                    event = json.loads(raw)
                    candidate = tg_signal(event) if isinstance(event, dict) else None
                except (ValueError, TypeError, UnicodeError):
                    candidate = None
                with self.db:
                    if candidate and not saved.get('discard_partial', False):
                        self.accept(candidate, now)
                    saved = dict(identity=identity, offset=handle.tell(),
                                 anchor=telegram_anchor(handle, handle.tell()))
                    self.put('tg_cursor', saved)

    @staticmethod
    def _partial_tail(handle, size):
        handle.seek(size-1)
        return handle.read(1) != b'\n'

    def step(self, now=None):
        now = now or now_local()
        # Independent exception paths: a failed TG file/relay never blocks local signals.
        for name, poll in (('concord_sqlite', self.poll_local), ('telegram', self.poll_telegram)):
            try:
                poll(now)
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                error = type(exc).__name__ + ': ' + str(exc)
                if self.errors.get(name) != error:
                    self.audit('source_error', source_kind=name, error=error)
                    print(f'H3 {name}: {error}', flush=True)
                self.errors[name] = error
            else:
                if self.errors.pop(name, None):
                    self.audit('source_recovered', source_kind=name)
            self.flush()
        self.status(now)

    def status(self, now):
        path = self.directory / 'h3_signal_health.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(checked_at=now.isoformat(), errors=self.errors,
            local_cursor=self.get('local_cursor'), last_signal=self.get('last_signal'))), encoding='utf-8')
        temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Baseline/read only, never submit orders')
    args = parser.parse_args(argv)
    directory = Path(os.getenv('H3_SIGNAL_DIRECTORY', str(BASE/'telegram-relay-records')))
    vendor = os.getenv('CONCORD_H3_DB_PATH', r'C:\Concords\Concords-iQT\Dat\H124353542.tde')
    telegram = Path(os.getenv('H3_TELEGRAM_SOURCE', str(BASE/'telegram-relay-records/telegram_signal_events.jsonl')))
    from filelock import FileLock
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory/'h3_signal_router.lock'), timeout=0):
        router = Router(vendor, telegram, directory)
        try:
            while True:
                router.step()
                if args.once:
                    return
                time.sleep(max(0.1, float(os.getenv('H3_SIGNAL_POLL_SECONDS', '0.25'))))
        finally:
            router.db.close()


if __name__ == '__main__':
    main()
