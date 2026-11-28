import test from 'node:test';
import assert from 'node:assert/strict';
import { availableCandleEntry, floatingPoints, latestOpenEvent, priceNumber } from './position-points.mjs';

test('long and short open positions use signed point changes; flat and missing prices stay unknown', () => {
  assert.equal(floatingPoints(1, 39248, 39177), -71);
  assert.equal(floatingPoints(-1, 39323, 39177), 146);
  assert.equal(floatingPoints(-1, 39281, 39177), 104);
  assert.equal(floatingPoints(0, 39248, 39177), null);
  assert.equal(floatingPoints(1, null, 39177), null);
  assert.equal(priceNumber(''), null);
});

test('entry estimate only uses a candle already received at signal time', () => {
  const candle = { 'Record Time': '2026-11-28 01:59:04', 'TradingView Time': '2026-11-28 01:58:00', Close: '39248' };
  assert.deepEqual(availableCandleEntry(candle, '2026-11-28 02:00:02'), {
    price: 39248, time: '2026-11-28 01:58:00', recordedAt: '2026-11-28 01:59:04',
  });
  assert.equal(availableCandleEntry({ ...candle, 'Record Time': '2026-11-28 02:00:03' }, '2026-11-28 02:00:02'), null);
  assert.equal(availableCandleEntry(candle, '2026-11-28 02:02:05'), null);
});

test('current entry is the latest opening or reversal, ignoring later adjustments', () => {
  const strategy = { position: -2, events: [
    { action: 'adjust', position: -2 },
    { action: 'reverse', position: -1, time: '2026-11-28 01:00:00' },
    { action: 'enter', position: 1 },
  ] };
  assert.equal(latestOpenEvent(strategy)?.time, '2026-11-28 01:00:00');
  assert.equal(latestOpenEvent({ ...strategy, position: 0 }), null);
});
