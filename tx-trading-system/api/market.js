import { loadMongoMarket } from '../server/mongo-data.mjs';
import { allowGet, cacheReadResponse } from '../server/api-request.mjs';

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const url = new URL(req.url, 'https://localhost');
    const history = url.searchParams.get('history');
    if (history !== null && history !== '0' && history !== '1') throw new RangeError('歷史資料參數錯誤');
    const result = await loadMongoMarket(url.searchParams.get('date'), history === '1');
    cacheReadResponse(res);
    res.status(200).json(result);
  } catch (error) {
    if (!(error instanceof RangeError)) console.error(error);
    res.setHeader('Cache-Control', 'no-store');
    res.status(error instanceof RangeError ? 400 : 500).json({ error: error instanceof RangeError ? error.message : '無法讀取 MongoDB 行情資料' });
  }
}
