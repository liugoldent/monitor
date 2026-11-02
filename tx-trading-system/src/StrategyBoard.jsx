import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, ArrowRight, ChevronLeft, ChevronRight, History, Minus, RefreshCw, X } from 'lucide-react';
import './strategies.css';

const number = value => value.toLocaleString('en-US', { maximumFractionDigits: 2 });
const signed = value => `${value > 0 ? '+' : ''}${number(value)}`;
const state = { long: '多單', short: '空單', flat: '空手' };
const actionName = { enter: '進場', exit: '出場', reverse: '反手', adjust: '調整' };
function DirectionIcon({ direction, size = 18 }) {
  const Icon = direction === 'long' ? ArrowUpRight : direction === 'short' ? ArrowDownRight : Minus;
  return <Icon size={size} aria-hidden="true"/>;
}
function positionText(value) { return value === 0 ? '空手' : `${value > 0 ? '多' : '空'} ${number(Math.abs(value))}`; }

export default function StrategyBoard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [page, setPage] = useState(0);
  const dialog = useRef(null);
  const request = useRef(0);
  const reload = useCallback(async signal => {
    const id = ++request.current;
    setLoading(true);
    try {
      const response = await fetch('/api/strategies', { signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '策略資料讀取失敗');
      if (request.current === id) { setData(result); setError(''); }
    } catch (error) {
      if (error.name !== 'AbortError' && request.current === id) setError(error.message);
    } finally { if (request.current === id) setLoading(false); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    reload(controller.signal);
    const timer = setInterval(() => reload(controller.signal), 30000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [reload]);
  const strategy = data?.strategies.find(s => s.id === selected);
  useEffect(() => {
    if (selected && dialog.current && !dialog.current.open) {
      dialog.current.showModal();
      const previous = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = previous; };
    }
  }, [selected]);
  function close() { dialog.current?.close(); setSelected(null); }
  const summary = data?.summary;
  const cards = data?.strategies.filter(s => filter === 'all' || s.direction === filter) ?? [];
  const pages = Math.max(1, Math.ceil((strategy?.events.length || 0) / 25));
  const currentPage = Math.min(page, pages - 1);
  const netLabel = !summary ? '—' : summary.net > 0 ? '淨多單' : summary.net < 0 ? '淨空單' : summary.long + summary.short === 0 ? '全數空手' : '多空平衡';
  return <div className="strategy-page">
    <div className="page-heading"><div><h1>策略看板</h1><p>每個策略的方向，一眼掌握。</p></div><div className="refresh-area"><span>{data ? `讀取於 ${new Date(data.refreshedAt).toLocaleTimeString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false })}` : '讀取策略中…'}</span><button className={`icon-button ${loading ? 'spinning' : ''}`} title="重新讀取策略" disabled={loading} onClick={() => reload()}><RefreshCw size={16}/></button></div></div>
    {error && <div className="error" role="alert">{error}<button onClick={() => reload()}>重試</button></div>}
    <section className="portfolio-summary" aria-label="全部策略倉位加總">
      <div className="portfolio-net"><span className="portfolio-label">策略合計</span><div className={`position-${summary?.direction ?? 'flat'}`}><DirectionIcon direction={summary?.direction}/><strong>{netLabel}</strong><span className="net-value">{summary ? signed(summary.net) : '—'}</span></div><p>多單 {summary ? number(summary.longUnits) : '—'} − 空單 {summary ? number(summary.shortUnits) : '—'} = 淨倉位</p></div>
      <div className="portfolio-distribution"><div className="distribution-labels"><span><i className="long-dot"/>做多 <b>{summary?.long ?? '—'}</b></span><span><i className="short-dot"/>做空 <b>{summary?.short ?? '—'}</b></span><span><i className="flat-dot"/>空手 <b>{summary?.flat ?? '—'}</b></span></div><div className="distribution-bar" aria-hidden="true">{['long', 'short', 'flat'].map(key => <span key={key} className={`bar-${key}`} style={{ flex: summary?.[key] || 0 }}/>)}</div><p>{summary ? `${summary.total} 個策略 · ${number(summary.events)} 筆歷史事件` : '正在整理策略紀錄…'}</p></div>
    </section>
    <div className="strategy-list-toolbar"><h2>策略狀態 <span>{summary?.total ?? '—'}</span></h2><div className="strategy-filters" aria-label="策略方向篩選">{[['all', '全部'], ['long', '做多'], ['short', '做空'], ['flat', '空手']].map(([value, label]) => <button key={value} aria-pressed={filter === value} className={filter === value ? 'selected' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div></div>
    <div className="strategy-grid" aria-busy={!data && loading}>
      {cards.map(s => <button key={s.id} className={`strategy-card ${selected === s.id ? 'is-flipped' : ''}`} aria-haspopup="dialog" aria-label={`${s.name}，${positionText(s.position)}，查看歷史進出場紀錄`} onClick={() => { setPage(0); setSelected(s.id); }}>
        <span className="strategy-card-inner"><span className="strategy-card-front"><span className="strategy-card-top"><span className="strategy-code">{s.code}</span><span className={`position-pill position-${s.direction}`}><DirectionIcon direction={s.direction} size={13}/>{state[s.direction]}</span></span><span className="strategy-name">{s.name}</span><span className={`strategy-position position-${s.direction}`}><DirectionIcon direction={s.direction} size={28}/><span>{s.position === 0 ? '空手' : `${s.position > 0 ? '多' : '空'} ${number(Math.abs(s.position))}`}</span><small>{s.position === 0 ? '等待下一個訊號' : '目前訊號倉位'}</small></span><span className="strategy-update">最近{actionName[s.lastEvent.action]} · {s.lastEvent.time || '時間未記錄'}</span><span className="strategy-card-bottom"><span><History size={13}/>{number(s.events.length)} 筆紀錄</span><span>查看歷史<ChevronRight size={14}/></span></span></span>
        <span className="strategy-card-back" aria-hidden="true"><History size={26}/><span>{s.name}</span><strong>歷史進出場</strong><small>{number(s.events.length)} 筆紀錄</small></span></span>
      </button>)}
    </div>
    {!cards.length && <div className="strategy-empty">{loading && !data ? '正在讀取策略訊號…' : error && !data ? '尚未取得策略資料，請重新讀取。' : '目前沒有符合條件的策略。'}</div>}
    <p className="strategy-footnote">依各策略最後一筆倉位訊號顯示，非券商即時庫存。每 30 秒更新；點擊卡片可查看完整紀錄。{data?.invalid > 0 && ` 已略過 ${data.invalid} 筆不完整紀錄。`}</p>
    <dialog ref={dialog} className="strategy-dialog" aria-labelledby="strategy-dialog-title" onClose={() => setSelected(null)} onClick={e => { if (e.target === dialog.current) { const box = dialog.current.getBoundingClientRect(); if (e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom) close(); } }}>
      {strategy && <><div className="strategy-dialog-header"><div><span className="strategy-code">{strategy.code}</span><h2 id="strategy-dialog-title">{strategy.name}</h2><p>歷史進出場紀錄 · 最新寫入在前 · 台北時間</p></div><button autoFocus className="icon-button" aria-label="關閉歷史紀錄" onClick={close}><X size={20}/></button></div><div className="strategy-dialog-status"><span className={`position-pill position-${strategy.direction}`}><DirectionIcon direction={strategy.direction} size={14}/>目前{positionText(strategy.position)}</span><span>{number(strategy.events.length)} 筆事件</span></div><div className="strategy-history-scroll"><table className="strategy-history"><thead><tr><th>訊號時間</th><th>動作</th><th>倉位變化</th><th>原始訊號</th></tr></thead><tbody>{strategy.events.slice(currentPage * 25, (currentPage + 1) * 25).map(event => <tr key={event.id}><td>{event.time || <span className="unrecorded-time">時間未記錄</span>}</td><td><span className={`event-action position-${event.direction}`}>{actionName[event.action]}</span></td><td><span className="position-transition"><span>{positionText(event.previousPosition)}</span><ArrowRight size={13}/><strong>{positionText(event.position)}</strong></span></td><td><details><summary>查看訊號</summary><p>{event.signal || '未提供原始訊號'}</p>{event.receivedAt && <small>接收：{event.receivedAt}</small>}</details></td></tr>)}</tbody></table></div><div className="pagination"><span>每頁 25 筆</span><div><button aria-label="上一頁紀錄" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16}/></button><span>{currentPage + 1} / {pages}</span><button aria-label="下一頁紀錄" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16}/></button></div></div><p className="strategy-dialog-note">保留原始事件順序，早期缺少時間的紀錄不推測時間。目前狀態以最後一筆訊號為準。</p></>}
    </dialog>
  </div>;
}
