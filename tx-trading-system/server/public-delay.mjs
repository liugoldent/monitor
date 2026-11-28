export const publicStrategyDelayMs = 30 * 60 * 1000;

export function publicStrategyCutoff(now = Date.now()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(now - publicStrategyDelayMs)).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

export function publicTradeFilter(cutoff, from, to) {
  return {
    ...(from ? { date: { $gte: from, $lte: to } } : {}),
    timestamp: { $lte: cutoff },
  };
}
