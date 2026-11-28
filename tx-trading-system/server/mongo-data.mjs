import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { MongoClient } from 'mongodb';
import { joinRows, normalizeRecords } from './data.mjs';
import { summarizeStrategyRows } from './strategies.mjs';
import { latestTradePosition, tradeChartEvent } from './chart-signals.mjs';
import { availableCandleEntry, floatingPoints, latestOpenEvent, priceNumber } from './position-points.mjs';

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

async function loadLatestHPositions(databaseHandle) {
  const latestTrade = name => databaseHandle.collection(name).find({}, {
    projection: { _id: 0, timestamp: 1, action: 1, side: 1, price: 1 },
  }).sort({ timestamp: -1, action: 1 }).limit(1).toArray();
  const [latestH2, latestH3] = await Promise.all([latestTrade('h2_trades'), latestTrade('h_trades')]);
  const position = row => {
    const result = latestTradePosition(row);
    return { ...result, entryPrice: result.position ? priceNumber(row?.price) : null };
  };
  return { h2: position(latestH2[0]), h3: position(latestH3[0]) };
}

async function availableDates(candles) {
  if (datesCache && Date.now() - datesCache.at < 30000) return datesCache.values;
  const values = (await candles.distinct('date')).filter(value => /^\d{4}-\d{2}-\d{2}$/.test(value)).sort().reverse();
  datesCache = { at: Date.now(), values };
  return values;
}

export async function loadMongoMarket(requestedDate, history = false) {
  if (requestedDate && !/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)) throw new RangeError('日期格式錯誤');
  const databaseHandle = await db();
  const candlesCollection = databaseHandle.collection('candles_1m');
  const chipsCollection = databaseHandle.collection('chips_1m');
  const dates = await availableDates(candlesCollection);
  const date = requestedDate || dates[0] || '';
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
  const databaseHandle = await db();
  const candles = databaseHandle.collection('candles_1m');
  const [rows, hPositions, latestCandles] = await Promise.all([
    databaseHandle.collection('strategy_signals').find({}, { projection: { _id: 0 } }).sort({ source_order: 1 }).toArray(),
    loadLatestHPositions(databaseHandle),
    candles.find({ Symbol: 'MXF1!' }, { projection: { _id: 0, 'TradingView Time': 1, Close: 1 } })
      .sort({ date: -1, 'TradingView Time': -1 }).limit(5).toArray(),
  ]);
  const latestCandle = latestCandles.find(row => priceNumber(row.Close) !== null && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(row['TradingView Time'] ?? ''));
  const market = latestCandle ? { close: priceNumber(latestCandle.Close), time: latestCandle['TradingView Time'] } : null;
  const result = summarizeStrategyRows(rows);
  const strategies = await Promise.all(result.strategies.map(async strategy => {
    const opening = latestOpenEvent(strategy);
    const receivedAt = opening?.receivedAt ?? opening?.time;
    const candle = receivedAt && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(receivedAt)
      ? await candles.find({ Symbol: 'MXF1!', 'Record Time': { $lte: receivedAt } }, {
        projection: { _id: 0, 'Record Time': 1, 'TradingView Time': 1, Close: 1 },
      }).sort({ 'Record Time': -1 }).limit(1).next()
      : null;
    const entry = availableCandleEntry(candle, receivedAt);
    return { ...strategy,
      entry: opening ? { signalTime: opening.time, price: entry?.price ?? null, candleTime: entry?.time ?? null, source: 'available-candle-close' } : null,
      floatingPoints: floatingPoints(strategy.position, entry?.price, market?.close),
    };
  }));
  const pricedHPositions = Object.fromEntries(Object.entries(hPositions).map(([code, position]) => [code, {
    ...position, floatingPoints: floatingPoints(position.position, position.entryPrice, market?.close),
  }]));
  return { ...result, strategies, hPositions: pricedHPositions, market, refreshedAt: new Date().toISOString() };
}

export async function loadMongoChartSignals(date, from = date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? '')) throw new RangeError('日期格式錯誤');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from ?? '') || from > date) throw new RangeError('起始日期格式錯誤');
  if (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`) > 45 * 86400000) {
    throw new RangeError('查詢區間不得超過 45 天');
  }
  const databaseHandle = await db();
  const dateRange = { date: { $gte: from, $lte: date } };
  const [h2Trades, h3Trades, positions] = await Promise.all([
    databaseHandle.collection('h2_trades').find(dateRange, {
      projection: { _id: 0, timestamp: 1, action: 1, side: 1 },
    }).toArray(),
    databaseHandle.collection('h_trades').find(dateRange, {
      projection: { _id: 0, timestamp: 1, action: 1, side: 1 },
    }).toArray(),
    loadLatestHPositions(databaseHandle),
  ]);
  const signals = [
    ...h2Trades.map(row => tradeChartEvent(row, 'h2')),
    ...h3Trades.map(row => tradeChartEvent(row, 'h3')),
  ].filter(Boolean);
  return {
    date, from, signals,
    positions,
    refreshedAt: new Date().toISOString(),
  };
}
