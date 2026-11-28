import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const cookieName = 'tx_strategy_session';
const sessionSeconds = 7 * 24 * 60 * 60;
let localCode;

async function accessCode() {
  if (process.env.STRATEGY_ACCESS_CODE) return process.env.STRATEGY_ACCESS_CODE;
  if (process.env.VERCEL) return null;
  localCode ??= readFile(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../.runtime/strategy-access-code'), 'utf8')
    .then(value => value.trim()).catch(() => null);
  return localCode;
}

function equal(left, right) {
  const a = createHash('sha256').update(left).digest();
  const b = createHash('sha256').update(right).digest();
  return timingSafeEqual(a, b);
}

function signature(code, expires) {
  return createHmac('sha256', code).update(`v1.${expires}`).digest('base64url');
}

export async function verifyStrategyCode(value) {
  const code = await accessCode();
  if (!code) return null;
  return typeof value === 'string' && value.length <= 128 && equal(value, code);
}

export async function strategySessionCookie() {
  const code = await accessCode();
  if (!code) return null;
  const expires = Math.floor(Date.now() / 1000) + sessionSeconds;
  const token = `${expires}.${signature(code, expires)}`;
  return `${cookieName}=${token}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=${sessionSeconds}${process.env.VERCEL ? '; Secure' : ''}`;
}

export async function hasStrategySession(req) {
  const code = await accessCode();
  if (!code) return null;
  const cookies = String(req.headers?.cookie ?? '').split(';').map(part => part.trim());
  const token = cookies.find(part => part.startsWith(`${cookieName}=`))?.slice(cookieName.length + 1) ?? '';
  const match = /^(\d{10})\.([A-Za-z0-9_-]{43})$/.exec(token);
  if (!match) return false;
  const expires = Number(match[1]);
  if (expires <= Date.now() / 1000 || expires > Date.now() / 1000 + sessionSeconds) return false;
  return equal(match[2], signature(code, expires));
}

export async function requireStrategySession(req, res) {
  const allowed = await hasStrategySession(req);
  if (allowed) return true;
  res.setHeader('Cache-Control', 'no-store');
  res.status(allowed === null ? 503 : 401).json({ error: allowed === null ? '策略存取尚未設定' : '請先解鎖策略資料' });
  return false;
}
