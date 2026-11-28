const cache = new Map();
const ttlMs = 30000;

export async function cachedStrategyData(key, load) {
  const now = Date.now();
  const hit = cache.get(key);
  if (hit?.data && hit.expires > now) return hit.data;
  if (hit?.pending) return hit.pending;
  const pending = load();
  cache.set(key, { pending });
  try {
    const data = await pending;
    cache.set(key, { data, expires: Date.now() + ttlMs });
    if (cache.size > 16) cache.delete(cache.keys().next().value);
    return data;
  } catch (error) {
    cache.delete(key);
    throw error;
  }
}
