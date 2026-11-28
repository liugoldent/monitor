"""API_KEY3 conditional H2/H3 targets on one verified Shioaji net position."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

from api_key3_history import Curve, stamp
from h_signal import parse_h2_dual_signal, parse_h2_signal
from h3_signal_retry import signal_step
from net_positions import commit_api_key3_signal, save_positions
from portfolio_runtime import PortfolioEngine
from runtime import LedgerError, RolloverPause, signal


def h2_signal(event, now=None):
    if isinstance(event, dict) and event.get('source') == 'h2_dual_source':
        return parse_h2_dual_signal(event, now=now, allow_flat=True)
    return parse_h2_signal(event, now=now, allow_flat=True)


def active_h2(h2, h3, at=None):
    cross = h3.drawdown_750_at
    return h2.longest_streak >= 4 or (cross is not None and (at is None or cross < at))


def active_h3(h3):
    return h3.large_loss or h3.longest_streak >= 2


class ConditionalPortfolio(PortfolioEngine):
    account_label = 'API_KEY3'
    strategy_label = 'H2／H3 回前高條件跟單'
    reconcile_signal_inventory = True

    def __init__(self, *args, seeds, market, started_at, **kwargs):
        super().__init__(*args, **kwargs)
        self.market, self.started_at = market, started_at
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS api_key3_curves '
                                  '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if not self.store.db.execute('SELECT 1 FROM api_key3_curves WHERE id=1').fetchone():
                if self.strategy_positions().net or self.store.pending() or not seeds:
                    raise LedgerError('API_KEY3 缺少可回溯的單口歷史帳本')
                self.store.db.execute('INSERT INTO api_key3_curves VALUES (1,?)',
                                      (json.dumps({s: asdict(c) for s, c in seeds.items()}),))
        self.curves()
        self.strategy_positions()

    def strategy_positions(self):
        positions = super().strategy_positions()
        if any(abs(q) > 1 for q in (positions.h2, positions.h3)):
            raise LedgerError('API_KEY3 策略口數超過單邊各 1 口')
        return positions

    def curves(self):
        try:
            row = self.store.db.execute('SELECT payload FROM api_key3_curves WHERE id=1').fetchone()
            data = json.loads(row[0])
            curves = {name: Curve(**data[name]) for name in ('h2', 'h3')}
            if any(c.drawdown < 0 or c.direction not in (-1, 0, 1)
                   or bool(c.entry is not None) != bool(c.direction) or not c.closed for c in curves.values()):
                raise ValueError()
            return curves
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LedgerError('API_KEY3 虛擬單口帳本無效') from exc

    def on_signal(self, event, *, now=None):
        if not isinstance(event, dict):
            return False
        clock = now or datetime.now(timezone.utc)
        source = 'h2' if event.get('route') == 'h2' else 'h3'
        parser = h2_signal if source == 'h2' else signal
        try:
            if source == 'h2' and event.get('source') == 'h2_dual_source' and event.get('source_kind') == 'telegram':
                reported = stamp(event['relay_received_at'])
            else:
                reported = stamp(event['received_at'])
        except (KeyError, TypeError, ValueError):
            return False
        if reported > clock.timestamp():
            return False
        parsed = parser(event, now=datetime.fromtimestamp(reported, timezone.utc))
        if parsed is None:
            return False
        key, direction, at = parsed
        prior = self.curves()
        current = prior[source]
        if (at <= current.at or self.store.db.execute('SELECT 1 FROM signals WHERE id=?', (key,)).fetchone()):
            return False

        fresh_signal = at >= self.started_at and parser(event, now=clock) is not None
        changed_direction = direction != current.direction
        updated = replace(current, at=at)
        if changed_direction:
            updated = current.signal(direction, self.market.at(at), at)
        if source == 'h3' and current.drawdown_750_at is None and updated.drawdown_750_at is not None:
            if event.get('observed_at'):
                observed = stamp(event['observed_at'])
                updated = replace(updated, drawdown_750_at=max(at, observed))
        curves = dict(prior, **{source: updated})
        h2, h3 = curves['h2'], curves['h3']
        before = self.strategy_positions()
        after = replace(before, h2=before.h2 if active_h2(h2, h3) else 0,
                        h3=before.h3 if active_h3(h3) else 0)
        if changed_direction:
            eligible = active_h2(h2, h3, at) if source == 'h2' else active_h3(h3)
            after = replace(after, **{source: direction if fresh_signal and eligible else 0})
        plan = {
            'before': {name: asdict(curve) for name, curve in prior.items()},
            'after': {name: asdict(curve) for name, curve in curves.items()},
            'direction': direction, 'at': at,
        }

        if not fresh_signal:
            # Catch-up can retire an obsolete target without touching broker holdings.
            # The next live signal reconciles the resulting net target.
            with self.store.db:
                if (self.store.pending() or self.broker.guard.get('pending')
                        or self.strategy_positions() != before or self.state().expected != before.net):
                    raise LedgerError('API_KEY3 回溯期間策略帳本或委託已變動')
                commit_api_key3_signal(self.store, {'trigger': key, 'api_key3_signal': plan})
                if after != before:
                    save_positions(self.store, after)
                    self.save(self.state_type(expected=after.net, round_key=key))
            return True

        def preflight():
            if parser(event, now=now) is None:
                raise RolloverPause('交易訊號已過 30 秒，重新以歷史訊號處理')

        preflight()
        self.broker.check_rollover()
        signal_step(self.broker, 'cancel_working_orders',
                    lambda: self.broker.cancel_working_orders(key, preflight))
        actual = signal_step(self.broker, 'net_inventory', lambda: self.validate_position(require_match=False))
        preflight()
        note = f'H2 回撤 {h2.drawdown:f}點／連輸 {h2.losses}筆｜H3 回撤 {h3.drawdown:f}點／連輸 {h3.losses}筆'
        try:
            self.adjust_net(source, before, after, key, preflight, note,
                            actual_before=actual, api_key3_signal=plan)
        except RolloverPause:
            raise
        except Exception:
            # The shared retry path may have already proved zero fills or that
            # nothing was submitted. Retain the virtual observation once.
            if (after.net != actual and not self.store.pending() and not self.broker.guard.get('pending')
                    and self.strategy_positions() == before):
                with self.store.db:
                    commit_api_key3_signal(self.store, {'trigger': key, 'api_key3_signal': plan})
            raise
        return True
