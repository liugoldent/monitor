import { readFile, stat } from 'node:fs/promises';
import { parse } from 'csv-parse/sync';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend-futures-py/tv_doc');
export const files = { candles: path.join(base, 'webhook_data_1min.csv'), chips: path.join(base, 'mxf_value.csv') };
export const number = value => value === undefined || value.trim() === '' ? null : Number.isFinite(Number(value)) ? Number(value) : null;
export function timestamp(value) {
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value ?? '')) return null;
  const result = Date.parse(value.replace(' ', 'T') + '+08:00') / 1000;
  return Number.isFinite(result) ? Math.floor(result / 60) * 60 : null;
}
export function normalize(text, kind) {
  const rows = parse(text, { columns: true, bom: true, skip_empty_lines: true, relax_column_count: true });
  const byTime = new Map();
  let invalid = 0;
  for (const row of rows) {
    const sourceTime = row[kind === 'candles' ? 'TradingView Time' : 'time'];
    const time = timestamp(sourceTime);
    if (time === null) { invalid++; continue; }
    const localTime = sourceTime.slice(0, 16) + ':00';
    if (kind === 'candles') {
      const [open, high, low, close] = ['Open', 'High', 'Low', 'Close'].map(key => number(row[key]));
      if ([open, high, low, close].includes(null) || low > Math.min(open, close) || high < Math.max(open, close) || high < low) { invalid++; continue; }
      byTime.set(time, { time, localTime, open, high, low, close, symbol: row.Symbol,
        ...Object.fromEntries(['MA_960', 'MA_P80', 'MA_P200', 'MA_N110', 'MA_N200', 'tt_short', 'tt_long', 'BBR'].map(key => [key, number(row[key])])) });
    } else {
      const fields = Object.fromEntries(['tx_bvav', 'mtx_bvav', 'mtx_tbta', 'mtx_bvav_avg'].map(key => [key, number(row[key])]));
      if (Object.values(fields).every(value => value === null)) { invalid++; continue; }
      byTime.set(time, { time, localTime, sourceTime, ...fields, signal: row.signal || 'none', trend: row.trend || 'none' });
    }
  }
  return { rows: [...byTime.values()].sort((a, b) => a.time - b.time), invalid, duplicates: rows.length - invalid - byTime.size };
}
export function joinRows(candles, chips) {
  const joined = new Map();
  for (const candle of candles) joined.set(candle.time, { time: candle.time, localTime: candle.localTime, candle, chip: null });
  for (const chip of chips) joined.set(chip.time, { time: chip.time, localTime: chip.localTime, candle: joined.get(chip.time)?.candle ?? null, chip });
  const sorted = [...joined.values()].sort((a, b) => a.time - b.time);
  const timeline = [];
  for (const row of sorted) {
    const previous = timeline.at(-1);
    // Add minutes missing from both files only for short gaps, never across a long session break.
    if (previous && row.time - previous.time <= 180) {
      for (let time = previous.time + 60; time < row.time; time += 60) {
        timeline.push({ time, localTime: new Date((time + 8 * 3600) * 1000).toISOString().slice(0, 19).replace('T', ' '), candle: null, chip: null });
      }
    }
    timeline.push(row);
  }
  const lastActual = { candle: null, chip: null };
  return timeline.map(row => {
    const result = { ...row };
    for (const key of ['candle', 'chip']) {
      if (row[key]) { lastActual[key] = row[key]; continue; }
      const previous = lastActual[key];
      if (previous && row.time - previous.time <= 120) {
        result[key] = { ...previous, time: row.time, localTime: row.localTime, imputed: true, imputedFrom: previous.time };
      }
    }
    return result;
  });
}
let cache;
let pending;
export async function loadData() {
  const stats = await Promise.all(Object.values(files).map(file => stat(file)));
  const signature = stats.map(s => `${s.mtimeMs}:${s.size}`).join('|');
  if (cache?.signature === signature) return cache;
  if (pending) return pending;
  pending = (async () => {
    const texts = await Promise.all(Object.values(files).map(file => readFile(file, 'utf8')));
    const candles = normalize(texts[0], 'candles');
    const chips = normalize(texts[1], 'chips');
    const rows = joinRows(candles.rows, chips.rows);
    const dates = [...new Set(candles.rows.map(row => row.localTime.slice(0, 10)))].reverse();
    cache = { signature, rows, dates, sources: { candles: { file: 'webhook_data_1min.csv', count: candles.rows.length, invalid: candles.invalid, duplicates: candles.duplicates }, chips: { file: 'mxf_value.csv', count: chips.rows.length, invalid: chips.invalid, duplicates: chips.duplicates } } };
    return cache;
  })();
  try { return await pending; } finally { pending = null; }
}
