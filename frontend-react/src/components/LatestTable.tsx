import type { Candle } from '../types'
import { formatPrice, formatTime } from '../utils/market'

type LatestTableProps = {
  candles: Candle[]
}

export function LatestTable({ candles }: LatestTableProps) {
  const latestRows = candles.slice(-12).reverse()

  return (
    <section className="table-panel">
      <div className="panel-heading">
        <h2>Latest Candles</h2>
        <span>{latestRows.length} rows</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Open</th>
              <th>High</th>
              <th>Low</th>
              <th>Close</th>
              <th>BBR</th>
            </tr>
          </thead>
          <tbody>
            {latestRows.map((candle) => (
              <tr key={candle.id}>
                <td>{formatTime(candle.timestamp)}</td>
                <td>{formatPrice(candle.open)}</td>
                <td>{formatPrice(candle.high)}</td>
                <td>{formatPrice(candle.low)}</td>
                <td className={candle.close >= candle.open ? 'positive' : 'negative'}>{formatPrice(candle.close)}</td>
                <td>{candle.bbr === null ? '-' : candle.bbr.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
