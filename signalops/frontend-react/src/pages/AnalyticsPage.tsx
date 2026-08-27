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
            <span>ESBI CASHFLOW QUADRANT</span>
            <span className="read-only-badge">B → I</span>
          </div>
          <h1>從系統擁有者到投資者</h1>
          <p>
            先讓程式交易脫離必須本人維護的 S 象限，成為能被監控、委派與持續運作的 B
            象限系統；等成交與資金資料可信後，再把策略視為 I 象限的可配置資產。
          </p>
        </div>
        <div className="quadrant-story" aria-label="ESBI 發展方向">
          <span>S｜本人操作</span>
          <b>→</b>
          <strong>B｜系統擁有</strong>
          <b>→</b>
          <strong>I｜資本配置</strong>
        </div>
      </header>

      <section className="quadrant-section-heading owner-heading">
        <span className="quadrant-letter" aria-hidden="true">B</span>
        <div>
          <small>BUSINESS OWNER · 大型企業／系統擁有者</small>
          <h2>讓策略在沒有本人盯盤時仍能運作</h2>
          <p>這一區觀察的是系統留下多少可追溯事實、目前處理多少策略，以及營運行為是否異常。</p>
        </div>
      </section>

      <section className="signal-summary-grid" aria-label="B 象限系統營運指標">
        <article>
          <span>目前有部位策略</span>
          <strong>{kpis.active_strategies}</strong>
          <small>由事件歷史推導，不依賴人工整理</small>
        </article>
        <article>
          <span>策略方向曝險</span>
          <strong>{percent(kpis.exposure_rate)}</strong>
          <small>有多空方向的策略比例，非資金曝險</small>
        </article>
        <article>
          <span>換向事件占比</span>
          <strong>{percent(kpis.reversal_rate)}</strong>
          <small>用來觀察營運行為是否異常頻繁</small>
        </article>
        <article>
          <span>平均事件紀錄</span>
          <strong>{kpis.average_events_per_strategy.toFixed(1)}</strong>
          <small>每個策略留下的可稽核事實</small>
        </article>
      </section>

      <section className="analytics-grid">
        <article className="table-panel activity-panel">
          <div className="panel-heading">
            <div>
              <h2>每月事件趨勢</h2>
              <span>系統最近 {status.data.periods} 個有資料月份的工作量</span>
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

        <article className="table-panel owner-readiness-panel">
          <div className="panel-heading">
            <div>
              <h2>B 象限系統資產</h2>
              <span>把個人能力封裝成可被接手的系統</span>
            </div>
          </div>
          <ol className="readiness-list">
            <li>
              <span>01</span>
              <div>
                <strong>事件就是營運帳本</strong>
                <small>{dataQuality.total_events} 筆不可變事件可供稽核與重播。</small>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <strong>故障不等於遺失</strong>
                <small>Outbox、broker 與冪等 worker 讓處理流程可恢復。</small>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <strong>工作可以被交接</strong>
                <small>Runbook、監控與事件演練降低對單一操作者的依賴。</small>
              </div>
            </li>
          </ol>
        </article>
      </section>

      <section className="table-panel">
        <div className="panel-heading">
          <div>
            <h2>持倉轉換矩陣</h2>
            <span>B 象限的流程觀測：辨識進場、出場與換向型態</span>
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
      </section>

      <section className="quadrant-section-heading investor-heading">
        <span className="quadrant-letter" aria-hidden="true">I</span>
        <div>
          <small>INVESTOR · 投資者</small>
          <h2>判斷哪些策略值得配置資本</h2>
          <p>事件很多不代表值得投資；必須先有可靠成交、成本、資金曲線與風險資料，才能比較資產品質。</p>
        </div>
      </section>

      <section className="analytics-grid investor-grid">
        <article className="table-panel quality-panel">
          <div className="panel-heading">
            <div>
              <h2>I 象限資料門檻</h2>
              <span>目前是否足以做資本配置判斷</span>
            </div>
          </div>
          <dl>
            <div>
              <dt>可稽核事件</dt>
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
            <strong>I 象限尚未開放資本配置判斷</strong>
            <span>價格覆蓋率未達門檻，現在不能可靠計算勝率、回撤或成本後報酬。</span>
          </div>
        </article>

        <article className="table-panel investor-requirements">
          <div className="panel-heading">
            <div>
              <h2>成為可投資資產前</h2>
              <span>仍需補齊三組事實資料</span>
            </div>
          </div>
          <ol className="readiness-list locked-list">
            <li>
              <span>01</span>
              <div>
                <strong>成交與交易成本</strong>
                <small>成交價、數量、手續費與滑價。</small>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <strong>策略資金曲線</strong>
                <small>帳戶淨值、已實現與未實現損益快照。</small>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <strong>組合風險</strong>
                <small>最大回撤、相關性、風險預算與壓力測試。</small>
              </div>
            </li>
          </ol>
          <ul className="limitation-list">
            {limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </article>
      </section>
    </div>
  )
}
