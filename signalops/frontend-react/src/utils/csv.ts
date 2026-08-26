import type { Candle } from '../types'

const requiredHeaders = [
  'Record Time',
  'Symbol',
  'Timeframe',
  'TradingView Time',
  'Open',
  'High',
  'Low',
  'Close',
]

function toNumber(value: string | undefined): number {
  if (!value) return Number.NaN
  return Number(value)
}

function toOptionalNumber(value: string | undefined): number | null {
  if (!value) return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function parseTaipeiTime(value: string): number {
  const normalized = value.trim().replace(' ', 'T')
  return Date.parse(`${normalized}+08:00`)
}

export function parseWebhookCsv(csv: string): Candle[] {
  const rows = csv.trim().split(/\r?\n/)
  const headerRow = rows.shift()

  if (!headerRow) {
    return []
  }

  const headers = headerRow.split(',').map((header) => header.trim())
  const indexes = new Map(headers.map((header, index) => [header, index]))

  for (const header of requiredHeaders) {
    if (!indexes.has(header)) {
      throw new Error(`CSV 缺少必要欄位：${header}`)
    }
  }

  const valueAt = (columns: string[], header: string) => columns[indexes.get(header) ?? -1]

  return rows.reduce<Candle[]>((candles, row, rowIndex) => {
    if (!row.trim()) return candles

    const columns = row.split(',').map((column) => column.trim())
    const tradingViewTime = valueAt(columns, 'TradingView Time')
    const timestamp = parseTaipeiTime(tradingViewTime)
    const open = toNumber(valueAt(columns, 'Open'))
    const high = toNumber(valueAt(columns, 'High'))
    const low = toNumber(valueAt(columns, 'Low'))
    const close = toNumber(valueAt(columns, 'Close'))

    if (![timestamp, open, high, low, close].every(Number.isFinite)) {
      return candles
    }

    candles.push({
      id: `${tradingViewTime}-${rowIndex}`,
      recordTime: valueAt(columns, 'Record Time'),
      tradingViewTime,
      timestamp,
      symbol: valueAt(columns, 'Symbol'),
      timeframe: valueAt(columns, 'Timeframe'),
      open,
      high,
      low,
      close,
      ma960: toOptionalNumber(valueAt(columns, 'MA_960')),
      maP80: toOptionalNumber(valueAt(columns, 'MA_P80')),
      maP200: toOptionalNumber(valueAt(columns, 'MA_P200')),
      maN110: toOptionalNumber(valueAt(columns, 'MA_N110')),
      maN200: toOptionalNumber(valueAt(columns, 'MA_N200')),
      ttShort: toOptionalNumber(valueAt(columns, 'tt_short')),
      ttLong: toOptionalNumber(valueAt(columns, 'tt_long')),
      bbr: toOptionalNumber(valueAt(columns, 'BBR')),
    })

    return candles
  }, [])
}
