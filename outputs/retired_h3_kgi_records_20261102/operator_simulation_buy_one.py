"""Operator-requested, simulation-only BUY 1 IOC/MKT, at most once for this test."""
import contextlib
import json
import msvcrt
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from kgi_monitor import account_key, credentials, load_env, read_config
from shared_h import Store, TradingError
from kgi_broker import KgiBroker
from mdd_sizing import Discord, MddPolicy, execute_mdd, read_webhook

runtime = BASE / 'runtime'
result_path = runtime / 'operator_simulation_buy_one_result.json'
result = dict(environment='simulation', product='TMFJ6', action='BUY', quantity=1,
              price_type='MKT', time_in_force='IOC', submission_attempted=False, confirmed=False)
broker = store = lock = None
with (runtime / 'operator_simulation_buy_one_private.log').open('a', encoding='utf-8') as log:
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            load_env(BASE / '.env')
            creds = credentials()
            if creds[-1] is not True:
                raise TradingError('This operator test only supports the simulation environment')
            config = read_config(BASE / 'h-config.json')
            if config['contract'].symbol != 'TMFJ6':
                raise TradingError('Unexpected contract; test not submitted')
            key = account_key(creds[2], creds[3], True)
            lock = (runtime / f'{key}.lock').open('a+b')
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            store = Store(runtime / f'{key}.sqlite')
            if store.pending():
                raise TradingError('Pending order guard exists; no new test order')
            policy = MddPolicy(store, config['contract'].symbol, one_way_cost=config['mdd_one_way_cost'])
            if policy.quantity() != 1 or policy.state()['open_trade'] is not None:
                raise TradingError('Test requires base-one sizing and no recorded open trade')
            sdk_dir = runtime / f'{key}-sdk'
            sdk_dir.mkdir(exist_ok=True)
            os.chdir(sdk_dir)
            broker = KgiBroker(config, store)
            broker.login(*creds)
            position = broker.position()
            policy.validate_position(position)
            if position != 0:
                raise TradingError('Test requires a flat account; no position will be closed')
            trigger = 'operator-simulation-buy-one-ioc-mkt-20261015'
            if not store.consume(trigger, 1, __import__('time').time()):
                raise TradingError('This manual test was already consumed; never resubmit')
            notify = Discord(read_webhook(BASE.parent / '.env'), store)
            target = execute_mdd(broker, policy, trigger, 1,
                                 lambda message: notify('【凱基測試環境｜手動買進1口測試】\n' + message))
            result.update(confirmed=target == 1, position=target,
                          fill_price=broker.last_fill['price'], order_id=broker.last_fill['order_id'])
        except Exception as exc:
            result['error_type'] = type(exc).__name__
            if store:
                store.audit('operator_simulation_buy_one_stopped', {'error': type(exc).__name__})
        finally:
            if broker:
                result['submission_attempted'] = broker.last_submission is not None
                try:
                    broker.close()
                except Exception:
                    result['logout_warning'] = True
            if store:
                result['pending_order_guard'] = bool(store.pending())
                store.db.close()
            if lock:
                lock.close()
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
