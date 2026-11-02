import test from 'node:test';
import assert from 'node:assert/strict';
import { isAtLatest, reconcileRange } from '../src/chart-range.mjs';
const points = (start, length) => Array.from({ length }, (_, i) => ({ time: start + i }));
test('new minutes move the right edge while preserving the zoom and right margin', () => {
  const range = reconcileRange(points(0, 200), points(0, 203), { from: 28, to: 208 });
  assert.deepEqual(range, { from: 31, to: 211 });
  assert.equal(isAtLatest(range, 203), true);
});
test('viewing history does not jump to the new right edge', () => {
  assert.deepEqual(reconcileRange(points(0, 200), points(0, 203), { from: 10, to: 100 }), { from: 10, to: 100 });
});
test('rolling windows and backfilled points preserve the viewed timestamp', () => {
  assert.deepEqual(reconcileRange(points(0, 200), points(3, 200), { from: 10.5, to: 100.5 }), { from: 7.5, to: 97.5 });
  assert.deepEqual(reconcileRange(points(3, 200), points(0, 203), { from: 10.5, to: 100.5 }), { from: 13.5, to: 103.5 });
});
test('crossing midnight continues following and correcting a bar keeps the viewport', () => {
  const old = [{ time: 1793548740 }];
  const next = [...old, { time: 1793548800 }];
  assert.deepEqual(reconcileRange(old, next, { from: -10, to: 8 }), { from: -9, to: 9 });
  assert.deepEqual(reconcileRange(points(0, 200), points(0, 200), { from: 20, to: 208 }), { from: 20, to: 208 });
});
