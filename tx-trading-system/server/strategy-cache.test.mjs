import test from 'node:test';
import assert from 'node:assert/strict';
import { cachedStrategyData } from './strategy-cache.mjs';

test('repeated and concurrent strategy reads share one database load', async () => {
  let loads = 0;
  const load = async () => { loads++; return { position: -1 }; };
  const [first, second] = await Promise.all([
    cachedStrategyData('test-concurrent', load),
    cachedStrategyData('test-concurrent', load),
  ]);
  const third = await cachedStrategyData('test-concurrent', load);
  assert.deepEqual([first, second, third], [{ position: -1 }, { position: -1 }, { position: -1 }]);
  assert.equal(loads, 1);
});
