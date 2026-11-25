import { loadMongoMarket } from '../server/mongo-data.mjs';

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    const url = new URL(req.url, 'https://localhost');
    res.status(200).json(await loadMongoMarket(url.searchParams.get('date'), url.searchParams.get('history') === '1'));
  } catch (error) {
    console.error(error);
    res.status(error instanceof RangeError ? 400 : 500).json({ error: error instanceof RangeError ? error.message : '無法讀取 MongoDB 行情資料' });
  }
}
