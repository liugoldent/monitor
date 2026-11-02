import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadData } from './data.mjs';
import { loadStrategies } from './strategies.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const production = process.argv.includes('--production');
const vite = production ? null : await (await import('vite')).createServer({ root, server: { middlewareMode: true }, appType: 'spa' });
const port = Number(process.env.PORT || 5173);
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    try {
      if (url.pathname === '/api/health') { res.end(JSON.stringify({ app: 'tx-trading-system', status: 'ok' })); return; }
      if (url.pathname === '/api/strategies') {
        try { res.end(JSON.stringify(await loadStrategies())); }
        catch (error) { console.error(error.message); res.writeHead(500); res.end(JSON.stringify({ error: '無法讀取策略事件檔，請確認 six_strategy_signal_events.csv 存在且格式正確。' })); }
        return;
      }
      if (url.pathname !== '/api/market') { res.writeHead(404); res.end(JSON.stringify({ error: '找不到 API' })); return; }
      const data = await loadData();
      const date = url.searchParams.get('date') || data.dates[0];
      if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) { res.writeHead(400); res.end(JSON.stringify({ error: '日期格式錯誤' })); return; }
      const rows = data.rows.filter(row => row.localTime.startsWith(date));
      // Continuous multi-session history, bounded to keep polling lightweight.
      const historyRows = url.searchParams.get('history') === '1'
        ? data.rows.filter(row => row.localTime.slice(0, 10) <= date).slice(-10000)
        : undefined;
      const matched = rows.filter(row => row.candle && row.chip).length;
      res.end(JSON.stringify({ date, dates: data.dates, rows, historyRows, sources: data.sources, stats: { candles: rows.filter(r => r.candle).length, chips: rows.filter(r => r.chip).length, matched }, refreshedAt: new Date().toISOString() }));
    } catch (error) {
      console.error(error.message);
      res.writeHead(500); res.end(JSON.stringify({ error: '無法讀取 CSV，請確認 backend-futures-py/tv_doc 中的兩份資料存在且格式正確。' }));
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
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => { await vite?.close(); server.close(); });
