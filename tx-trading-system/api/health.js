import { allowGet } from '../server/api-request.mjs';

export default function handler(req, res) {
  if (!allowGet(req, res)) return;
  res.setHeader('Cache-Control', 'no-store');
  res.status(200).json({ app: 'tx-trading-system', status: 'ok' });
}
