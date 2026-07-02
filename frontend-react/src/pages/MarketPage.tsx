import { useMemo, useState } from 'react'
import { CandlestickChart } from '../components/CandlestickChart'
import { Controls } from '../components/Controls'
import { LatestTable } from '../components/LatestTable'
import { MarketSummary } from '../components/MarketSummary'
import { useCandles } from '../hooks/useCandles'
import { calculateStats, getVisibleCandles } from '../utils/market'

export function MarketPage() {
  const status = useCandles()
  const [candleLimit, setCandleLimit] = useState(240)
  const [showMa, setShowMa] = useState(true)
  const [showTrend, setShowTrend] = useState(true)

  const visibleCandles = useMemo(() => {
    if (status.state !== 'ready') return []
    return getVisibleCandles(status.candles, candleLimit)
  }, [candleLimit, status])

  const stats = useMemo(() => calculateStats(visibleCandles), [visibleCandles])

  if (status.state === 'loading') {
    return (
      <div className="state-panel">
        <strong>Loading CSV</strong>
        <span>Preparing 1m futures candles...</span>
      </div>
    )
  }

  if (status.state === 'error') {
    return (
      <div className="state-panel error">
        <strong>CSV Error</strong>
        <span>{status.message}</span>
      </div>
    )
  }

  if (!stats) {
    return (
      <div className="state-panel error">
        <strong>No Data</strong>
        <span>CSV parsed successfully, but no valid OHLC rows were found.</span>
      </div>
    )
  }

  return (
    <div className="market-page">
      <Controls
        candleLimit={candleLimit}
        onCandleLimitChange={setCandleLimit}
        onShowMaChange={setShowMa}
        onShowTrendChange={setShowTrend}
        showMa={showMa}
        showTrend={showTrend}
      />
      <MarketSummary stats={stats} totalRows={status.candles.length} visibleRows={visibleCandles.length} />
      <CandlestickChart candles={visibleCandles} showMa={showMa} showTrend={showTrend} />
      <LatestTable candles={visibleCandles} />
    </div>
  )
}
