"""Keep one reconnect attempt within the current H3 signal's execution."""
from dataclasses import dataclass
import sqlite3

from h_core import TradingError
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
import shioaji_tmf_target as shared


def handle_signal(engine, event):
    broker = engine.broker
    key = event.get('signal_key') if isinstance(event, dict) else None
    broker.signal_retry = SignalRetry(broker, key)
    try:
        return engine.on_signal(event)
    finally:
        broker.signal_retry = None


def signal_step(broker, stage, operation, *, recover=None):
    retry = getattr(broker, 'signal_retry', None)
    if isinstance(retry, SignalRetry):
        return retry.run(stage, operation, recover)
    return operation()


def signal_retry_used(broker):
    retry = getattr(broker, 'signal_retry', None)
    return isinstance(retry, SignalRetry) and retry.used


@dataclass
class SignalRetry:
    broker: object
    key: str | None = None
    used: bool = False

    def run(self, stage, operation, recover):
        from runtime import LedgerError, RolloverPause

        try:
            return operation()
        except (LedgerError, RolloverPause, sqlite3.Error):
            raise
        except Exception as exc:
            if self.used:
                raise
            self.used = True
            self.broker.store.audit('h3_signal_reconnect',
                                    dict(trigger=self.key, stage=stage, error=type(exc).__name__))
            print(f'【{self.broker.account_label}】H3 {stage} 發生 {type(exc).__name__}，重新連線核對一次。',
                  flush=True)

        try:
            self.broker.reconnect_for_signal()
            return recover() if recover is not None else operation()
        except RolloverPause as exc:
            # The watch loop must not replay this signal with a fresh retry budget.
            raise TradingError(str(exc)) from exc


def reconnect_broker(broker, close, initialize):
    def identity(api):
        return tuple(shared._position_value(api.futopt_account, field)
                     for field in ('broker_id', 'account_id'))

    expected = identity(broker.api)
    close()
    api = initialize()
    if not all(expected) or identity(api) != expected:
        raise TradingError('永豐重連後期貨帳號不符，本筆停止')
    watchdog = BrokerReconnectWatchdog(broker.watchdog.timeout_seconds)
    api.set_event_callback(watchdog.on_event)
    broker.api, broker.watchdog = api, watchdog
