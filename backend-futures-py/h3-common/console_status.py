"""Console-only progress reporting; never queries or changes broker state."""
from datetime import datetime
import time
from zoneinfo import ZoneInfo


class ConsoleStatus:
    def __init__(self, label, simulation=False, interval=60):
        self.label = label
        self.mode = '模擬' if simulation else '正式'
        self.interval = interval
        self.last_print = None
        self.last_signal = '啟動後尚無新訊號'

    def write(self, message):
        stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M:%S')
        print(f'[{stamp} 台北][{self.label}｜{self.mode}] {message}', flush=True)

    def notifications(self, notify):
        def emit(message):
            self.write(message)
            return notify(message)
        return emit

    def received(self, event):
        if not isinstance(event, dict):
            return
        direction = event.get('direction')
        name = ({1: '多', -1: '空', 0: '空手'}.get(direction, '待策略判讀')
                if type(direction) is int else '待策略判讀')
        self.last_signal = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%H:%M:%S')
        self.write(f'讀到新訊號：{name}；交由策略檢查有效期、去重及執行條件')

    def heartbeat(self, engine):
        now = time.monotonic()
        if self.last_print is not None and now - self.last_print < self.interval:
            return
        expected = engine.state().expected
        pending = bool(engine.store.pending() or engine.broker.guard.get('pending'))
        status = '有未確認委託，等待核對' if pending else '監控迴圈執行中，等待新H3訊號'
        self.write(f'{status}；帳本部位 {expected:+d} 口；最近讀取：{self.last_signal}。'
                   '此為程式心跳，非即時券商查倉。')
        self.last_print = now
