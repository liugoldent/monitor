import { useAnalytics } from '../hooks/useAnalytics'
import type { BusinessAnalytics } from '../types'

const positionLabels = new Map([
  [-1, '空單'],
  [0, '空手'],
  [1, '多單'],
])

function percent(value: number) {
  return new Intl.NumberFormat('zh-TW', { style: 'percent', maximumFractionDigits: 1 }).format(value)
}

function transitionCount(
  transitions: BusinessAnalytics['transitions'],
  previous: number,
  current: number,
) {
  return (
    transitions.find(
      (transition) =>
        transition.previous_position === previous && transition.new_position === current,
    )?.count ?? 0
  )
}

export function AnalyticsPage() {
  const { status, reload } = useAnalytics()

  if (status.state === 'loading') {
    return (
      <div className="state-panel">
        <strong>正在建立營運分析</strong>
        <span>聚合事件趨勢、持倉曝險與資料品質……</span>
      </div>
    )
  }

  if (status.state === 'error') {
    return (
      <div className="state-panel error">
        <strong>無法載入營運分析</strong>
        <span>{status.message}</span>
        <button className="primary-button" onClick={reload} type="button">
          重新嘗試
        </button>
      </div>
    )
  }

  const { activity, data_quality: dataQuality, kpis, limitations, transitions } = status.data
  const maxActivity = Math.max(...activity.map((item) => item.total), 1)

  return (
    <div className="analytics-page">
      <header className="signal-page-header analytics-header">
        <div>
          <div className="eyebrow-row">
            <span>ESBI × BUSINESS INTELLIGENCE</span>
            <span className="read-only-badge">決策支援</span>
          </div>
          <h1>策略營運分析</h1>
          <p>
            把依賴個人操作的程式交易，轉成可量測、可委派、可持續運作的系統。這裡先回答營運問題，不用不完整資料製造投資績效。
          </p>
        </div>
        <div className="quadrant-story" aria-label="ESBI 發展方向">
          <span>個人執行</span>
          <b>→</b>
          <strong>系統化營運</strong>
          <b>→</b>
          <span>可信投資決策</span>
        </div>
      </header>

      <section className="signal-summary-grid" aria-label="營運關鍵指標">
        <article>
          <span>活躍策略</span>
          <strong>{kpis.active_strategies}</strong>
          <small>目前持有方向部位</small>
        </article>
        <article>
          <span>策略曝險比例</span>
          <strong>{percent(kpis.exposure_rate)}</strong>
          <small>非資金曝險比例</small>
        </article>
        <article>
          <span>反轉事件占比</span>
          <strong>{percent(kpis.reversal_rate)}</strong>
          <small>觀察策略換向頻率</small>
        </article>
        <article>
          <span>每策略平均事件</span>
          <strong>{kpis.average_events_per_strategy.toFixed(1)}</strong>
          <small>比較系統使用強度</small>
        </article>
      </section>

      <section className="analytics-grid">
        <article className="table-panel activity-panel">
          <div className="panel-heading">
            <div>
              <h2>每月事件趨勢</h2>
              <span>最近 {status.data.periods} 個有資料的月份</span>
            </div>
          </div>
          <div className="activity-chart" aria-label="每月策略事件數">
            {activity.map((item) => (
              <div className="activity-column" key={item.period}>
                <span>{item.total}</span>
                <div className="activity-track">
                  <i style={{ height: `${Math.max((item.total / maxActivity) * 100, 3)}%` }} />
                </div>
                <small>{item.period.slice(2)}</small>
              </div>
            ))}
          </div>
        </article>

        <article className="table-panel quality-panel">
          <div className="panel-heading">
            <div>
              <h2>資料品質門檻</h2>
              <span>決定哪些 BI 指標現在可信</span>
            </div>
          </div>
          <dl>
            <div>
              <dt>事件完整筆數</dt>
              <dd>{dataQuality.total_events}</dd>
            </div>
            <div>
              <dt>成交／參考價覆蓋率</dt>
              <dd>{percent(dataQuality.reference_price_coverage)}</dd>
            </div>
            <div>
              <dt>缺少參考價</dt>
              <dd>{dataQuality.missing_reference_price}</dd>
            </div>
          </dl>
          <div className="quality-gate">
            <strong>目前不開放績效 BI</strong>
            <span>價格覆蓋率未達門檻，避免勝率與報酬率失真。</span>
          </div>
        </article>
      </section>

      <section className="table-panel">
        <div className="panel-heading">
          <div>
            <h2>持倉轉換矩陣</h2>
            <span>辨識進場、出場與換向的操作型態</span>
          </div>
        </div>
        <div className="table-wrap">
          <table className="transition-table">
            <thead>
              <tr>
                <th>前一狀態 ↓／新狀態 →</th>
                {[-1, 0, 1].map((position) => (
                  <th key={position}>{positionLabels.get(position)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[-1, 0, 1].map((previous) => (
                <tr key={previous}>
                  <td>{positionLabels.get(previous)}</td>
                  {[-1, 0, 1].map((current) => (
                    <td key={current}>{transitionCount(transitions, previous, current)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="limitation-list">
          {limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}
