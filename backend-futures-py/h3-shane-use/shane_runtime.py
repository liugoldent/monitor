"""Each distinct H3 direction signal closes the old position, then opens SHANE_UNIT."""
from dataclasses import dataclass, replace

import shane_bootstrap
from runtime import Engine, LedgerError, RolloverPause, signal
from strategy import State


@dataclass(frozen=True)
class ShaneState(State):
    def validate(self):
        if type(self.expected) is not int or self.added is not False:
            raise ValueError('Shane 帳本口數或加碼旗標無效')
        # Reuse price and source validation without the base policy's two-lot cap.
        direction = (1 if self.expected > 0 else -1) if self.expected else 0
        State.validate(replace(self, expected=direction))
        return self


class ShaneEngine(Engine):
    state_type = ShaneState

    def validate_position(self):
        self.broker.check_rollover()
        if self.store.pending():
            raise LedgerError('Shane 有未確認委託，保留 guard，不重送')
        if self.broker.position() != self.state().expected:
            raise LedgerError('Shane 券商庫存與專用帳本不符，不接管或自動平倉')

    def on_signal(self, event, *, now=None):
        parsed = signal(event, now)
        if parsed is None:
            return False
        self.validate_position()
        key, direction, received = parsed
        # The established Store.consume suppresses same-direction notices for
        # ten seconds. This policy intentionally acts on every distinct H3 ID.
        with self.store.db:
            if self.store.db.execute('SELECT 1 FROM signals WHERE id=?', (key,)).fetchone():
                return False
            self.store.db.execute('INSERT INTO signals VALUES (?,?,?,?)',
                                  (key, direction, received, 'consumed'))

        def fresh():
            if signal(event) is None:
                raise RolloverPause('H3 訊號已超過30秒，本筆尚未送出')

        try:
            if self.state().expected:
                self.order(0, 'flatten', key, ShaneState(round_key=key), fresh)
            if direction:
                self.order(direction * self.broker.unit, 'entry', key,
                           ShaneState(round_key=key), fresh)
        except RolloverPause:
            if not self.store.pending() and not self.broker.guard.get('pending'):
                with self.store.db:
                    self.store.db.execute('DELETE FROM signals WHERE id=?', (key,))
            raise
        return True
