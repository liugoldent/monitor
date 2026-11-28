import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { MongoClient } from 'mongodb';
import { joinRows, normalizeRecords } from './data.mjs';
import { summarizeStrategyRows } from './strategies.mjs';

let client;
let datesCache;
const database = 'tx_trading_system';

async function mongoUri() {
  if (process.env.MONGO_URI) return process.env.MONGO_URI;
  if (process.env.VERCEL) throw new Error('MONGO_URI is not configured');
  const envPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend-futures-py/.env');
  const text = await readFile(envPath, 'utf8');
  const line = text.split(/\r?\n/).find(value => value.startsWith('MONGO_URI='));
  if (!line) throw new Error('MONGO_URI is not configured');
  return line.slice('MONGO_URI='.length).trim().replace(/^["']|["']$/g, '');
}

async function db() {
  if (!client) client = new MongoClient(await mongoUri(), { serverSelectionTimeoutMS: 5000 });
  return client.db(database);
}

async function availableDates(candles) {
  if (datesCache && Date.now() - datesCache.at < 30000) return datesCache.values;
  const values = (await candles.distinct('date')).filter(value => /^\d{4}-\d{2}-\d{2}$/.test(value)).sort().reverse();
  datesCache = { at: Date.now(), values };
  return values;
}

export async function loadMongoMarket(requestedDate, history = false) {
  const databaseHandle = await db();
  const candlesCollection = databaseHandle.collection('candles_1m');
  const chipsCollection = databaseHandle.collection('chips_1m');
  const dates = await availableDates(candlesCollection);
  const date = requestedDate || dates[0] || '';
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new RangeError('日期格式錯誤');
  const query = { date: { $lte: date } };
  const [candleDocuments, chipDocuments] = await Promise.all([
    candlesCollection.find(query, { projection: { _id: 0 } }).sort({ date: -1, 'TradingView Time': -1 }).limit(10000).toArray(),
    chipsCollection.find(query, { projection: { _id: 0 } }).sort({ date: -1, time: -1 }).limit(10000).toArray(),
  ]);
  const candles = normalizeRecords(candleDocuments.reverse(), 'candles');
  const chips = normalizeRecords(chipDocuments.reverse(), 'chips');
  const allRows = joinRows(candles.rows, chips.rows);
  const rows = allRows.filter(row => row.localTime.startsWith(date));
  const historyRows = history ? allRows.slice(-10000) : undefined;
  const matched = rows.filter(row => row.candle && row.chip).length;
  return {
    date, dates, rows, historyRows,
    sources: {
      candles: { file: 'MongoDB: candles_1m', count: candles.rows.length, invalid: candles.invalid, duplicates: candles.duplicates },
      chips: { file: 'MongoDB: chips_1m', count: chips.rows.length, invalid: chips.invalid, duplicates: chips.duplicates },
    },
    stats: { candles: rows.filter(row => row.candle).length, chips: rows.filter(row => row.chip).length, matched },
    refreshedAt: new Date().toISOString(),
  };
}

export async function loadMongoStrategies() {
  const collection = (await db()).collection('strategy_signals');
  const rows = await collection.find({}, { projection: { _id: 0 } }).sort({ source_order: 1 }).toArray();
  return { ...summarizeStrategyRows(rows), refreshedAt: new Date().toISOString() };
}

export async function loadMongoChartSignals(date, from = date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? '')) throw new RangeError('日期格式錯誤');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from ?? '') || from > date) throw new RangeError('起始日期格式錯誤');
  const databaseHandle = await db();
  const dateRange = { date: { $gte: from, $lte: date } };
  const [ef, hEvents, hTrades] = await Promise.all([
    databaseHandle.collection('strategy_signals').find(dateRange, {
      projection: { _id: 0, message_time: 1, received_at: 1, previous_position: 1, new_position: 1 },
    }).toArray(),
    databaseHandle.collection('h_position_events').find(dateRange, {
      projection: { _id: 0, received_at: 1, new_position: 1 },
    }).toArray(),
    databaseHandle.collection('h_trades').find({ ...dateRange, action: 'enter' }, {
      projection: { _id: 0, timestamp: 1, side: 1 },
    }).toArray(),
  ]);
  const hSignals = new Map();
  for (const row of hEvents) {
    const signal = { time: row.received_at, position: Number(row.new_position), source: 'H' };
    if (signal.time && [-1, 1].includes(signal.position)) hSignals.set(`${signal.time.slice(0, 16)}:${signal.position}`, signal);
  }
  for (const row of hTrades) {
    const signal = { time: row.timestamp, position: row.side === 'bull' ? 1 : row.side === 'bear' ? -1 : null, source: 'H' };
    if (signal.time && signal.position != null) {
      const key = `${signal.time.slice(0, 16)}:${signal.position}`;
      if (!hSignals.has(key)) hSignals.set(key, signal);
    }
  }
  const signals = [
    ...ef.map(row => ({ time: row.message_time || row.received_at, position: Number(row.new_position), source: 'EF' })),
    ...hSignals.values(),
  ].filter(row => row.time && [-1, 0, 1].includes(row.position));
  return { date, from, signals, refreshedAt: new Date().toISOString() };
}
