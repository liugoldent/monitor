import { readFile, stat } from 'node:fs/promises';
import { parse } from 'csv-parse/sync';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend-futures-py/tv_doc/six_strategy_signal_events.csv');
const numeric = value => value == null || value.trim() === '' ? null : Number.isFinite(Number(value)) ? Number(value) : null;
const direction = value => value > 0 ? 'long' : value < 0 ? 'short' : 'flat';
export function summarizeStrategies(text) {
  const rows = parse(text, { columns: true, bom: true, skip_empty_lines: true, relax_column_count: true });
  const strategies = new Map();
  let invalid = 0;
  for (const [index, row] of rows.entries()) {
    const code = row.strategy_code?.trim();
    const previous = numeric(row.previous_position);
    const position = numeric(row.new_position);
    if (!code || previous === null || position === null || previous === position) { invalid++; continue; }
    // The producer also rebuilds current state in CSV append order.
    // Blank times in imported historical records never become invented timestamps.
    const action = previous === 0 ? 'enter' : position === 0 ? 'exit' : Math.sign(previous) !== Math.sign(position) ? 'reverse' : 'adjust';
    const event = { id: index + 1, time: row.message_time || row.received_at || null,
      receivedAt: row.received_at || null, account: row.account || null,
      previousPosition: previous, position, action, quantity: numeric(row.quantity),
      direction: direction(position === 0 ? previous : position), signal: row.signal || '' };
    const strategy = strategies.get(code) ?? { id: code, code, name: row.strategy_name || code, events: [] };
    strategy.name = row.strategy_name || strategy.name;
    strategy.position = position;
    strategy.direction = direction(position);
    strategy.lastEvent = event;
    strategy.events.push(event);
    strategies.set(code, strategy);
  }
  const result = [...strategies.values()];
  const summary = { total: result.length, long: 0, short: 0, flat: 0, longUnits: 0, shortUnits: 0, net: 0, events: 0 };
  for (const strategy of result) {
    summary[strategy.direction]++;
    summary.net += strategy.position;
    summary.longUnits += Math.max(0, strategy.position);
    summary.shortUnits += Math.max(0, -strategy.position);
    summary.events += strategy.events.length;
    strategy.events.reverse();
  }
  summary.direction = direction(summary.net);
  result.sort((a, b) => Number(b.position !== 0) - Number(a.position !== 0) || a.name.localeCompare(b.name, 'zh-TW', { numeric: true }));
  return { strategies: result, summary, invalid, source: 'six_strategy_signal_events.csv', order: 'file-append' };
}
let cache;
export async function loadStrategies() {
  const info = await stat(file);
  const signature = `${info.mtimeMs}:${info.size}`;
  if (cache?.signature !== signature) cache = { signature, data: summarizeStrategies(await readFile(file, 'utf8')) };
  return { ...cache.data, refreshedAt: new Date().toISOString() };
}
