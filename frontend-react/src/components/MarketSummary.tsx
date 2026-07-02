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
    <section className="summary-grid" aria-label="Market summary">
      <article>
        <span>Last Close</span>
        <strong>{formatPrice(stats.latest.close)}</strong>
        <small>{formatFullTime(stats.latest.timestamp)}</small>
      </article>
      <article>
        <span>Visible Change</span>
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
        <span>High / Low</span>
        <strong>
          {formatPrice(stats.high)} / {formatPrice(stats.low)}
        </strong>
        <small>Range {formatPrice(stats.high - stats.low)}</small>
      </article>
      <article>
        <span>Rows</span>
        <strong>
          {visibleRows} / {totalRows}
        </strong>
        <small>Avg range {formatPrice(stats.averageRange)}</small>
      </article>
    </section>
  )
}
