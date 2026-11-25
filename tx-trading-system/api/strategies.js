import { loadMongoStrategies } from '../server/mongo-data.mjs';

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    res.status(200).json(await loadMongoStrategies());
  } catch (error) {
    console.error(error);
    res.status(500).json({ error: '無法讀取 MongoDB 策略資料' });
  }
}
