import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadMongoMarket, loadMongoStrategies, loadMongoChartSignals } from './mongo-data.mjs';

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
    if (req.method !== 'GET') { res.writeHead(405, { Allow: 'GET' }); res.end(JSON.stringify({ error: '僅支援 GET' })); return; }
    try {
      if (url.pathname === '/api/health') { res.end(JSON.stringify({ app: 'tx-trading-system', status: 'ok' })); return; }
      if (url.pathname === '/api/strategies') {
        try { res.end(JSON.stringify(await loadMongoStrategies())); }
        catch (error) { console.error(error.message); res.writeHead(500); res.end(JSON.stringify({ error: '無法讀取 MongoDB 策略資料。' })); }
        return;
      }
      if (url.pathname === '/api/chart-signals') {
        res.end(JSON.stringify(await loadMongoChartSignals(url.searchParams.get('date'), url.searchParams.get('from') || undefined)));
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
