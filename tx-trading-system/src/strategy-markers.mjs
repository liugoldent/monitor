const eventTime = value => {
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value ?? '')) return null;
  const seconds = Date.parse(value.replace(' ', 'T') + '+08:00') / 1000;
  return Number.isFinite(seconds) ? Math.floor(seconds / 60) * 60 : null;
};

export function buildStrategyMarkers(rows, signals, colors) {
  const candleTimes = rows.filter(row => row.candle && !row.candle.imputed).map(row => row.time);
  const grouped = new Map();
  for (const signal of signals) {
    const minute = eventTime(signal.time);
    if (minute == null) continue;
    let lo = 0;
    let hi = candleTimes.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (candleTimes[mid] < minute) lo = mid + 1;
      else hi = mid;
    }
    const time = candleTimes[lo];
    if (time == null || time - minute > 120) continue;
    const type = signal.position > 0 ? 'long' : signal.position < 0 ? 'short' : 'exit';
    const source = signal.source === 'H' ? 'H' : 'EF';
    const key = `${time}:${source}:${type}`;
    grouped.set(key, { time, source, type, count: (grouped.get(key)?.count ?? 0) + 1 });
  }
  return [...grouped.values()].sort((a, b) => a.time - b.time || a.source.localeCompare(b.source) || a.type.localeCompare(b.type)).map(({ time, source, type, count }) => ({
    time,
    position: type === 'long' ? 'belowBar' : 'aboveBar',
    shape: type === 'long' ? 'arrowUp' : type === 'short' ? 'arrowDown' : 'circle',
    color: colors[type],
    text: `${source === 'H' ? 'H' : ''}${type === 'long' ? 'L' : type === 'short' ? 'S' : 'X'}${count > 1 ? `×${count}` : ''}`,
    size: 0.8,
  }));
}
