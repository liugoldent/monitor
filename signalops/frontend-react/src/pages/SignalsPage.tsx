import { useMemo, useState } from 'react'
import { SignalCard } from '../components/SignalCard'
import { useSignals } from '../hooks/useSignals'
import type { SignalFilter } from '../types'

const filters: { value: SignalFilter; label: string }[] = [
  { value: 'all', label: '全部事件' },
  { value: 'enter', label: '進場' },
  { value: 'exit', label: '出場' },
  { value: 'reverse', label: '反轉' },
]

export function SignalsPage() {
  const [filter, setFilter] = useState<SignalFilter>('all')
  const { status, liveStatus, loadMore, reload } = useSignals(filter)
  const summary = useMemo(() => {
    const strategies = new Set(status.items.map((event) => event.strategy_code)).size
    const longBias = status.items.filter((event) => event.new_position === 1).length
    const shortBias = status.items.filter((event) => event.new_position === -1).length
    return { strategies, longBias, shortBias }
  }, [status.items])

  return (
    <div className="signal-page">
      <header className="signal-page-header">
        <div>
          <div className="eyebrow-row">
            <span>策略事件維運</span>
            <span className="read-only-badge">唯讀</span>
          </div>
          <h1>策略事件時間軸</h1>
          <p>所有持倉轉換都能稽核，資料跨越交易邊界前會先完成匿名化。</p>
        </div>

        <div>
          <div className={`live-indicator ${liveStatus}`}>
            <i />
            {liveStatus === 'connected' ? '即時串流已連線' : '正在重新連線'}
          </div>
          <div className="signal-filter" aria-label="篩選策略事件">
          {filters.map((item) => (
            <button
              className={filter === item.value ? 'active' : ''}
              key={item.value}
              onClick={() => setFilter(item.value)}
              type="button"
            >
              {item.label}
            </button>
          ))}
          </div>
        </div>
      </header>

      <section className="signal-summary-grid" aria-label="已載入事件摘要">
        <article>
          <span>已載入事件</span>
          <strong>{status.items.length}</strong>
          <small>使用 cursor 分頁</small>
        </article>
        <article>
          <span>策略數</span>
          <strong>{summary.strategies}</strong>
          <small>目前頁面的不重複策略</small>
        </article>
        <article>
          <span>轉為多方</span>
          <strong className="positive">{summary.longBias}</strong>
          <small>事件結束時持倉為 +1</small>
        </article>
        <article>
          <span>轉為空方</span>
          <strong className="negative">{summary.shortBias}</strong>
          <small>事件結束時持倉為 -1</small>
        </article>
      </section>

      {status.state === 'loading' && (
        <div className="state-panel">
          <strong>正在載入事件串流</strong>
          <span>正在取得匿名化的 SignalEvent v1 資料……</span>
        </div>
      )}

      {status.state === 'error' && (
        <div className="state-panel error inline-state">
          <strong>SignalOps API 暫時無法使用</strong>
          <span>{status.message}</span>
          <button className="primary-button" onClick={reload} type="button">
            重新嘗試
          </button>
        </div>
      )}

      {status.items.length > 0 && (
        <section className="signal-card-grid" aria-label="策略事件">
          {status.items.map((event) => (
            <SignalCard event={event} key={event.id} />
          ))}
        </section>
      )}

      {status.state === 'ready' && status.nextCursor && (
        <button className="load-more-button" disabled={status.loadingMore} onClick={loadMore} type="button">
          {status.loadingMore ? '載入中……' : '載入較早事件'}
        </button>
      )}

      {status.state === 'ready' && status.items.length === 0 && (
        <div className="state-panel inline-state">
          <strong>目前還沒有事件</strong>
          <span>請先匯入匿名化 CSV 批次，時間軸才會顯示資料。</span>
        </div>
      )}
    </div>
  )
}
