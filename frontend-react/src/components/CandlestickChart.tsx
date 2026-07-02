import { useMemo, useState } from 'react'
import type { Candle } from '../types'
import { formatPrice, formatTime } from '../utils/market'

type CandlestickChartProps = {
  candles: Candle[]
  showMa: boolean
  showTrend: boolean
}

const width = 1180
const height = 520
const padding = {
  top: 28,
  right: 72,
  bottom: 46,
  left: 28,
}

function yFor(value: number, min: number, max: number): number {
  const plotHeight = height - padding.top - padding.bottom
  if (max === min) return padding.top + plotHeight / 2
  return padding.top + ((max - value) / (max - min)) * plotHeight
}

function buildLinePath(candles: Candle[], min: number, max: number, valueOf: (candle: Candle) => number | null) {
  const plotWidth = width - padding.left - padding.right
  const step = plotWidth / candles.length
  let path = ''

  candles.forEach((candle, index) => {
    const value = valueOf(candle)
    if (value === null) return

    const x = padding.left + step * index + step / 2
    const y = yFor(value, min, max)
    path += path ? ` L ${x.toFixed(2)} ${y.toFixed(2)}` : `M ${x.toFixed(2)} ${y.toFixed(2)}`
  })

  return path
}

export function CandlestickChart({ candles, showMa, showTrend }: CandlestickChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const hoveredCandle = hoveredIndex === null ? candles[candles.length - 1] : candles[hoveredIndex]

  const chart = useMemo(() => {
    const values = candles.flatMap((candle) => [
      candle.high,
      candle.low,
      showMa ? candle.ma960 : null,
      showMa ? candle.maP80 : null,
      showTrend ? candle.ttShort : null,
      showTrend ? candle.ttLong : null,
    ])
    const numericValues = values.filter((value): value is number => value !== null && Number.isFinite(value))
    const min = Math.min(...numericValues)
    const max = Math.max(...numericValues)
    const rangePadding = Math.max((max - min) * 0.08, 24)
    const domainMin = min - rangePadding
    const domainMax = max + rangePadding
    const plotWidth = width - padding.left - padding.right
    const step = plotWidth / candles.length
    const candleWidth = Math.max(2, Math.min(step * 0.62, 12))

    const priceTicks = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4
      const value = domainMax - (domainMax - domainMin) * ratio
      return {
        value,
        y: padding.top + (height - padding.top - padding.bottom) * ratio,
      }
    })

    const timeTicks = Array.from({ length: 5 }, (_, index) => {
      const candleIndex = Math.min(candles.length - 1, Math.round((candles.length - 1) * (index / 4)))
      return {
        candle: candles[candleIndex],
        x: padding.left + step * candleIndex + step / 2,
      }
    })

    return {
      candleWidth,
      domainMax,
      domainMin,
      ma960Path: buildLinePath(candles, domainMin, domainMax, (candle) => candle.ma960),
      maP80Path: buildLinePath(candles, domainMin, domainMax, (candle) => candle.maP80),
      priceTicks,
      step,
      timeTicks,
      ttLongPath: buildLinePath(candles, domainMin, domainMax, (candle) => candle.ttLong),
      ttShortPath: buildLinePath(candles, domainMin, domainMax, (candle) => candle.ttShort),
    }
  }, [candles, showMa, showTrend])

  function handlePointerMove(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width) * width
    const plotWidth = width - padding.left - padding.right
    const index = Math.round(((x - padding.left) / plotWidth) * candles.length - 0.5)
    setHoveredIndex(Math.max(0, Math.min(candles.length - 1, index)))
  }

  return (
    <section className="chart-panel" aria-label="Candlestick chart">
      <div className="chart-toolbar">
        <div>
          <h1>MXF1! 1m K 線</h1>
          <p>
            {formatTime(candles[0].timestamp)} - {formatTime(candles[candles.length - 1].timestamp)}
          </p>
        </div>
        <div className="ohlc-strip">
          <span>O {formatPrice(hoveredCandle.open)}</span>
          <span>H {formatPrice(hoveredCandle.high)}</span>
          <span>L {formatPrice(hoveredCandle.low)}</span>
          <span>C {formatPrice(hoveredCandle.close)}</span>
        </div>
      </div>

      <svg
        className="candlestick-svg"
        onPointerLeave={() => setHoveredIndex(null)}
        onPointerMove={handlePointerMove}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect className="chart-background" height={height} width={width} x="0" y="0" />

        {chart.priceTicks.map((tick) => (
          <g key={tick.value}>
            <line className="grid-line" x1={padding.left} x2={width - padding.right} y1={tick.y} y2={tick.y} />
            <text className="axis-label price-label" x={width - padding.right + 14} y={tick.y + 4}>
              {formatPrice(tick.value)}
            </text>
          </g>
        ))}

        {chart.timeTicks.map((tick) => (
          <text className="axis-label" key={tick.candle.id} textAnchor="middle" x={tick.x} y={height - 16}>
            {formatTime(tick.candle.timestamp)}
          </text>
        ))}

        {showMa && (
          <>
            <path className="indicator-line ma-line" d={chart.ma960Path} />
            <path className="indicator-line ma-p80-line" d={chart.maP80Path} />
          </>
        )}

        {showTrend && (
          <>
            <path className="indicator-line tt-short-line" d={chart.ttShortPath} />
            <path className="indicator-line tt-long-line" d={chart.ttLongPath} />
          </>
        )}

        {candles.map((candle, index) => {
          const x = padding.left + chart.step * index + chart.step / 2
          const openY = yFor(candle.open, chart.domainMin, chart.domainMax)
          const closeY = yFor(candle.close, chart.domainMin, chart.domainMax)
          const highY = yFor(candle.high, chart.domainMin, chart.domainMax)
          const lowY = yFor(candle.low, chart.domainMin, chart.domainMax)
          const isUp = candle.close >= candle.open
          const bodyY = Math.min(openY, closeY)
          const bodyHeight = Math.max(1.5, Math.abs(closeY - openY))

          return (
            <g className={isUp ? 'candle up' : 'candle down'} key={candle.id}>
              <line x1={x} x2={x} y1={highY} y2={lowY} />
              <rect
                height={bodyHeight}
                rx="1.5"
                width={chart.candleWidth}
                x={x - chart.candleWidth / 2}
                y={bodyY}
              />
            </g>
          )
        })}

        {hoveredCandle && (
          <g className="crosshair">
            <line
              x1={padding.left + chart.step * candles.indexOf(hoveredCandle) + chart.step / 2}
              x2={padding.left + chart.step * candles.indexOf(hoveredCandle) + chart.step / 2}
              y1={padding.top}
              y2={height - padding.bottom}
            />
            <text x={padding.left + 12} y={padding.top + 18}>
              {hoveredCandle.tradingViewTime}
            </text>
          </g>
        )}
      </svg>
    </section>
  )
}
