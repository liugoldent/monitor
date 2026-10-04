import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import dotenv from "dotenv";
import { spawn } from "node:child_process";
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildMrReviewPrompt, extractIssueNumberFromMrTitle, extractIssueRequirement, isMrReviewTrigger, parsePreviousMr } from "./mr_review_core.js";
import { verifyMrAccess } from "./mr_access.js";
import { readIssuePage } from "./qa_issue_access.js";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(serviceDir, ".env"), quiet: true });
const repoDir = process.env.MR_PROJECT_DIR || process.env.QA_PROJECT_DIR || "/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3";
const runtimeDir = path.join(serviceDir, "runtime", "mr-review");
const statePath = path.join(runtimeDir, "state.json");
const receiptPath = path.join(runtimeDir, "received.jsonl");
const issueUser = process.env.MR_ISSUE_USER || process.env.QA_ISSUE_USER;
const issuePassword = process.env.MR_ISSUE_PASSWORD || process.env.QA_ISSUE_PASSWORD;
const apiId = Number(process.env.TG_API_ID);
if (!Number.isInteger(apiId) || !process.env.TG_API_HASH || !process.env.TG_SESSION) {
  throw new Error("TG_API_ID, TG_API_HASH and TG_SESSION are required");
}
const client = new TelegramClient(new StringSession(process.env.TG_SESSION), apiId, process.env.TG_API_HASH, { connectionRetries: 5 });
const state = JSON.parse(await readFile(statePath, "utf8").catch(error => {
  if (error.code === "ENOENT") return '{"messages":{}}';
  throw error;
}));
let queue = Promise.resolve();
let receiptQueue = Promise.resolve();
const queuedMessages = new Set();
const observedMessages = new Set((await readFile(receiptPath, "utf8").catch(error => {
  if (error.code === "ENOENT") return "";
  throw error;
})).trim().split("\n").slice(-2000).flatMap(line => {
  try {
    const record = JSON.parse(line);
    return record.chatId && record.messageId ? [`${record.chatId}:${record.messageId}`] : [];
  } catch { return []; }
}));
const pollIntervalMs = 30_000;
const recoveryWindowMs = 4 * 60 * 60 * 1000;

async function saveState() {
  await mkdir(runtimeDir, { recursive: true });
  await writeFile(`${statePath}.tmp`, JSON.stringify(state, null, 2), { mode: 0o600 });
  await rename(`${statePath}.tmp`, statePath);
}

function runReview(mr) {
  return new Promise((resolve, reject) => {
    const reportPath = path.join(runtimeDir, `${mr.number}-${Date.now()}.report.txt`);
    const logPath = reportPath.replace(".report.txt", ".codex.log");
    const child = spawn(process.env.CODEX_BIN || "codex", ["exec", "--approve-for-me", "--sandbox", "workspace-write", "--cd", repoDir, "--output-last-message", reportPath, "-"], {
      cwd: repoDir,
      stdio: ["pipe", "pipe", "pipe"],
      env: Object.fromEntries(["PATH", "HOME", "USER", "LANG", "TMPDIR", "CODEX_HOME"].filter(key => process.env[key]).map(key => [key, process.env[key]])),
    });
    const chunks = [];
    let size = 0;
    const timer = setTimeout(() => child.kill("SIGTERM"), 20 * 60 * 1000);
    for (const stream of [child.stdout, child.stderr]) stream.on("data", chunk => {
      size += chunk.length;
      if (size > 1_000_000) child.kill("SIGTERM");
      else chunks.push(chunk);
    });
    child.on("error", reject);
    child.on("close", async code => {
      clearTimeout(timer);
      try {
        await writeFile(logPath, Buffer.concat(chunks), { mode: 0o600 });
        let report = await readFile(reportPath, "utf8").catch(() => "");
        if (code !== 0 || !/^RESULT: (PASS|FAIL|INSUFFICIENT)$/m.test(report)) {
          report = `RESULT: INSUFFICIENT\nMR: ${mr.url}\n\n審查程序未能產生可驗證結論（Codex exit=${code}）。請人工檢查；執行紀錄：${logPath}\n\n${report}`;
          await writeFile(reportPath, report, { mode: 0o600 });
        }
        resolve({ reportPath, report });
      } catch (error) { reject(error); }
    });
    child.stdin.end(buildMrReviewPrompt({ ...mr, repoDir }));
  });
}

async function notificationRecipient() {
  return process.env.MR_NOTIFY_CHAT_ID || process.env.QA_NOTIFY_CHAT_ID || (await client.getMe()).id;
}

async function notify(reportPath, report, number) {
  const recipient = await notificationRecipient();
  await client.sendFile(recipient, { file: reportPath, caption: `MR !${number} 審查報告\n${report.slice(0, 800)}` });
}

async function sendStopNotice(record) {
  if (record.notifiedAt) return;
  try {
    await client.sendMessage(await notificationRecipient(), {
      message: `MR !${record.number} 已收到，但審查已停止。\n${record.error}\n${record.url}`,
    });
    record.notifiedAt = new Date().toISOString();
    delete record.notificationError;
  } catch (error) {
    record.notificationError = error.message;
    console.error(`[mr] !${record.number}: notification failed: ${error.message}`);
  }
  await saveState();
}

async function stopAndNotify(key, mr, status, reason) {
  state.messages[key] = { number: mr.number, url: mr.url, status, error: reason, at: new Date().toISOString() };
  await saveState();
  console.error(`[mr] !${mr.number}: ${reason}; stopped`);
  await sendStopNotice(state.messages[key]);
}

async function processTrigger(message) {
  const key = `${message.chatId}:${message.id}`;
  if (state.messages[key]) return;
  const previous = await client.getMessages(message.peerId, { limit: 1, maxId: message.id });
  const mr = parsePreviousMr(previous[0]?.message);
  if (!mr) {
    console.log(`[mr] trigger ${message.id}: previous message is not a single pc MR`);
    return;
  }
  console.log(`[mr] trigger ${message.id}: checking MR !${mr.number}`);
  let mrPage;
  try {
    mrPage = await verifyMrAccess(mr.url);
  } catch (error) {
    await stopAndNotify(key, mr, "access_blocked", `GitLab MR 頁面無法讀取：${error.message}`);
    return;
  }
  const issueNumber = extractIssueNumberFromMrTitle(mrPage.title);
  if (!issueNumber) {
    await stopAndNotify(key, mr, "missing_issue_number", `MR 標題沒有唯一的五碼工單號：${mrPage.title}`);
    return;
  }
  let issue;
  try {
    const page = await readIssuePage(issueNumber, issueUser, issuePassword);
    const requirement = extractIssueRequirement(page.html);
    if (!requirement) throw new Error("工單需求描述無法讀取");
    issue = { number: issueNumber, url: page.url, requirement };
  } catch (error) {
    await stopAndNotify(key, mr, "issue_access_blocked", `工單 #${issueNumber} 無法讀取（VPN、登入或網站問題）：${error.message}`);
    return;
  }
  console.log(`[mr] !${mr.number}: issue #${issueNumber} readable; starting review`);
  state.messages[key] = { number: mr.number, issueNumber, status: "started", at: new Date().toISOString() };
  await saveState();
  try {
    const { reportPath, report } = await runReview({ ...mr, mrTitle: mrPage.title, issue });
    state.messages[key].status = /^RESULT: (PASS|FAIL|INSUFFICIENT)$/m.exec(report)[1].toLowerCase();
    state.messages[key].reportPath = reportPath;
    await notify(reportPath, report, mr.number);
    console.log(`[mr] !${mr.number}: ${state.messages[key].status}; ${reportPath}`);
  } catch (error) {
    state.messages[key].status = "failed";
    state.messages[key].error = error.message;
    console.error(`[mr] !${mr.number}: ${error.message}`);
  } finally {
    state.messages[key].finishedAt = new Date().toISOString();
    await saveState();
  }
}

await client.connect();
if (!(await client.isUserAuthorized())) throw new Error("TG_SESSION is not authorized");
const dialogs = await client.getDialogs({ limit: 500 });
const group = process.env.MR_CHAT_ID
  ? dialogs.find(dialog => String(dialog.id) === process.env.MR_CHAT_ID)
  : dialogs.find(dialog => dialog.isGroup && dialog.name === "平台前端");
if (!group?.isGroup || group.name !== "平台前端") throw new Error("MR_CHAT_ID must identify the group named 平台前端");
const chatId = String(group.id);
for (const record of Object.values(state.messages)) {
  if (!["access_blocked", "missing_issue_number", "issue_access_blocked"].includes(record.status) || record.notifiedAt) continue;
  record.url ||= `https://gitlab.k8s.adwqa.com/xingba/xingba_pcweb_vue3/-/merge_requests/${record.number}`;
  await sendStopNotice(record);
}
function enqueueTrigger(message) {
  if (!message || message.out || String(message.chatId) !== chatId || !isMrReviewTrigger(message.message)) return;
  const key = `${chatId}:${message.id}`;
  if (state.messages[key] || queuedMessages.has(key)) return;
  queuedMessages.add(key);
  queue = queue.then(() => processTrigger(message))
    .catch(error => console.error(`[mr] queue error: ${error.message}`));
}

function observeMessage(message, source) {
  if (!message || String(message.chatId) !== chatId) return receiptQueue;
  receiptQueue = receiptQueue.then(async () => {
    const key = `${chatId}:${message.id}`;
    if (observedMessages.has(key)) return;
    const trigger = isMrReviewTrigger(message.message);
    const decision = message.out ? "ignored_outgoing" : trigger
      ? state.messages[key] ? `already_${state.messages[key].status}` : "trigger_queued"
      : "ignored_non_trigger";
    const record = {
      observedAt: new Date().toISOString(),
      messageAt: new Date(message.date * 1000).toISOString(),
      source,
      chatId,
      messageId: message.id,
      text: message.message || "",
      mediaType: message.media?.className || null,
      decision,
    };
    await mkdir(runtimeDir, { recursive: true });
    await appendFile(receiptPath, `${JSON.stringify(record)}\n`, { mode: 0o600 });
    observedMessages.add(key);
    if (observedMessages.size > 2000) observedMessages.delete(observedMessages.values().next().value);
    const preview = String(message.message || `[${record.mediaType || "no text"}]`).replace(/\s+/g, " ").slice(0, 100);
    console.log(`[mr] received ${source} message=${message.id} decision=${decision} text=${JSON.stringify(preview)}`);
    if (decision === "trigger_queued") enqueueTrigger(message);
  }).catch(error => console.error(`[mr] receipt error: ${error.message}`));
  return receiptQueue;
}

client.addEventHandler(event => { void observeMessage(event.message, "event"); }, new NewMessage({}));
console.log(`[mr] listening for MR review trigger in 平台前端 (${chatId}); receipts: ${receiptPath}`);

// Telegram updates can be missed during reconnects or when this session is used by
// another process. Reconcile recent group history as well as listening for events.
let polling = false;
async function pollRecentMessages() {
  if (polling) return;
  polling = true;
  try {
    const messages = await client.getMessages(group, { limit: 100 });
    const cutoff = Date.now() - recoveryWindowMs;
    for (const message of [...messages].reverse()) {
      if (message.date * 1000 >= cutoff) void observeMessage(message, "history");
    }
    await receiptQueue;
  } catch (error) {
    console.error(`[mr] history poll failed: ${error.message}`);
  } finally {
    polling = false;
  }
}
await pollRecentMessages();
setInterval(pollRecentMessages, pollIntervalMs);
