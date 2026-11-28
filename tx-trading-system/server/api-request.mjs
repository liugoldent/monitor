export function allowGet(req, res) {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (req.method === 'GET') return true;
  res.setHeader('Allow', 'GET');
  res.setHeader('Cache-Control', 'no-store');
  res.status(405).json({ error: '僅支援 GET' });
  return false;
}

export function cacheReadResponse(res) {
  // Keep browsers fresh while reusing the same public result at Vercel's CDN.
  res.setHeader('Cache-Control', 'public, max-age=0, s-maxage=15, stale-while-revalidate=15');
}
