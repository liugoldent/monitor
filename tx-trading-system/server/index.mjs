import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadMongoMarket, loadMongoStrategies, loadMongoChartSignals } from './mongo-data.mjs';
import { hasStrategySession, strategySessionCookie, verifyStrategyCode } from './strategy-auth.mjs';
import { cachedStrategyData } from './strategy-cache.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const production = process.argv.includes('--production');
const vite = production ? null : await (await import('vite')).createServer({ root, server: { middlewareMode: true }, appType: 'spa' });
const port = Number(process.env.PORT || 5173);
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (url.pathname === '/api/strategy-login') {
      if (req.method !== 'POST') { res.writeHead(405, { Allow: 'POST' }); res.end(JSON.stringify({ error: '僅支援 POST' })); return; }
      try {
        let body = '';
        for await (const chunk of req) {
          body += chunk;
          if (body.length > 1024) { res.writeHead(413); res.end(JSON.stringify({ error: '請求內容過長' })); return; }
        }
        const allowed = await verifyStrategyCode(JSON.parse(body).code);
        if (!allowed) { res.writeHead(allowed === null ? 503 : 401); res.end(JSON.stringify({ error: allowed === null ? '策略存取尚未設定' : '存取碼錯誤' })); return; }
        res.setHeader('Set-Cookie', await strategySessionCookie());
        res.end(JSON.stringify({ ok: true }));
      } catch { res.writeHead(400); res.end(JSON.stringify({ error: '請求內容格式錯誤' })); }
      return;
    }
    if (req.method !== 'GET') { res.writeHead(405, { Allow: 'GET' }); res.end(JSON.stringify({ error: '僅支援 GET' })); return; }
    try {
      if (url.pathname === '/api/health') { res.end(JSON.stringify({ app: 'tx-trading-system', status: 'ok' })); return; }
      if (url.pathname === '/api/strategies') {
        const allowed = await hasStrategySession(req);
        if (!allowed) { res.writeHead(allowed === null ? 503 : 401); res.end(JSON.stringify({ error: allowed === null ? '策略存取尚未設定' : '請先解鎖策略資料' })); return; }
        try { res.end(JSON.stringify(await cachedStrategyData('strategies', loadMongoStrategies))); }
        catch (error) { console.error(error.message); res.writeHead(500); res.end(JSON.stringify({ error: '無法讀取 MongoDB 策略資料。' })); }
        return;
      }
      if (url.pathname === '/api/chart-signals') {
        const allowed = await hasStrategySession(req);
        if (!allowed) { res.writeHead(allowed === null ? 503 : 401); res.end(JSON.stringify({ error: allowed === null ? '策略存取尚未設定' : '請先解鎖策略資料' })); return; }
        const date = url.searchParams.get('date');
        const from = url.searchParams.get('from') || undefined;
        res.end(JSON.stringify(await cachedStrategyData(`chart:${date}:${from}`, () => loadMongoChartSignals(date, from))));
        return;
      }
      if (url.pathname !== '/api/market') { res.writeHead(404); res.end(JSON.stringify({ error: '找不到 API' })); return; }
      res.end(JSON.stringify(await loadMongoMarket(url.searchParams.get('date'), url.searchParams.get('history') === '1')));
    } catch (error) {
      console.error(error.message);
      res.writeHead(error instanceof RangeError ? 400 : 500);
      res.end(JSON.stringify({ error: error instanceof RangeError ? error.message : '無法讀取 MongoDB 行情資料。' }));
    }
    return;
  }
  if (vite) { vite.middlewares(req, res); return; }
  try {
    const requested = decodeURIComponent(url.pathname);
    const file = path.resolve(root, 'dist', '.' + requested);
    const dist = path.join(root, 'dist') + path.sep;
    if (!file.startsWith(dist) && file !== path.join(root, 'dist')) { res.writeHead(403); res.end(); return; }
    const extension = path.extname(file);
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.html': 'text/html' };
    const content = await readFile(extension ? file : path.join(root, 'dist/index.html'));
    res.setHeader('Content-Type', types[extension] || 'text/html; charset=utf-8'); res.end(content);
  } catch { res.writeHead(404); res.end('Not found'); }
});
server.listen(port, '127.0.0.1', () => console.log(`TX Trading ready at http://localhost:${port}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
  if (!production) {
    // Node's --watch waits for the child to exit before starting the updated API.
    server.closeAllConnections();
    server.close();
    process.exit(0);
  }
  await vite?.close();
  server.close();
});
