import test from 'node:test';
import assert from 'node:assert/strict';
import { buildStrategyMarkers } from './strategy-markers.mjs';

const t = value => Date.parse(value + '+08:00') / 1000;
const rows = ['2026-11-24T08:45:00', '2026-11-24T08:46:00', '2026-11-24T08:47:00'].map(value => ({ time: t(value), candle: { close: 28000 } }));
const colors = { long: 'red', short: 'green' };

test('h2 and h3 entries and exits keep their strategy and direction', () => {
  const markers = buildStrategyMarkers(rows, [
    { time: '2026-11-24 08:45:14', strategy: 'h2', action: 'enter', side: 1 },
    { time: '2026-11-24 08:45:20', strategy: 'h3', action: 'enter', side: -1 },
    { time: '2026-11-24 08:46:02', strategy: 'h2', action: 'exit', side: 1 },
    { time: '2026-11-24 08:47:00', strategy: 'h3', action: 'exit', side: -1 },
  ], colors);
  assert.deepEqual(markers.map(marker => [marker.text, marker.shape, marker.position]), [
    ['h2進', 'arrowUp', 'belowBar'],
    ['h3進', 'arrowDown', 'aboveBar'],
    ['h2出', 'arrowUp', 'belowBar'],
    ['h3出', 'arrowDown', 'aboveBar'],
  ]);
});

test('same-minute events combine only within the same strategy, action, and side', () => {
  const markers = buildStrategyMarkers(rows, [
    { time: '2026-11-24 08:45:14', strategy: 'h2', action: 'enter', side: 1 },
    { time: '2026-11-24 08:45:30', strategy: 'h2', action: 'enter', side: 1 },
    { time: '2026-11-24 08:45:35', strategy: 'h2', action: 'exit', side: 1 },
    { time: '2026-11-24 08:45:40', strategy: 'h3', action: 'enter', side: 1 },
  ], colors);
  assert.deepEqual(markers.map(marker => marker.text), ['h2進×2', 'h2出', 'h3進']);
});

test('signals without a nearby real candle are not placed on misleading bars', () => {
  const markers = buildStrategyMarkers([rows[0], { ...rows[1], candle: { imputed: true } }, rows[2]], [
    { time: '2026-11-24 08:40:00', strategy: 'h2', action: 'enter', side: 1 },
    { time: '2026-11-24 08:46:00', strategy: 'h2', action: 'enter', side: 1 },
    { time: 'unknown', strategy: 'h3', action: 'enter', side: -1 },
  ], colors);
  assert.equal(markers.length, 1);
  assert.equal(markers[0].time, rows[2].time);
});
