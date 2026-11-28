import { strategySessionCookie, verifyStrategyCode } from '../server/strategy-auth.mjs';

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: '僅支援 POST' });
  }
  const allowed = await verifyStrategyCode(req.body?.code);
  if (allowed === null) return res.status(503).json({ error: '策略存取尚未設定' });
  if (!allowed) return res.status(401).json({ error: '存取碼錯誤' });
  res.setHeader('Set-Cookie', await strategySessionCookie());
  return res.status(200).json({ ok: true });
}
