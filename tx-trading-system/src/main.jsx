import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, NavLink, Route, Routes, Navigate, useLocation } from 'react-router-dom';
import { Activity, ArrowDownRight, ArrowUpRight, CalendarDays, ChartCandlestick, ChevronLeft, ChevronRight, Database, Expand, Layers, RefreshCw, Radio, Crosshair, Moon, Sun, Monitor, Eye, EyeOff } from 'lucide-react';
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, ColorType } from 'lightweight-charts';
import './styles.css';
import StrategyBoard from './StrategyBoard.jsx';
import { isAtLatest, reconcileRange } from './chart-range.mjs';

const priceLines = [ ['MA_P200', 'MA +200', 'var(--ma-upper)'], ['MA_P80', 'MA +80', 'var(--ma-warm)'], ['MA_960', 'MA 960', 'var(--ma-center)'], ['MA_N110', 'MA −110', 'var(--ma-purple)'], ['MA_N200', 'MA −200', 'var(--ma-lower)'] ];
const ma960Bands = [ ['MA960_P179', 'MA960 +179', '#ffffff', 179], ['MA960_P449', 'MA960 +449', '#ffffff', 449], ['MA960_N270', 'MA960 −270', '#ffffff', -270], ['MA960_N661', 'MA960 −661', '#ffffff', -661] ];
const chartPriceLines = [...priceLines, ...ma960Bands];
const priceLineValue = (candle, key, offset) => offset == null ? candle?.[key] : candle?.MA_960 == null ? null : candle.MA_960 + offset;
const chipLines = [ ['tx_bvav', '坦克', 'var(--up)'], ['mtx_bvav', '游擊', 'var(--chip-blue)'], ['mtx_tbta', '炮灰', 'var(--ma-center)'], ['mtx_bvav_avg', '游擊平均', 'var(--ma-warm)'] ];
const cssColor = value => value.startsWith('var(') ? getComputedStyle(document.documentElement).getPropertyValue(value.slice(4, -1)).trim() : value;
const themeColor = name => cssColor(`var(--${name})`);
const fmt = (value, decimals = 0) => value == null ? '—' : value.toLocaleString('en-US', { maximumFractionDigits: decimals });
const timeFormat = (time, full = false) => new Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei', ...(full ? { month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(Number(time) * 1000));

function Chart({ data, viewKey, visible, scope, chartRef, setHover, setFollowing, appearance }) {
  const container = useRef(null);
  const refs = useRef(null);
  const dateRef = useRef(null);
  const previousRows = useRef([]);
  const updating = useRef(false);
  const hoverCallback = useRef(setHover);
  hoverCallback.current = setHover;
  useEffect(() => {
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: themeColor('surface') }, textColor: themeColor('secondary'), fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Microsoft JhengHei, sans-serif', fontSize: 12, panes: { separatorColor: themeColor('separator'), separatorHoverColor: themeColor('accent'), enableResize: true } },
      grid: { vertLines: { visible: false }, horzLines: { color: themeColor('grid'), style: 0 } },
      rightPriceScale: { borderColor: themeColor('separator'), minimumWidth: 85 },
      timeScale: { borderColor: themeColor('separator'), timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 7, tickMarkFormatter: time => timeFormat(time) },
      localization: { locale: 'zh-TW', timeFormatter: time => timeFormat(time, true) },
      crosshair: { mode: 0, vertLine: { color: '#748290', style: 2 }, horzLine: { color: '#748290', style: 2 } },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: themeColor('up'), downColor: themeColor('down'), borderUpColor: themeColor('up'), borderDownColor: themeColor('down'), wickUpColor: themeColor('up'), wickDownColor: themeColor('down'), priceFormat: { type: 'price', precision: 0, minMove: 1 } });
    const ma = Object.fromEntries(chartPriceLines.map(([key, title, color]) => [key, chart.addSeries(LineSeries, { color: cssColor(color), title: '', lineWidth: key === 'MA_960' ? 2 : 1, priceLineVisible: false, crosshairMarkerVisible: false, priceFormat: { type: 'price', precision: 0, minMove: 1 } })]));
    const chips = Object.fromEntries(chipLines.map(([key, title, color], i) => [key, chart.addSeries(i === 0 ? HistogramSeries : LineSeries, { color: cssColor(color), ...(i === 0 ? {} : { lineWidth: key === 'mtx_bvav_avg' ? 2 : 1 }), priceLineVisible: false, priceFormat: { type: 'price', precision: 0, minMove: 1 } }, 1)]));
    chart.panes()[0].setStretchFactor(2.5);
    chart.panes()[1].setStretchFactor(1);
    chart.priceScale('right', 0).applyOptions({ scaleMargins: { top: 0.14, bottom: 0.08 } });
    chart.priceScale('right', 1).applyOptions({ scaleMargins: { top: 0.19, bottom: 0.12 } });
    const index = new Map();
    chart.subscribeCrosshairMove(param => hoverCallback.current(param.time ? index.get(param.time) ?? null : null));
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!updating.current) setFollowing(isAtLatest(range, previousRows.current.length));
    });
    refs.current = { chart, candles, ma, chips, index };
    chartRef.current = chart;
    return () => { chartRef.current = null; refs.current = null; chart.remove(); };
  }, []);
  useEffect(() => {
    if (!refs.current) return;
    const { chart, candles, ma, chips } = refs.current;
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: themeColor('surface') }, textColor: themeColor('secondary'), panes: { separatorColor: themeColor('separator'), separatorHoverColor: themeColor('accent') } },
      grid: { horzLines: { color: themeColor('grid') } },
      rightPriceScale: { borderColor: themeColor('separator') },
      timeScale: { borderColor: themeColor('separator') },
    });
    candles.applyOptions({ upColor: themeColor('up'), downColor: themeColor('down'), borderUpColor: themeColor('up'), borderDownColor: themeColor('down'), wickUpColor: themeColor('up'), wickDownColor: themeColor('down') });
    for (const [key, , color] of chartPriceLines) ma[key].applyOptions({ color: cssColor(color) });
    for (const [key, , color] of chipLines) chips[key].applyOptions({ color: cssColor(color) });
  }, [appearance]);
  useEffect(() => {
    if (!refs.current || !data) return;
    const { chart, candles, ma, chips, index } = refs.current;
    const prior = chart.timeScale().getVisibleLogicalRange();
    const range = reconcileRange(previousRows.current, data.rows, prior, dateRef.current !== viewKey);
    updating.current = true;
    index.clear(); data.rows.forEach(row => index.set(row.time, row));
    candles.setData(data.rows.map(row => row.candle ? { time: row.time, open: row.candle.open, high: row.candle.high, low: row.candle.low, close: row.candle.close } : { time: row.time }));
    chartPriceLines.forEach(([key, , , offset]) => ma[key].setData(data.rows.map(row => {
      const value = priceLineValue(row.candle, key, offset);
      return value == null ? { time: row.time } : { time: row.time, value };
    })));
    Object.entries(chips).forEach(([key, series]) => series.setData(data.rows.map(row => row.chip?.[key] == null ? { time: row.time } : { time: row.time, value: row.chip[key], ...(key === 'tx_bvav' ? { color: themeColor(row.chip[key] >= 0 ? 'hist-up' : 'hist-down') } : {}) })));
    dateRef.current = viewKey;
    previousRows.current = data.rows;
    if (range) chart.timeScale().setVisibleLogicalRange(range);
    updating.current = false;
    setFollowing(isAtLatest(range, data.rows.length));
  }, [data, viewKey, appearance]);
  useEffect(() => {
    if (!refs.current) return;
    for (const [key, series] of Object.entries({ ...refs.current.ma, ...refs.current.chips })) series.applyOptions({ visible: visible[key] });
  }, [visible]);
  useEffect(() => {
    if (!data || !refs.current || !scope) return;
    const total = data.rows.length;
    if (!total) return;
    const scale = refs.current.chart.timeScale();
    const current = scale.getVisibleLogicalRange();
    const firstToday = data.rows.findIndex(row => row.localTime.startsWith(data.date));
    const from = scope.value === 'all' ? Math.max(0, firstToday)
      : scope.value === 'latest' ? total + 8 - (current ? current.to - current.from : 188)
      : Math.max(0, total - Number(scope.value));
    scale.setVisibleLogicalRange({ from, to: total + 8 });
  }, [scope]);
  return <div ref={container} className="chart-canvas" aria-label="上方一分鐘 K 線與均線，下方台指籌碼，共用時間軸" />;
}

function App() {
  const [theme, setTheme] = useState(() => {
    try { const saved = localStorage.getItem('tx-theme'); return ['light', 'dark'].includes(saved) ? saved : 'system'; }
    catch { return 'system'; }
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  const appearance = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme;
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = appearance;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', appearance === 'dark' ? '#161617' : '#f5f5f7');
  }, [appearance]);
  useEffect(() => {
    try { localStorage.setItem('tx-theme', theme); } catch { /* Theme still works when storage is unavailable. */ }
  }, [theme]);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const change = () => setSystemDark(media.matches);
    media.addEventListener('change', change);
    change();
    return () => media.removeEventListener('change', change);
  }, []);
  const ThemeIcon = theme === 'system' ? Monitor : appearance === 'dark' ? Moon : Sun;
  const isStrategyPage = useLocation().pathname === '/strategies';
  const [data, setData] = useState(null);
  const [date, setDate] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [visible, setVisible] = useState(Object.fromEntries([...chartPriceLines, ...chipLines].map(([key]) => [key, true])));
  const [hover, setHover] = useState(null);
  const [scope, setScope] = useState(null);
  const [following, setFollowing] = useState(true);
  const chartRef = useRef(null);
  const board = useRef(null);
  const requestId = useRef(0);
  const reload = useCallback(async (signal) => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const response = await fetch(`/api/market?history=1${date ? `&date=${date}` : ''}`, { signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      if (id === requestId.current) { setData(result); setError(''); setHover(null); }
    } catch (e) { if (e.name !== 'AbortError' && id === requestId.current) setError(e.message); }
    finally { if (id === requestId.current) setLoading(false); }
  }, [date]);
  useEffect(() => { if (isStrategyPage) return; const controller = new AbortController(); reload(controller.signal); return () => controller.abort(); }, [reload, isStrategyPage]);
  useEffect(() => { if (!auto || isStrategyPage) return; const timer = setInterval(() => reload(), 30000); return () => clearInterval(timer); }, [auto, reload, isStrategyPage]);
  const currentData = data && (!date || date === data.date) ? data : null;
  const chartData = useMemo(() => currentData ? { ...currentData, rows: currentData.historyRows ?? currentData.rows } : null, [currentData]);
  const candles = currentData?.rows.filter(r => r.candle) ?? [];
  const last = candles.at(-1)?.candle;
  const previous = candles.at(-2)?.candle;
  const change = last && previous ? last.close - previous.close : null;
  const focus = hover ?? currentData?.rows.at(-1);
  const focusCandle = hover ? hover.candle : last;
  const toggle = key => setVisible(v => ({ ...v, [key]: !v[key] }));
  function moveDate(offset) { const index = data?.dates.indexOf(data.date); const next = data?.dates[index + offset]; if (next) setDate(next === data.dates[0] ? '' : next); }
  return <div className="app-shell">
    <header className="topbar">
      <NavLink to="/chart" className="brand"><span className="brand-icon"><ChartCandlestick size={21}/></span><span>TX<span className="brand-light">市場</span></span></NavLink>
      <nav><NavLink to="/chart"><ChartCandlestick size={17}/>行情圖表</NavLink><NavLink to="/data"><Database size={16}/>原始資料</NavLink><NavLink to="/strategies"><Layers size={16}/>策略看板</NavLink></nav>
      <div className="topbar-tools"><label className="theme-picker"><ThemeIcon size={16} aria-hidden="true"/><select aria-label="外觀模式" value={theme} onChange={event => setTheme(event.target.value)}><option value="light">淺色模式</option><option value="dark">黑夜模式</option><option value="system">跟隨系統</option></select></label><div className="source-badge"><span className="status-dot"/>本機資料</div></div>
    </header>
    <main>
      {!isStrategyPage && <>
      <div className="page-heading"><div><h1>市場總覽</h1><p>小型台指期貨 · 價格與籌碼</p></div><div className="refresh-area"><span>{data ? `讀取於 ${new Date(data.refreshedAt).toLocaleTimeString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false })}` : '讀取 CSV 中…'}</span><button className={`icon-button ${loading ? 'spinning' : ''}`} title="重新讀取 CSV" disabled={loading} onClick={() => reload()}><RefreshCw size={16}/></button></div></div>
      {error && <div className="error" role="alert">{error}<button onClick={() => reload()}>重試</button></div>}
      <div className="workspace-toolbar"><div className="instrument"><span className="instrument-square">TX</span><div><strong>小型台指期貨</strong><span>MXF1! · 臺灣期貨交易所</span></div><span className="timeframe">1 分 K</span></div><div className="date-tools"><CalendarDays size={16}/><button className="icon-button" title="前一天（有 K 線資料）" disabled={!data || data.dates.indexOf(data.date) === data.dates.length - 1} onClick={() => moveDate(1)}><ChevronLeft size={16}/></button><select aria-label="資料日期" value={data?.date ?? ''} onChange={e => setDate(e.target.value)}>{data?.dates.map(d => <option key={d}>{d}</option>)}</select><button className="icon-button" title="後一天（有 K 線資料）" disabled={!data || data.dates.indexOf(data.date) === 0} onClick={() => moveDate(-1)}><ChevronRight size={16}/></button><span className="timezone">UTC+8</span></div></div>
      </>}
      <Routes><Route path="/chart" element={<>
        <section className="market-summary"><div className="price-block"><span className="label">最新收盤價</span><div className={change >= 0 ? 'up' : 'down'}><strong>{fmt(last?.close)}</strong><span>{change == null ? '—' : change >= 0 ? <ArrowUpRight size={17}/> : <ArrowDownRight size={17}/>} {change == null ? '—' : `${change > 0 ? '+' : ''}${fmt(change)} (${(change / previous.close * 100).toFixed(2)}%)`}</span></div><small>相較前一根 K 棒 · {last ? timeFormat(last.time) : '—'}</small></div><div className="summary-stat"><span>當日最高</span><strong>{candles.length ? fmt(Math.max(...candles.map(r => r.candle.high))) : '—'}</strong></div><div className="summary-stat"><span>當日最低</span><strong>{candles.length ? fmt(Math.min(...candles.map(r => r.candle.low))) : '—'}</strong></div><div className="summary-stat"><span>分鐘對齊</span><strong>{fmt(currentData?.stats.matched)}<small> / {fmt(currentData?.rows.length)}</small></strong></div><div className="summary-stat last-stat"><span>資料狀態</span><strong className="status-text"><span className="status-dot"/>{currentData?.rows.length ? 'CSV 已載入' : '等待資料'}</strong><small>非交易所即時報價</small></div></section>
        <section className="chart-board" ref={board}>
          <div className="chart-toolbar"><div className="chart-title"><ChartCandlestick size={16}/><strong>行情圖表</strong><span>1m</span></div><div className="chart-actions"><button className={following && !date ? 'active follow-button' : 'follow-button'} title="回到最新資料並跟隨右端，保留目前縮放比例" onClick={() => { setDate(''); setScope({ value: 'latest' }); }}><ChevronRight size={14}/>{following && !date ? '跟隨最新' : '回到最新'}</button><button className={auto ? 'active auto-button' : 'auto-button'} onClick={() => setAuto(v => !v)}><Radio size={14}/>{auto ? '30 秒自動讀取' : '自動讀取已暫停'}</button><span className="separator"/><button title="顯示全部當日資料" onClick={() => setScope({ value: 'all' })}><Expand size={15}/><span>全覽</span></button><button title="全螢幕" onClick={async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await board.current.requestFullscreen(); } catch { setError('目前瀏覽器不支援全螢幕，請使用圖表全覽。'); } }}><Expand size={15}/></button></div></div>
          <div className="price-legend"><div className="ohlc"><strong>小台指 · 1 · TX</strong><span>{hover ? focus?.localTime : last?.localTime}</span>{[['開', 'open'], ['高', 'high'], ['低', 'low'], ['收', 'close']].map(([name, key]) => <span key={key}>{name} <b className={focusCandle?.close >= focusCandle?.open ? 'up' : 'down'}>{fmt(focusCandle?.[key])}</b></span>)}</div><div className="legend-toggles"><Layers size={13}/>{priceLines.map(([key, name, color]) => <button key={key} className={!visible[key] ? 'muted-toggle' : ''} aria-pressed={visible[key]} onClick={() => toggle(key)}><i style={{ background: color }}/>{name}<b style={{ color }}>{fmt(focusCandle?.[key])}</b></button>)}</div><div className="legend-toggles ma960-bands">{ma960Bands.map(([key, name, color, offset]) => <button key={key} className={!visible[key] ? 'muted-toggle' : ''} title={`${visible[key] ? '隱藏' : '顯示'}${name}`} aria-pressed={visible[key]} onClick={() => toggle(key)}><i style={{ background: color }}/>{name}<b>{fmt(priceLineValue(focusCandle, key, offset))}</b></button>)}</div></div>
          <div className="chart-region"><Chart data={chartData} viewKey={date || 'latest'} visible={visible} scope={scope} chartRef={chartRef} setHover={setHover} setFollowing={setFollowing} appearance={appearance}/>{(!currentData || !currentData.rows.length) && <div className="chart-empty">{loading ? '正在讀取與對齊 CSV 資料…' : '這個日期尚無資料'}</div>}</div>
          <div className="chip-legend"><div><Activity size={15}/><strong>台指籌碼</strong><span>點擊指標可顯示／隱藏</span></div><div className="legend-toggles">{chipLines.map(([key, name, color]) => <button key={key} className={!visible[key] ? 'muted-toggle' : ''} aria-label={`${name}顯示開關`} title={`${visible[key] ? '隱藏' : '顯示'}${name}`} aria-pressed={visible[key]} onClick={() => toggle(key)}>{visible[key] ? <Eye size={13} aria-hidden="true"/> : <EyeOff size={13} aria-hidden="true"/>}<i style={{ background: color }}/>{name}<b style={{ color }}>{fmt(focus?.chip?.[key])}</b></button>)}</div></div>
          <div className="chart-footer"><div className="range-buttons">{[['120', '近 120 筆'], ['240', '近 240 筆'], ['all', '當日全部']].map(([value, label]) => <button className={scope?.value === value ? 'selected' : ''} key={value} onClick={() => setScope({ value })}>{label}</button>)}</div><span><Crosshair size={12}/>拖曳平移 · 滾輪縮放 · 上下圖共用時間軸</span></div>
        </section>
      </>}/><Route path="/strategies" element={<StrategyBoard/>}/><Route path="/data" element={<DataTable data={currentData} loading={loading}/>}/><Route path="*" element={<Navigate to="/chart" replace/>}/></Routes>
      <footer className="page-footer"><span><span className="status-dot"/>台北時間 UTC+8</span><span>{isStrategyPage ? "依最後一筆策略訊號更新" : "每 30 秒讀取 · 缺 1～2 分鐘沿用前值"}</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a></footer>
    </main>
  </div>;
}

function DataTable({ data, loading }) {
  const [page, setPage] = useState(0);
  const [onlyMatched, setOnlyMatched] = useState(false);
  useEffect(() => setPage(0), [data?.date, onlyMatched]);
  const rows = (data?.rows ?? []).filter(row => !onlyMatched || (row.candle && row.chip)).slice().reverse();
  const pages = Math.max(1, Math.ceil(rows.length / 50));
  const actualPage = Math.min(page, pages - 1);
  return <section className="data-panel"><div className="data-heading"><div><h2>分鐘資料核對</h2><p>依時間由新到舊 · 缺 1～2 分鐘沿用前值，補值列會標記</p></div><label><input type="checkbox" checked={onlyMatched} onChange={e => setOnlyMatched(e.target.checked)}/>僅顯示成功對齊</label></div><div className="table-scroll"><table><thead><tr>{['台北時間', '開', '高', '低', '收', '坦克', '游擊', '炮灰', '游擊平均', 'Signal', 'Trend', '對齊狀態'].map(title => <th key={title}>{title}</th>)}</tr></thead><tbody>{rows.slice(actualPage * 50, (actualPage + 1) * 50).map(row => <tr key={row.time}><td>{row.localTime}</td>{['open', 'high', 'low', 'close'].map(key => <td key={key}>{fmt(row.candle?.[key])}</td>)}{chipLines.map(([key]) => <td key={key}>{fmt(row.chip?.[key])}</td>)}<td>{row.chip?.signal ?? '—'}</td><td>{row.chip?.trend ?? '—'}</td><td><span className={row.candle && row.chip ? 'table-tag matched' : 'table-tag'}>{row.candle && row.chip ? (row.candle.imputed || row.chip.imputed ? '沿用前值' : '已對齊') : row.candle ? '缺籌碼' : '缺 K 線'}</span></td></tr>)}</tbody></table>{!rows.length && <div className="table-empty">{loading ? '讀取資料中…' : '無符合條件的資料'}</div>}</div><div className="pagination"><span>共 {fmt(rows.length)} 筆 · 每頁 50 筆</span><div><button aria-label="上一頁" disabled={actualPage === 0} onClick={() => setPage(actualPage - 1)}><ChevronLeft size={16}/></button><span>{actualPage + 1} / {pages}</span><button aria-label="下一頁" disabled={actualPage + 1 >= pages} onClick={() => setPage(actualPage + 1)}><ChevronRight size={16}/></button></div></div><div className="source-details">{data && Object.values(data.sources).map(source => <p key={source.file}><strong>{source.file}</strong>：共 {fmt(source.count)} 筆有效資料 · {source.duplicates} 筆重複時間（取最後一筆）· {source.invalid} 筆無效資料略過</p>)}</div></section>;
}

createRoot(document.getElementById('root')).render(<BrowserRouter><App/></BrowserRouter>);
