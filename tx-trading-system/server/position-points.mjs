const timestampPattern = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

export function priceNumber(value) {
  if (value == null || String(value).trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function floatingPoints(position, entryPrice, latestClose) {
  if (!Number.isFinite(position) || position === 0 || entryPrice == null || latestClose == null) return null;
  return Math.round((latestClose - entryPrice) * Math.sign(position) * 100) / 100;
}

export function latestOpenEvent(strategy) {
  if (!strategy || !strategy.position) return null;
  return strategy.events.find(event =>
    (event.action === 'enter' || event.action === 'reverse') &&
    Math.sign(event.position) === Math.sign(strategy.position)
  ) ?? null;
}

export function availableCandleEntry(candle, receivedAt) {
  const recordedAt = candle?.['Record Time'];
  const price = priceNumber(candle?.Close);
  if (!timestampPattern.test(recordedAt ?? '') || !timestampPattern.test(receivedAt ?? '') || price === null) return null;
  const lag = Date.parse(receivedAt.replace(' ', 'T') + '+08:00') - Date.parse(recordedAt.replace(' ', 'T') + '+08:00');
  if (!Number.isFinite(lag) || lag < 0 || lag > 120000) return null;
  return { price, time: candle['TradingView Time'] ?? null, recordedAt };
}
