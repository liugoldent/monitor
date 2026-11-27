"""Offline checks for live-signal handling of a manual cross-month hedge."""
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_signal_flatten as existing
from broker import PyramidBroker
from h_core import Store, TradingError
from mdd_runtime import MddEngine
from mdd_sizing import H3History, MddSizing
from protection_account import ProtectionBroker
from protection_runtime import ProtectionEngine
import shioaji_tmf_target as shared


NEAR = 'TMFL6'
FAR = 'TMFA7'


class Contracts(list):
    TMFR1 = {'code': NEAR}


class CrossMonthSignalTests(unittest.TestCase):
    def exercise(self, engine_type, broker_type, *, direction=1, available=None, positions=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store = Store(Path(directory) / 'ledger.sqlite')
            cleanup.callback(store.db.close)
            inventory = dict(positions if positions is not None else {NEAR: -1, FAR: 1})
            orders = []
            trades = []
            api = Mock()
            api.Contracts = SimpleNamespace(Futures=SimpleNamespace(
                TMF=Contracts([{'code': NEAR}, {'code': FAR}])))
            api.Order.side_effect = dict
            api.list_trades.side_effect = lambda: trades
            api.list_positions.side_effect = lambda account: [
                dict(code=code, quantity=abs(quantity), direction='Buy' if quantity > 0 else 'Sell')
                for code, quantity in inventory.items() if quantity]
            constants = SimpleNamespace(
                Action=SimpleNamespace(Buy='Buy', Sell='Sell'),
                FuturesPriceType=SimpleNamespace(MKT='MKT'),
                OrderType=SimpleNamespace(IOC='IOC'),
                FuturesOCType=SimpleNamespace(Auto='Auto', Cover='Cover'),
            )
            sj = SimpleNamespace(constant=constants)
            watchdog = Mock()
            watchdog.status.return_value = (False, None)

            def place(contract, order, **kwargs):
                code = contract['code']
                action, quantity, octype = (order[k] for k in ('action', 'quantity', 'octype'))
                orders.append((code, action, quantity, octype))
                if octype == 'Auto' and quantity > 1:
                    self.assertFalse(any(inventory.values()))
                filled = min(quantity, (available or {}).get(code, quantity))
                inventory[code] = inventory.get(code, 0) + (filled if action == 'Buy' else -filled)
                trade = dict(contract=contract,
                             order=dict(id=str(len(orders)), action=action, quantity=quantity),
                             status=dict(status='Filled' if filled == quantity else 'Cancelled',
                                         deal_quantity=filled,
                                         deals=[dict(price=39000, quantity=filled, ts=1795491480)] if filled else []))
                trades.append(trade)
                return trade

            api.place_order.side_effect = place
            broker = broker_type(api, sj, store, watchdog, records=directory, close_all_months=True)
            broker.unit = 1
            broker.contract_code = NEAR
            engine = engine_type(store, broker, NEAR)
            with store.db:
                engine.save(engine.state_type(expected=inventory.get(NEAR, 0),
                                              first_price='39000' if inventory.get(NEAR) else None))
                engine.on_external_inventory(inventory.get(NEAR, 0))
            self.assertEqual(broker.startup_position(), inventory.get(NEAR, 0))
            self.assertEqual(orders, [])
            history = H3History(MddSizing(), direction, None, loss_extra_contracts=2,
                                peak_drawdown=Decimal(4500))
            with (patch('mdd_runtime.wait_for_signal_history', return_value=history),
                  patch.object(shared, 'POSITION_VERIFY_ATTEMPTS', 1)):
                event = existing.SignalFlattenTests().event(1, direction)
                if available:
                    with self.assertRaises(TradingError):
                        engine.on_signal(event)
                    self.assertTrue(broker.guard['pending']['close_only'])
                    self.assertFalse(engine.on_signal(event))
                    recovered = broker_type(api, sj, store, watchdog, records=directory, close_all_months=True)
                    self.assertFalse(recovered.guard.get('pending'))
                else:
                    self.assertTrue(engine.on_signal(event))
                    self.assertFalse(engine.on_signal(event))
            return orders, inventory, engine.state().expected

    def test_cross_month_then_mdd_near_order(self):
        for engine, broker, quantity in [(MddEngine, PyramidBroker, 2),
                                         (ProtectionEngine, ProtectionBroker, 1)]:
            for direction in (-1, 1):
                with self.subTest(engine=engine.__name__, direction=direction):
                    orders, inventory, expected = self.exercise(engine, broker, direction=direction)
                    self.assertEqual(orders, [(NEAR, 'Buy', 1, 'Cover'), (FAR, 'Sell', 1, 'Cover'),
                                              (NEAR, 'Buy' if direction > 0 else 'Sell', quantity, 'Auto')])
                    self.assertEqual(inventory, {NEAR: direction * quantity, FAR: 0})
                    self.assertEqual(expected, direction * quantity)

    def test_unfilled_near_preserves_far_and_partial_near_does_not_open(self):
        for engine, broker in [(MddEngine, PyramidBroker), (ProtectionEngine, ProtectionBroker)]:
            for fills in (0, 1):
                with self.subTest(engine=engine.__name__, fills=fills):
                    orders, inventory, _ = self.exercise(engine, broker, available={NEAR: fills},
                                                         positions={NEAR: -2, FAR: 2})
                    self.assertEqual(orders, [(NEAR, 'Buy', 2, 'Cover')])
                    self.assertEqual(inventory, {NEAR: -2 + fills, FAR: 2})

    def test_unfilled_far_blocks_new_position(self):
        orders, inventory, _ = self.exercise(MddEngine, PyramidBroker, available={FAR: 0})
        self.assertEqual(orders, [(NEAR, 'Buy', 1, 'Cover'), (FAR, 'Sell', 1, 'Cover')])
        self.assertEqual(inventory, {NEAR: 0, FAR: 1})

    def test_flat_signal_and_far_only(self):
        orders, inventory, expected = self.exercise(ProtectionEngine, ProtectionBroker,
                                                    direction=0, positions={FAR: 1})
        self.assertEqual(orders, [(FAR, 'Sell', 1, 'Cover')])
        self.assertEqual(inventory, {FAR: 0})
        self.assertEqual(expected, 0)

    def test_near_only_uses_existing_order_flow(self):
        orders, inventory, expected = self.exercise(MddEngine, PyramidBroker, positions={NEAR: -1})
        self.assertEqual(orders, [(NEAR, 'Buy', 1, 'Auto'), (NEAR, 'Buy', 2, 'Auto')])
        self.assertEqual(inventory, {NEAR: 2})
        self.assertEqual(expected, 2)

