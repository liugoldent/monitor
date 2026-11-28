export function tradeChartEvent(row, strategy) {
  const side = row.side === 'bull' ? 1 : row.side === 'bear' ? -1 : null;
  const action = row.action === 'enter' ? 'enter' : row.action === 'exiting' ? 'exit' : null;
  const time = typeof row.timestamp === 'string' ? row.timestamp.slice(0, 19) : '';
  return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(time) && side && action
    ? { time, strategy, action, side }
    : null;
}

export function latestTradePosition(row) {
  if (!row) return { position: null, time: null };
  const event = tradeChartEvent(row, '');
  return event
    ? { position: event.action === 'enter' ? event.side : 0, time: event.time }
    : { position: null, time: null };
}
