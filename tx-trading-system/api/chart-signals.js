import { loadMongoChartSignals } from '../server/mongo-data.mjs';
import { allowGet, cacheReadResponse } from '../server/api-request.mjs';

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const url = new URL(req.url, 'https://localhost');
    const result = await loadMongoChartSignals(url.searchParams.get('date'), url.searchParams.get('from') || undefined);
    cacheReadResponse(res);
    res.status(200).json(result);
  } catch (error) {
    if (!(error instanceof RangeError)) console.error(error);
    res.setHeader('Cache-Control', 'no-store');
    res.status(error instanceof RangeError ? 400 : 500).json({ error: error instanceof RangeError ? error.message : '無法讀取策略點位' });
  }
}
