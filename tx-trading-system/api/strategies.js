import { loadMongoStrategies } from '../server/mongo-data.mjs';
import { allowGet } from '../server/api-request.mjs';
import { cachedStrategyData } from '../server/strategy-cache.mjs';

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const result = await cachedStrategyData('strategies', loadMongoStrategies);
    res.setHeader('Cache-Control', 'public, max-age=15, s-maxage=30, stale-while-revalidate=30');
    res.status(200).json(result);
  } catch (error) {
    console.error(error);
    res.setHeader('Cache-Control', 'no-store');
    res.status(500).json({ error: '無法讀取 MongoDB 策略資料' });
  }
}
