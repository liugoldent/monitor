"""Track Shioaji session events for strategy process restart decisions."""

from __future__ import annotations

import time
from threading import Lock


class BrokerReconnectWatchdog:
    """Allow a short SDK reconnect, then ask the main loop to exit."""

    def __init__(self, timeout_seconds: float, *, clock=time.monotonic):
        if timeout_seconds <= 0:
            raise ValueError("券商重連逾時秒數必須大於 0")
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.lock = Lock()
        self.down_since: float | None = None
        self.recovered = False

    def on_event(self, _response_code: int, event_code: int, _info: str, _event: str) -> None:
        with self.lock:
            if event_code in {1, 12} and self.down_since is None:
                self.down_since = self.clock()
                self.recovered = False
                print("永豐連線中斷，等待 SDK 自動重連", flush=True)
            elif event_code == 13 and self.down_since is not None:
                self.down_since = None
                self.recovered = True
                print("永豐連線已恢復", flush=True)

    def status(self) -> tuple[bool, bool]:
        """Return (disconnected, restart deadline passed)."""
        with self.lock:
            if self.down_since is None:
                return False, False
            return True, self.clock() - self.down_since >= self.timeout_seconds

    def consume_recovery(self) -> bool:
        with self.lock:
            recovered = self.recovered
            self.recovered = False
            return recovered
