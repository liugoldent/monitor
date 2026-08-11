#!/usr/bin/env node

import { access, mkdir } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const HQQ_URL = 'https://hr-att.web.app/';
const CHROME_APP = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
// Do not point Playwright at the normal Chrome directory. Chrome protects that
// directory from remote-debugging automation, and it may also be locked by a
// normal Chrome window. Sign in to HQQ once in this dedicated profile instead.
const HQQ_CHROME_USER_DATA_DIR = process.env.HQQ_CHROME_USER_DATA_DIR
  || '/Users/kt/Library/Application Support/HQQPlaywrightProfile';
const DEFAULT_OUTPUT_DIR = '/Users/kt/Downloads';
const DEFAULT_TIMEOUT_MS = 45_000;

function parseArgs(argv) {
  const options = {
    outputDir: process.env.HQQ_SCREENSHOT_OUTPUT_DIR || DEFAULT_OUTPUT_DIR,
    timeoutMs: Number(process.env.HQQ_SCREENSHOT_TIMEOUT_MS || DEFAULT_TIMEOUT_MS),
    triggerCode: '',
    triggerName: '',
    previousPosition: '',
    newPosition: '',
    signIn: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--') {
      continue;
    } else if (argument === '--output-dir') {
      options.outputDir = argv[++index];
    } else if (argument === '--timeout-ms') {
      options.timeoutMs = Number(argv[++index]);
    } else if (argument === '--trigger-code') {
      options.triggerCode = argv[++index] || '';
    } else if (argument === '--trigger-name') {
      options.triggerName = argv[++index] || '';
    } else if (argument === '--previous-position') {
      options.previousPosition = argv[++index] || '';
    } else if (argument === '--new-position') {
      options.newPosition = argv[++index] || '';
    } else if (argument === '--sign-in') {
      options.signIn = true;
    } else if (argument === '--help' || argument === '-h') {
      console.log(`\n開啟 HQQ 專用 Chrome profile、載入 HQQ、重整後截圖。\n\n` +
        `不會點擊打卡按鈕、不會送出表單。\n\n` +
        `用法：pnpm run screenshot -- [--output-dir DIR] [--timeout-ms MS] ` +
        `[--trigger-code CODE] [--trigger-name NAME] ` +
        `[--previous-position POSITION] [--new-position POSITION] [--sign-in]\n`);
      process.exit(0);
    } else {
      throw new Error(`不支援的參數：${argument}`);
    }
  }

  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1_000) {
    throw new Error('--timeout-ms 必須是至少 1000 的整數。');
  }
  return options;
}

function safeFilenamePart(value, fallback) {
  const cleaned = String(value)
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return cleaned || fallback;
}

function taipeiTimestamp(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}${values.month}${values.day}-${values.hour}${values.minute}${values.second}`;
}

async function assertReadable(filePath, label) {
  try {
    await access(filePath, constants.R_OK);
  } catch {
    throw new Error(`${label} 無法讀取：${filePath}`);
  }
}

async function signInWithGoogleIfRequested(page, context, { signIn, timeoutMs }) {
  if (!signIn) {
    return false;
  }

  const email = process.env.HQQ_GOOGLE_EMAIL;
  const password = process.env.HQQ_GOOGLE_PASSWORD;
  if (!email || !password) {
    throw new Error('--sign-in 需要暫時提供 HQQ_GOOGLE_EMAIL 與 HQQ_GOOGLE_PASSWORD；密碼不會寫入檔案。');
  }

  // The HQQ login control is rendered as a styled clickable container rather
  // than a semantic <button>, so locate its visible label instead of role.
  const signInButton = page.getByText(/^(使用 Google 帳戶登入|Sign in with Google)$/i).first();
  if (!await signInButton.isVisible().catch(() => false)) {
    return false;
  }

  const pagesBeforeClick = new Set(context.pages());
  await signInButton.click({ noWaitAfter: true });
  await page.waitForTimeout(1_000);

  const authPage = context.pages().find((candidate) => !pagesBeforeClick.has(candidate)) || page;
  await authPage.bringToFront();
  await authPage.waitForURL(/accounts\.google\.com/, { waitUntil: 'domcontentloaded', timeout: timeoutMs });

  const emailInput = authPage.locator('input[type="email"]');
  await emailInput.waitFor({ state: 'visible', timeout: timeoutMs });
  await emailInput.fill(email);
  await authPage.getByRole('button', { name: /^(Next|下一步)$/i }).click();

  const passwordInput = authPage.locator('input[type="password"]');
  await passwordInput.waitFor({ state: 'visible', timeout: timeoutMs });
  await passwordInput.fill(password);
  await authPage.getByRole('button', { name: /^(Next|下一步)$/i }).click();

  // Google may show a device confirmation or 2-step verification page.  Keep
  // the visible browser open while it waits, and never attempt to bypass it.
  await page.bringToFront();
  await page.waitForTimeout(4_000);
  return true;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const {
    outputDir,
    timeoutMs,
    triggerCode,
    triggerName,
    previousPosition,
    newPosition,
  } = options;
  await assertReadable(CHROME_APP, 'Google Chrome 執行檔');
  await mkdir(outputDir, { recursive: true });
  await mkdir(HQQ_CHROME_USER_DATA_DIR, { recursive: true });

  let context;
  try {
    context = await chromium.launchPersistentContext(HQQ_CHROME_USER_DATA_DIR, {
      executablePath: CHROME_APP,
      headless: false,
      viewport: { width: 1440, height: 1080 },
      args: [
        '--no-first-run',
        '--no-default-browser-check',
      ],
    });
  } catch (error) {
    const details = error instanceof Error ? error.message : String(error);
    if (/singleton|user data directory|profile.*in use|already running/i.test(details)) {
      throw new Error(
        'HQQPlaywrightProfile 正被其他 Chrome 視窗使用中，因此安全地停止本次截圖。' +
        '請先關閉該專用的 HQQPlaywrightProfile 視窗後再重試；一般 Chrome 可維持開啟。',
      );
    }
    throw error;
  }

  try {
    // Always create and foreground our own tab.  Chrome may show a default
    // about:blank tab on startup; reusing it can leave the HQQ tab hidden.
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutMs);
    await page.bringToFront();
    await page.goto(HQQ_URL, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    await page.bringToFront();
    if (new URL(page.url()).origin !== new URL(HQQ_URL).origin) {
      throw new Error(`HQQ 導頁驗證失敗，目前頁面是：${page.url()}`);
    }
    const signInAttempted = await signInWithGoogleIfRequested(page, context, options);
    await page.reload({ waitUntil: 'domcontentloaded', timeout: timeoutMs });
    await page.bringToFront();
    if (new URL(page.url()).origin !== new URL(HQQ_URL).origin) {
      throw new Error(`HQQ 重整驗證失敗，目前頁面是：${page.url()}`);
    }
    await page.waitForTimeout(1_500);

    const triggerPart = triggerCode
      ? `${safeFilenamePart(triggerCode, 'strategy')}-${safeFilenamePart(previousPosition, 'from')}-to-${safeFilenamePart(newPosition, 'to')}`
      : 'manual-refresh';
    const screenshotPath = path.join(outputDir, `hqq-${triggerPart}-${taipeiTimestamp()}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(JSON.stringify({
      action: 'opened-refreshed-and-screenshot',
      userDataDirectory: HQQ_CHROME_USER_DATA_DIR,
      pageUrl: page.url(),
      screenshotPath,
      signInAttempted,
      trigger: triggerCode ? {
        strategyCode: triggerCode,
        strategyName: triggerName,
        previousPosition,
        newPosition,
      } : null,
      submitted: false,
    }));
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  console.error(`HQQ 晨間截圖失敗：${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
