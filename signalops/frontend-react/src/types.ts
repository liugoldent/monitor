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

export type SignalAction = 'enter' | 'exit' | 'reverse'
export type SignalSide = 'bull' | 'bear'

export type SignalEvent = {
  id: string
  schema_version: 1
  occurred_at: string
  received_at: string
  source: 'legacy_csv' | 'webhook' | 'replay'
  instrument: string
  strategy_code: string
  strategy_name: string
  account_ref: string | null
  previous_position: -1 | 0 | 1
  new_position: -1 | 0 | 1
  action: SignalAction
  side: SignalSide
  quantity: string
  reference_price: string | null
  attributes: Record<string, unknown>
}

export type SignalEventPage = {
  items: SignalEvent[]
  next_cursor: string | null
}

export type SignalFilter = SignalAction | 'all'

export type SignalDataStatus =
  | { state: 'loading'; items: SignalEvent[] }
  | { state: 'ready'; items: SignalEvent[]; nextCursor: string | null; loadingMore: boolean }
  | { state: 'error'; items: SignalEvent[]; message: string }

export type LiveConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export type CurrentPosition = {
  event_id: string
  strategy_code: string
  strategy_name: string
  instrument: string
  position: -1 | 0 | 1
  position_label: 'long' | 'short' | 'flat'
  quantity: string
  updated_at: string
}

export type OverviewCounts = {
  total_events: number
  strategies: number
  entries: number
  exits: number
  reversals: number
  long_positions: number
  short_positions: number
  flat_positions: number
}

export type StrategySummary = {
  strategy_code: string
  strategy_name: string
  event_count: number
  entries: number
  exits: number
  reversals: number
  current_position: -1 | 0 | 1
  last_event_at: string
}

export type SignalOverview = {
  generated_at: string
  last_event_at: string | null
  counts: OverviewCounts
  positions: CurrentPosition[]
  strategies: StrategySummary[]
}

export type BusinessAnalytics = {
  generated_at: string
  periods: number
  kpis: {
    active_strategies: number
    exposure_rate: number
    reversal_rate: number
    average_events_per_strategy: number
    reference_price_coverage: number
  }
  activity: Array<{
    period: string
    total: number
    entries: number
    exits: number
    reversals: number
  }>
  transitions: Array<{
    previous_position: -1 | 0 | 1
    new_position: -1 | 0 | 1
    count: number
  }>
  data_quality: {
    total_events: number
    missing_reference_price: number
    reference_price_coverage: number
    last_event_at: string | null
  }
  limitations: string[]
}

export type AssistantCitation = {
  id: string
  label: string
  href: string
}

export type AssistantStatus =
  | { state: 'idle'; text: ''; citations: []; mode: null }
  | { state: 'streaming'; text: string; citations: AssistantCitation[]; mode: string | null }
  | { state: 'ready'; text: string; citations: AssistantCitation[]; mode: string | null }
  | { state: 'error'; text: string; citations: AssistantCitation[]; mode: string | null; message: string }
