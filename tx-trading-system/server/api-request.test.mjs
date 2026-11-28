import test from 'node:test';
import assert from 'node:assert/strict';
import market from '../api/market.js';
import chartSignals from '../api/chart-signals.js';
import health from '../api/health.js';

function response() {
  return {
    headers: {},
    statusCode: null,
    body: null,
    setHeader(name, value) { this.headers[name] = value; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
  };
}

test('public API rejects POST before touching MongoDB', async () => {
  const res = response();
  await market({ method: 'POST', url: '/api/market' }, res);
  assert.equal(res.statusCode, 405);
  assert.equal(res.headers.Allow, 'GET');
  assert.equal(res.headers['Cache-Control'], 'no-store');
});

test('invalid and overly broad queries fail before opening MongoDB', async () => {
  const invalidMarket = response();
  await market({ method: 'GET', url: '/api/market?date=invalid' }, invalidMarket);
  assert.equal(invalidMarket.statusCode, 400);
  const broadSignals = response();
  await chartSignals({ method: 'GET', url: '/api/chart-signals?from=2026-01-01&date=2026-11-28' }, broadSignals);
  assert.equal(broadSignals.statusCode, 400);
});

test('health endpoint remains available for GET only', () => {
  const get = response();
  health({ method: 'GET' }, get);
  assert.equal(get.statusCode, 200);
  const post = response();
  health({ method: 'POST' }, post);
  assert.equal(post.statusCode, 405);
});
