// Preserve zoom while following the right edge; anchor history by timestamp
// when older points roll out of the bounded server window or are backfilled.
export function isAtLatest(range, count) {
  return Boolean(range && count && range.to >= count - 1 - 0.5);
}
export function reconcileRange(previous, next, range, reset = false) {
  if (!next.length) return null;
  if (reset || !range || !previous.length) return { from: Math.max(0, next.length - 180), to: next.length + 8 };
  if (isAtLatest(range, previous.length)) {
    const to = next.length - 1 + Math.max(8, range.to - (previous.length - 1));
    return { from: to - (range.to - range.from), to };
  }
  const anchor = Math.max(0, Math.min(previous.length - 1, Math.floor(range.from)));
  const time = previous[anchor].time;
  let low = 0, high = next.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (next[middle].time < time) low = middle + 1;
    else high = middle;
  }
  const shift = Math.min(low, next.length - 1) - anchor;
  return { from: range.from + shift, to: range.to + shift };
}
