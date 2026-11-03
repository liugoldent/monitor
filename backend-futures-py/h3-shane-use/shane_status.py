"""Rate-limited console status without exposing broker credentials."""
import time

from h_core import TradingError


def failure_status(exc, *, simulation, pending):
    mode = '模擬' if simulation else '正式'
    if pending or isinstance(exc, TradingError):
        reason = str(exc) if isinstance(exc, TradingError) else type(exc).__name__
        return 'shane_waiting', f'Shane H3（{mode}）等待核對：{reason}'
    # SDK exception text can contain API keys; only expose known safe explanations.
    reason = type(exc).__name__
    if 'not match signature' in str(exc):
        reason += '（登入簽章驗證失敗，請確認 API_KEY／SECRET_KEY 配對）'
    return 'shane_retrying', f'Shane H3（{mode}）執行中斷，等待重試：{reason}'


class StatusReporter:
    def __init__(self, interval=60):
        self.interval = interval
        self.previous = None
        self.last_print = 0

    def report(self, message):
        now = time.monotonic()
        if message != self.previous or now - self.last_print >= self.interval:
            print(message, flush=True)
            self.previous = message
            self.last_print = now
