import test from 'node:test';
import assert from 'node:assert/strict';
import { timestamp, normalize, joinRows } from './data.mjs';

test('timestamps use explicit Taipei timezone, normalize seconds and reject malformed times', () => {
  assert.equal(timestamp('2026-11-02 08:45:00'), Date.parse('2026-11-02T00:45:00Z') / 1000);
  assert.equal(timestamp('2026-11-02 08:45:12'), timestamp('2026-11-02 08:45:00'));
  assert.equal(timestamp('bad'), null);
});
test('K bars join by TradingView time; short gaps carry previous data without filling absent fields', () => {
  const c = normalize('Record Time,TradingView Time,Open,High,Low,Close,MA_960\n2026-11-02 08:46:07,2026-11-02 08:45:00,100,105,99,104,\n2026-11-02 08:47:03,2026-11-02 08:46:00,104,106,103,105,102\n', 'candles');
  const m = normalize('time,tx_bvav,mtx_bvav,mtx_tbta,mtx_bvav_avg,signal,trend\n2026-11-02 08:45:00,0,-2,3,,bull,none\n2026-11-02 08:47:00,5,6,7,8,bear,death\n', 'chips');
  const joined = joinRows(c.rows, m.rows);
  assert.equal(joined.length, 3);
  assert.equal(joined[0].chip.tx_bvav, 0);
  assert.equal(joined[0].candle.close, 104);
  assert.equal(joined[0].candle.MA_960, null);
  assert.equal(joined[0].chip.mtx_bvav_avg, null);
  assert.equal(joined[1].chip.tx_bvav, 0);
  assert.equal(joined[1].chip.imputed, true);
  assert.equal(joined[1].chip.mtx_bvav_avg, null);
  assert.equal(joined[2].candle.close, 105);
  assert.equal(joined[2].candle.imputed, true);
});
test('forward fill expires after two minutes and never uses a future sample', () => {
  const t = timestamp('2026-11-02 08:45:00');
  const candles = [0, 1, 2, 3, 4].map(i => ({ time: t + i * 60, localTime: `2026-11-02 08:${45 + i}:00`, close: 100 + i }));
  const chips = [{ time: t + 60, localTime: candles[1].localTime, tx_bvav: 42 }];
  const result = joinRows(candles, chips);
  assert.equal(result[0].chip, null);
  assert.equal(result[2].chip.tx_bvav, 42);
  assert.equal(result[3].chip.tx_bvav, 42);
  assert.equal(result[3].chip.imputedFrom, t + 60);
  assert.equal(result[4].chip, null);
  assert.equal(chips[0].imputed, undefined);
});
test('one or two minutes absent in both files are inserted, session breaks are not', () => {
  const t = timestamp('2026-11-02 08:45:00');
  const source = [0, 3, 20].map(i => ({ time: t + i * 60, localTime: new Date((t + i * 60 + 28800) * 1000).toISOString().slice(0, 19).replace('T', ' '), close: 100 + i }));
  const result = joinRows(source, []);
  assert.deepEqual(result.map(r => r.time), [0, 1, 2, 3, 20].map(i => t + i * 60));
  assert.equal(result[1].candle.close, 100);
  assert.equal(result[2].candle.close, 100);
  assert.equal(result[2].candle.localTime, '2026-11-02 08:47:00');
  assert.equal(result[3].candle.imputed, undefined);
});
test('real backfilled values replace carried values and work across midnight', () => {
  const t = timestamp('2026-11-02 23:59:00');
  const candles = [{ time: t, localTime: '2026-11-02 23:59:00', close: 100 }, { time: t + 120, localTime: '2026-11-03 00:01:00', close: 102 }];
  const chips = [{ time: t, localTime: candles[0].localTime, tx_bvav: 1 }, { time: t + 60, localTime: '2026-11-03 00:00:00', tx_bvav: 2 }];
  const result = joinRows(candles, chips);
  assert.equal(result[1].candle.close, 100);
  assert.equal(result[1].candle.localTime, '2026-11-03 00:00:00');
  assert.equal(result[1].chip.tx_bvav, 2);
  assert.equal(result[1].chip.imputed, undefined);
});
test('duplicate minutes take last row, truncated writes and invalid candles are skipped', () => {
  const result = normalize('TradingView Time,Open,High,Low,Close\n2026-11-02 08:45:00,100,105,99,104\n2026-11-02 08:45:00,100,106,99,105\n2026-11-02 08:46:00,100\n2026-11-02 08:47:00,100,99,98,105\n', 'candles');
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].close, 105);
  assert.equal(result.duplicates, 1);
  assert.equal(result.invalid, 2);
});
test('off-minute chip samples keep their original timestamp and match the minute', () => {
  const result = normalize('time,tx_bvav\n2026-11-02 08:45:01,8\n', 'chips');
  assert.equal(result.rows[0].localTime, '2026-11-02 08:45:00');
  assert.equal(result.rows[0].sourceTime, '2026-11-02 08:45:01');
  assert.equal(result.rows[0].time, timestamp('2026-11-02 08:45:00'));
});
