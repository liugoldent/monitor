"""Read the two live journals without consuming an event before execution."""
from datetime import datetime, timedelta, timezone
import json
import os

from h_core import TradingError


class SignalJournal:
    def __init__(self, path, route, *, optional=False):
        self.path, self.route = path, route
        self.identity = None
        self.offset = 0
        try:
            stat = path.stat()
        except FileNotFoundError:
            if not optional:
                raise
        else:
            self.identity, self.offset = stat.st_ino, stat.st_size

    def peek(self):
        try:
            handle = self.path.open('rb')
        except FileNotFoundError:
            if self.identity is None:
                return None
            raise TradingError(f'{self.path.name} 消失，停止核對') from None
        with handle:
            stat = os.fstat(handle.fileno())
            if self.identity is None:
                self.identity = stat.st_ino
            if stat.st_ino != self.identity or stat.st_size < self.offset:
                raise TradingError(f'{self.path.name} 被替換或截斷，停止核對')
            handle.seek(self.offset)
            while True:
                raw = handle.readline()
                if not raw.endswith(b'\n'):
                    return None
                next_offset = handle.tell()
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    event = None
                if (isinstance(event, dict) and event.get('route') == self.route
                        and event.get('event') == 'received'):
                    return event, next_offset
                self.offset = next_offset

    @staticmethod
    def arrival(event):
        try:
            dt = datetime.fromisoformat(event.get('observed_at') or event['received_at'])
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=8)))).timestamp()
        except (KeyError, TypeError, ValueError):
            return float('inf')
