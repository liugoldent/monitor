import { loadMongoChartSignals } from '../server/mongo-data.mjs';

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    const url = new URL(req.url, 'https://localhost');
    res.status(200).json(await loadMongoChartSignals(url.searchParams.get('date'), url.searchParams.get('from') || undefined));
  } catch (error) {
    console.error(error);
    res.status(error instanceof RangeError ? 400 : 500).json({ error: error instanceof RangeError ? error.message : '無法讀取策略點位' });
  }
}
