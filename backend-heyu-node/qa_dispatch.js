import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import dotenv from "dotenv";
import { spawn } from "node:child_process";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildQaPrompt, parseQaDispatch } from "./qa_dispatch_core.js";
import { verifyIssueAccess } from "./qa_issue_access.js";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(serviceDir, ".env"), quiet: true });
const repoDir = process.env.QA_PROJECT_DIR || "/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3";
const runtimeDir = path.join(serviceDir, "runtime", "qa-dispatch");
const statePath = path.join(runtimeDir, "state.json");
const chatId = process.env.QA_CHAT_ID;
const username = process.env.QA_ISSUE_USER;
const password = process.env.QA_ISSUE_PASSWORD;
const apiId = Number(process.env.TG_API_ID);
if (!Number.isInteger(apiId) || !process.env.TG_API_HASH || !process.env.TG_SESSION || !chatId || !username || !password) {
  throw new Error("TG_API_ID, TG_API_HASH, TG_SESSION, QA_CHAT_ID, QA_ISSUE_USER and QA_ISSUE_PASSWORD are required");
}

const client = new TelegramClient(new StringSession(process.env.TG_SESSION), apiId, process.env.TG_API_HASH, { connectionRetries: 5 });
const state = JSON.parse(await readFile(statePath, "utf8").catch(error => {
  if (error.code === "ENOENT") return '{"issues":{}}';
  throw error;
}));
let queue = Promise.resolve();
const seenMessages = new Set();

async function saveState() {
  await mkdir(runtimeDir, { recursive: true });
  await writeFile(`${statePath}.tmp`, JSON.stringify(state, null, 2), { mode: 0o600 });
  await rename(`${statePath}.tmp`, statePath);
}

function runCodex(issue) {
  return new Promise((resolve, reject) => {
    const output = path.join(runtimeDir, `${issue}.codex.log`);
    const child = spawn(process.env.CODEX_BIN || "codex", ["exec", "--approve-for-me", "--sandbox", "workspace-write", "--cd", repoDir, "-"], {
      cwd: repoDir,
      stdio: ["pipe", "pipe", "pipe"],
      env: Object.fromEntries(["PATH", "HOME", "USER", "LANG", "TMPDIR", "CODEX_HOME"].filter(key => process.env[key]).map(key => [key, process.env[key]])),
    });
    const chunks = [];
    let stdout = "";
    let size = 0;
    child.stdout.on("data", chunk => {
      size += chunk.length;
      if (size > 1_000_000) child.kill("SIGTERM");
      else { chunks.push(chunk); stdout += chunk; }
    });
    child.stderr.on("data", chunk => {
      size += chunk.length;
      if (size > 1_000_000) child.kill("SIGTERM");
      else chunks.push(chunk);
    });
    child.on("error", reject);
    child.on("close", async code => {
      try {
        await writeFile(output, Buffer.concat(chunks), { mode: 0o600 });
        if (code === 0) resolve({ output, result: /^RESULT: (MR_CREATED|NEEDS_INPUT|FAILED)$/m.exec(stdout)?.[1] || "NEEDS_REVIEW" });
        else reject(new Error(`Codex exited ${code}; see ${output}`));
      } catch (error) { reject(error); }
    });
    child.stdin.end(buildQaPrompt(issue, username, password, repoDir));
  });
}

async function notify(message) {
  const recipient = process.env.QA_NOTIFY_CHAT_ID || (await client.getMe()).id;
  await client.sendMessage(recipient, { message });
}

async function processIssue(issue, messageId) {
  if (state.issues[issue]) return;
  try {
    await verifyIssueAccess(issue, username, password);
  } catch (error) {
    console.error(`[qa] #${issue}: 工單頁無法讀取，未啟動 Codex：${error.message}`);
    return;
  }
  state.issues[issue] = { status: "started", messageId, at: new Date().toISOString() };
  await saveState();
  try {
    const { output, result } = await runCodex(issue);
    state.issues[issue].status = result === "MR_CREATED" ? "completed" : result.toLowerCase();
    state.issues[issue].output = output;
    console.log(`[qa] #${issue}: Codex ${result}; ${output}`);
    try {
      await notify(result === "MR_CREATED"
        ? `工單 #${issue} 的 Codex 工作已完成，MR 已建立。請在本地原專案手動測試；執行紀錄：${output}`
        : `工單 #${issue} 的 Codex 工作停在 ${result}，請查看問題或失敗原因：${output}`);
    } catch (error) {
      console.error(`[qa] #${issue}: 通知發送失敗：${error.message}`);
    }
  } catch (error) {
    state.issues[issue].status = "failed";
    state.issues[issue].error = error.message;
    console.error(`[qa] #${issue}: ${error.message}`);
    try {
      await notify(`工單 #${issue} 的 Codex 工作失敗：${error.message}`);
    } catch (notifyError) {
      console.error(`[qa] #${issue}: 通知發送失敗：${notifyError.message}`);
    }
  } finally {
    state.issues[issue].finishedAt = new Date().toISOString();
    await saveState();
  }
}

await client.connect();
if (!(await client.isUserAuthorized())) throw new Error("TG_SESSION is not authorized");
const dialogs = await client.getDialogs({ limit: 500 });
const group = dialogs.find(dialog => String(dialog.id) === chatId);
if (!group || !group.isGroup || group.name !== "前端QA提測裙") {
  throw new Error("QA_CHAT_ID must match the group named 前端QA提測裙");
}
client.addEventHandler(event => {
  const message = event.message;
  if (!message || message.out || String(message.chatId) !== chatId) return;
  const issue = parseQaDispatch(message.message);
  if (!issue) return;
  const key = `${chatId}:${message.id}`;
  if (seenMessages.has(key)) return;
  seenMessages.add(key);
  if (seenMessages.size > 1000) seenMessages.clear();
  queue = queue.then(() => processIssue(issue, message.id)).catch(error => console.error(`[qa] queue error: ${error.message}`));
}, new NewMessage({}));
console.log("[qa] listening for assigned tickets in 前端QA提測裙");
