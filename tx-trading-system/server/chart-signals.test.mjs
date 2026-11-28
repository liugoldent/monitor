import test from 'node:test';
import assert from 'node:assert/strict';
import { latestTradePosition, tradeChartEvent } from './chart-signals.mjs';

test('H2 timestamps are truncated to seconds and exits retain their side', () => {
  assert.deepEqual(tradeChartEvent({
    timestamp: '2026-11-27 10:40:34.838585', action: 'exiting', side: 'bear',
  }, 'h2'), {
    time: '2026-11-27 10:40:34', strategy: 'h2', action: 'exit', side: -1,
  });
});

test('H3 entries use the same format and invalid trades are omitted', () => {
  assert.deepEqual(tradeChartEvent({
    timestamp: '2026-11-26 15:03:07', action: 'enter', side: 'bull',
  }, 'h3'), {
    time: '2026-11-26 15:03:07', strategy: 'h3', action: 'enter', side: 1,
  });
  assert.equal(tradeChartEvent({ timestamp: 'bad', action: 'enter', side: 'bull' }, 'h3'), null);
});

test('latest trade shows held direction or flat after an exit', () => {
  assert.deepEqual(latestTradePosition({ timestamp: '2026-11-27 12:57:02.784288', action: 'enter', side: 'bear' }), {
    position: -1, time: '2026-11-27 12:57:02',
  });
  assert.deepEqual(latestTradePosition({ timestamp: '2026-11-28 04:30:34', action: 'exiting', side: 'bear' }), {
    position: 0, time: '2026-11-28 04:30:34',
  });
  assert.deepEqual(latestTradePosition(null), { position: null, time: null });
});
