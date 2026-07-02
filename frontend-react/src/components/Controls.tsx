type ControlsProps = {
  candleLimit: number
  showMa: boolean
  showTrend: boolean
  onCandleLimitChange: (value: number) => void
  onShowMaChange: (value: boolean) => void
  onShowTrendChange: (value: boolean) => void
}

const candleOptions = [120, 240, 480, 960]

export function Controls({
  candleLimit,
  showMa,
  showTrend,
  onCandleLimitChange,
  onShowMaChange,
  onShowTrendChange,
}: ControlsProps) {
  return (
    <section className="control-panel" aria-label="Chart controls">
      <div className="segmented-control" aria-label="Visible candles">
        {candleOptions.map((option) => (
          <button
            className={candleLimit === option ? 'active' : ''}
            key={option}
            onClick={() => onCandleLimitChange(option)}
            type="button"
          >
            {option}
          </button>
        ))}
      </div>

      <label className="switch-control">
        <input checked={showMa} onChange={(event) => onShowMaChange(event.target.checked)} type="checkbox" />
        <span>MA</span>
      </label>

      <label className="switch-control">
        <input checked={showTrend} onChange={(event) => onShowTrendChange(event.target.checked)} type="checkbox" />
        <span>Trend</span>
      </label>
    </section>
  )
}
