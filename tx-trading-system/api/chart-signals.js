import { loadMongoChartSignals } from '../server/mongo-data.mjs';
import { allowGet } from '../server/api-request.mjs';
import { cachedStrategyData } from '../server/strategy-cache.mjs';

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const url = new URL(req.url, 'https://localhost');
    const date = url.searchParams.get('date');
    const from = url.searchParams.get('from') || undefined;
    const result = await cachedStrategyData(`chart:${date}:${from}`, () => loadMongoChartSignals(date, from));
    res.setHeader('Cache-Control', 'public, max-age=15, s-maxage=30, stale-while-revalidate=30');
    res.status(200).json(result);
  } catch (error) {
    if (!(error instanceof RangeError)) console.error(error);
    res.setHeader('Cache-Control', 'no-store');
    res.status(error instanceof RangeError ? 400 : 500).json({ error: error instanceof RangeError ? error.message : '無法讀取策略點位' });
  }
}
