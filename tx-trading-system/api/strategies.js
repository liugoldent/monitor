import { loadMongoStrategies } from '../server/mongo-data.mjs';
import { allowGet, cacheReadResponse } from '../server/api-request.mjs';

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const result = await loadMongoStrategies();
    cacheReadResponse(res);
    res.status(200).json(result);
  } catch (error) {
    console.error(error);
    res.setHeader('Cache-Control', 'no-store');
    res.status(500).json({ error: '無法讀取 MongoDB 策略資料' });
  }
}
