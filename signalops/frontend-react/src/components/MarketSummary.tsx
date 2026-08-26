import type { CandleStats } from '../types'
import { formatFullTime, formatPercent, formatPrice } from '../utils/market'

type MarketSummaryProps = {
  totalRows: number
  visibleRows: number
  stats: CandleStats
}

export function MarketSummary({ totalRows, visibleRows, stats }: MarketSummaryProps) {
  const directionClass = stats.change >= 0 ? 'positive' : 'negative'

  return (
    <section className="summary-grid" aria-label="市場摘要">
      <article>
        <span>最新收盤</span>
        <strong>{formatPrice(stats.latest.close)}</strong>
        <small>{formatFullTime(stats.latest.timestamp)}</small>
      </article>
      <article>
        <span>可見區間漲跌</span>
        <strong className={directionClass}>
          {stats.change >= 0 ? '+' : ''}
          {formatPrice(stats.change)}
        </strong>
        <small className={directionClass}>
          {stats.changePercent >= 0 ? '+' : ''}
          {formatPercent(stats.changePercent)}%
        </small>
      </article>
      <article>
        <span>最高／最低</span>
        <strong>
          {formatPrice(stats.high)} / {formatPrice(stats.low)}
        </strong>
        <small>區間 {formatPrice(stats.high - stats.low)}</small>
      </article>
      <article>
        <span>資料列</span>
        <strong>
          {visibleRows} / {totalRows}
        </strong>
        <small>平均振幅 {formatPrice(stats.averageRange)}</small>
      </article>
    </section>
  )
}
