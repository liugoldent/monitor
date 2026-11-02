import test from 'node:test';
import assert from 'node:assert/strict';
import { summarizeStrategies } from './strategies.mjs';
const header = 'message_time,strategy_code,strategy_name,previous_position,new_position,action,quantity\n';
test('current state is the last signed position, not accumulated entry or reversal quantities', () => {
  const data = summarizeStrategies(header + ',A,策略甲,0,1,enter,1\n2026-11-02 10:00:00,A,策略甲,1,-1,reverse,1\n2026-11-02 11:00:00,B,策略乙,0,2,enter,2\n');
  assert.equal(data.strategies.find(s => s.code === 'A').position, -1);
  assert.equal(data.summary.net, 1);
  assert.equal(data.summary.longUnits, 2);
  assert.equal(data.summary.shortUnits, 1);
  assert.equal(data.summary.direction, 'long');
  assert.equal(data.summary.events, 3);
});
test('closing a short is flat, missing historical times stay null, histories use append order', () => {
  const data = summarizeStrategies(header + ',A,策略甲,0,-1,enter,1\n2026-11-02 10:00:00,A,策略甲,-1,0,exit,1\n');
  assert.equal(data.summary.flat, 1);
  assert.equal(data.summary.net, 0);
  assert.equal(data.strategies[0].events[0].action, 'exit');
  assert.equal(data.strategies[0].events[0].direction, 'short');
  assert.equal(data.strategies[0].events[1].time, null);
});
test('invalid or truncated rows do not overwrite current state, empty file has no invented positions', () => {
  const data = summarizeStrategies(header + ',A,策略甲,0,1,enter,1\n,A,策略甲,1,,exit,\n,A,策略甲,1,1,enter,1\n');
  assert.equal(data.invalid, 2);
  assert.equal(data.summary.net, 1);
  assert.equal(summarizeStrategies(header).summary.total, 0);
});
test('opposing positions can sum to zero while strategies still hold positions', () => {
  const data = summarizeStrategies(header + ',A,策略甲,0,1,enter,1\n,B,策略乙,0,-1,enter,1\n');
  assert.equal(data.summary.net, 0);
  assert.equal(data.summary.long, 1);
  assert.equal(data.summary.short, 1);
  assert.equal(data.summary.flat, 0);
});
