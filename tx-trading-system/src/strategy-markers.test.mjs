import test from 'node:test';
import assert from 'node:assert/strict';
import { buildStrategyMarkers } from './strategy-markers.mjs';

const t = value => Date.parse(value + '+08:00') / 1000;
const rows = ['2026-11-24T08:45:00', '2026-11-24T08:46:00', '2026-11-24T08:47:00'].map(value => ({ time: t(value), candle: { close: 28000 } }));
const colors = { long: 'red', short: 'green', exit: 'gray' };

test('strategy signals align to candles and combine same-minute directions', () => {
  const markers = buildStrategyMarkers(rows, [
    { time: '2026-11-24 08:45:14', position: 1 },
    { time: '2026-11-24 08:45:30', position: 1 },
    { time: '2026-11-24 08:46:02', position: -1 },
    { time: '2026-11-24 08:47:00', position: 0 },
  ], colors);
  assert.deepEqual(markers.map(marker => [marker.time, marker.text, marker.shape]), [
    [rows[0].time, 'L×2', 'arrowUp'],
    [rows[1].time, 'S', 'arrowDown'],
    [rows[2].time, 'X', 'circle'],
  ]);
});

test('signals without a nearby real candle are not placed on misleading bars', () => {
  const markers = buildStrategyMarkers([rows[0], { ...rows[1], candle: { imputed: true } }, rows[2]], [
    { time: '2026-11-24 08:40:00', position: 1 },
    { time: '2026-11-24 08:46:00', position: 1 },
    { time: 'unknown', position: 1 },
  ], colors);
  assert.equal(markers.length, 1);
  assert.equal(markers[0].time, rows[2].time);
});

test('H entries keep their own labels beside EF signals', () => {
  const markers = buildStrategyMarkers(rows, [
    { time: '2026-11-24 08:45:14', position: 1, source: 'EF' },
    { time: '2026-11-24 08:45:20', position: 1, source: 'H' },
    { time: '2026-11-24 08:46:02', position: -1, source: 'H' },
  ], colors);
  assert.deepEqual(markers.map(marker => marker.text), ['L', 'HL', 'HS']);
});
