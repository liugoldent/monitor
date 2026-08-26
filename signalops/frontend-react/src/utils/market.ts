import type { Candle, CandleStats } from '../types'

export function formatPrice(value: number): string {
  return new Intl.NumberFormat('zh-Hant-TW', {
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatPercent(value: number): string {
  return new Intl.NumberFormat('zh-Hant-TW', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(value)
}

export function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-Hant-TW', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Taipei',
  }).format(timestamp)
}

export function formatFullTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-Hant-TW', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Taipei',
  }).format(timestamp)
}

export function calculateStats(candles: Candle[]): CandleStats | null {
  if (candles.length === 0) return null

  const first = candles[0]
  const latest = candles[candles.length - 1]
  const high = Math.max(...candles.map((candle) => candle.high))
  const low = Math.min(...candles.map((candle) => candle.low))
  const averageRange =
    candles.reduce((total, candle) => total + (candle.high - candle.low), 0) / candles.length
  const change = latest.close - first.open
  const changePercent = first.open === 0 ? 0 : (change / first.open) * 100

  return {
    first,
    latest,
    high,
    low,
    change,
    changePercent,
    averageRange,
  }
}

export function getVisibleCandles(candles: Candle[], limit: number): Candle[] {
  if (limit >= candles.length) return candles
  return candles.slice(candles.length - limit)
}
