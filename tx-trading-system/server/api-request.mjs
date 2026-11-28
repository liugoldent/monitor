export function allowGet(req, res) {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (req.method === 'GET') return true;
  res.setHeader('Allow', 'GET');
  res.setHeader('Cache-Control', 'no-store');
  res.status(405).json({ error: '僅支援 GET' });
  return false;
}
