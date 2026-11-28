import test from 'node:test';
import assert from 'node:assert/strict';
import { publicStrategyCutoff, publicTradeFilter } from './public-delay.mjs';

test('public strategy cutoff uses Taipei time and delays across midnight', () => {
  assert.equal(publicStrategyCutoff(Date.parse('2026-11-28T16:15:00Z')), '2026-11-28 23:45:00');
  assert.equal(publicStrategyCutoff(Date.parse('2026-11-28T00:00:00Z')), '2026-11-28 07:30:00');
});

test('public H2 and H3 filters always include the delay cutoff', () => {
  const cutoff = '2026-11-28 21:00:00';
  assert.deepEqual(publicTradeFilter(cutoff), { timestamp: { $lte: cutoff } });
  assert.deepEqual(publicTradeFilter(cutoff, '2026-11-27', '2026-11-29'), {
    date: { $gte: '2026-11-27', $lte: '2026-11-29' },
    timestamp: { $lte: cutoff },
  });
});
