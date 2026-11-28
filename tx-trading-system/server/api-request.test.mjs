import test from 'node:test';
import assert from 'node:assert/strict';
import market from '../api/market.js';
import chartSignals from '../api/chart-signals.js';
import strategies from '../api/strategies.js';
import strategyLogin from '../api/strategy-login.js';
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

test('market API rejects POST before touching MongoDB', async () => {
  const res = response();
  await market({ method: 'POST', url: '/api/market' }, res);
  assert.equal(res.statusCode, 405);
  assert.equal(res.headers.Allow, 'GET');
  assert.equal(res.headers['Cache-Control'], 'no-store');
});

test('invalid and overly broad queries fail before opening MongoDB', async () => {
  process.env.STRATEGY_ACCESS_CODE = 'test-strategy-secret';
  const login = response();
  await strategyLogin({ method: 'POST', body: { code: 'test-strategy-secret' } }, login);
  const cookie = login.headers['Set-Cookie'].split(';')[0];
  const invalidMarket = response();
  await market({ method: 'GET', url: '/api/market?date=invalid', headers: { cookie } }, invalidMarket);
  assert.equal(invalidMarket.statusCode, 400);
  const broadSignals = response();
  await chartSignals({ method: 'GET', url: '/api/chart-signals?from=2026-01-01&date=2026-11-28', headers: { cookie } }, broadSignals);
  assert.equal(broadSignals.statusCode, 400);
});

test('private strategy APIs reject missing or invalid sessions before MongoDB', async () => {
  process.env.STRATEGY_ACCESS_CODE = 'test-strategy-secret';
  const noSession = response();
  await strategies({ method: 'GET', headers: {} }, noSession);
  assert.equal(noSession.statusCode, 401);
  assert.equal(noSession.headers['Cache-Control'], 'no-store');
  const noMarketSession = response();
  await market({ method: 'GET', url: '/api/market', headers: {} }, noMarketSession);
  assert.equal(noMarketSession.statusCode, 401);
  const invalidSession = response();
  await chartSignals({ method: 'GET', url: '/api/chart-signals?date=2026-11-28', headers: { cookie: 'tx_strategy_session=forged' } }, invalidSession);
  assert.equal(invalidSession.statusCode, 401);
  const wrongCode = response();
  await strategyLogin({ method: 'POST', body: { code: 'wrong' } }, wrongCode);
  assert.equal(wrongCode.statusCode, 401);
  assert.equal(wrongCode.headers['Set-Cookie'], undefined);
});

test('health endpoint remains available for GET only', () => {
  const get = response();
  health({ method: 'GET' }, get);
  assert.equal(get.statusCode, 200);
  const post = response();
  health({ method: 'POST' }, post);
  assert.equal(post.statusCode, 405);
});
