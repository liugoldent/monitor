import { useOverview } from '../hooks/useOverview'
import type { CurrentPosition } from '../types'

function positionText(position: CurrentPosition['position']) {
  if (position === 1) return '多單'
  if (position === -1) return '空單'
  return '空手'
}

function formatTime(value: string | null) {
  if (!value) return '尚無事件'
  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Taipei',
  }).format(new Date(value))
}

export function OverviewPage() {
  const { status, reload } = useOverview()

  if (status.state === 'loading') {
    return (
      <div className="state-panel">
        <strong>正在整理策略總覽</strong>
        <span>由完整事件歷史推導目前持倉與統計資料……</span>
      </div>
    )
  }

  if (status.state === 'error') {
    return (
      <div className="state-panel error">
        <strong>無法載入策略總覽</strong>
        <span>{status.message}</span>
        <button className="primary-button" onClick={reload} type="button">
          重新嘗試
        </button>
      </div>
    )
  }

  const { counts, positions, strategies, last_event_at: lastEventAt } = status.data
  return (
    <div className="overview-page">
      <header className="signal-page-header overview-header">
        <div>
          <div className="eyebrow-row">
            <span>策略維運中心</span>
            <span className="read-only-badge">唯讀</span>
          </div>
          <h1>策略總覽</h1>
          <p>目前持倉由事件歷史即時計算，不會連接券商憑證，也不能送出交易指令。</p>
        </div>
        <div className="last-event-block">
          <span>最後事件時間</span>
          <strong>{formatTime(lastEventAt)}</strong>
        </div>
      </header>

      <section className="signal-summary-grid" aria-label="策略總覽數字">
        <article>
          <span>全部事件</span>
          <strong>{counts.total_events}</strong>
          <small>{counts.strategies} 個策略</small>
        </article>
        <article>
          <span>進場／出場／反轉</span>
          <strong>
            {counts.entries} / {counts.exits} / {counts.reversals}
          </strong>
          <small>經過 domain transition 驗證</small>
        </article>
        <article>
          <span>目前多方策略</span>
          <strong className="positive">{counts.long_positions}</strong>
          <small>最新事件持倉為 +1</small>
        </article>
        <article>
          <span>目前空方／空手</span>
          <strong className="negative">
            {counts.short_positions} / {counts.flat_positions}
          </strong>
          <small>空方與沒有部位</small>
        </article>
      </section>

      <section className="table-panel">
        <div className="panel-heading">
          <div>
            <h2>目前持倉</h2>
            <span>每個策略只取時間最新的一筆事件</span>
          </div>
        </div>
        <div className="position-grid">
          {positions.map((position) => (
            <article className={`position-card ${position.position_label}`} key={position.strategy_code}>
              <div>
                <strong>{position.strategy_name}</strong>
                <small>{position.strategy_code}</small>
              </div>
              <b>{positionText(position.position)}</b>
              <span>{formatTime(position.updated_at)}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="table-panel">
        <div className="panel-heading">
          <div>
            <h2>策略事件統計</h2>
            <span>目前資料沒有可靠成交價，因此不虛構損益</span>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>策略</th>
                <th>事件數</th>
                <th>進場</th>
                <th>出場</th>
                <th>反轉</th>
                <th>目前持倉</th>
                <th>最後事件</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((strategy) => (
                <tr key={strategy.strategy_code}>
                  <td>
                    {strategy.strategy_name} <small>{strategy.strategy_code}</small>
                  </td>
                  <td>{strategy.event_count}</td>
                  <td>{strategy.entries}</td>
                  <td>{strategy.exits}</td>
                  <td>{strategy.reversals}</td>
                  <td>{positionText(strategy.current_position)}</td>
                  <td>{formatTime(strategy.last_event_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
