import test from 'node:test';
import assert from 'node:assert/strict';
import { elapsedSeconds, relativeTime } from './relative-time.mjs';

test('Taipei timestamps are compared with the current instant across day boundaries', () => {
  const now = Date.parse('2026-11-28T16:00:00+08:00');
  assert.equal(relativeTime('2026-11-28 15:59:20', now), '剛剛');
  assert.equal(relativeTime('2026-11-28 15:58:00', now), '2 分鐘前');
  assert.equal(relativeTime('2026-11-28 04:59:00', now), '11 小時 1 分鐘前');
  assert.equal(relativeTime('2026-11-26 15:03:07', now), '2 天 0 小時前');
  assert.equal(elapsedSeconds('2026-11-26 15:03:07', now) > 86400, true);
  assert.equal(relativeTime('', now), '時間未記錄');
});
