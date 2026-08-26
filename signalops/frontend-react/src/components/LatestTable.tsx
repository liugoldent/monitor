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
        <h2>最新 K 線</h2>
        <span>{latestRows.length} 列</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>時間</th>
              <th>開盤</th>
              <th>最高</th>
              <th>最低</th>
              <th>收盤</th>
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
