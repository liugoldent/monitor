export type Candle = {
  id: string
  recordTime: string
  tradingViewTime: string
  timestamp: number
  symbol: string
  timeframe: string
  open: number
  high: number
  low: number
  close: number
  ma960: number | null
  maP80: number | null
  maP200: number | null
  maN110: number | null
  maN200: number | null
  ttShort: number | null
  ttLong: number | null
  bbr: number | null
}

export type CandleStats = {
  first: Candle
  latest: Candle
  high: number
  low: number
  change: number
  changePercent: number
  averageRange: number
}

export type DataStatus =
  | { state: 'loading' }
  | { state: 'ready'; candles: Candle[] }
  | { state: 'error'; message: string }
