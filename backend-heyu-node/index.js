import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import dotenv from "dotenv";
import { spawn } from "node:child_process";
import { access, mkdir, realpath, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildAnalysisPrompt, formatMessages, parseDecision } from "./watcher_core.js";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
const repoDir = "/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3";
dotenv.config({ path: path.join(serviceDir, ".env"), quiet: true });

const apiId = Number(process.env.TG_API_ID);
const apiHash = process.env.TG_API_HASH;
const session = process.env.TG_SESSION;
if (!Number.isInteger(apiId) || !apiHash || !session) {
  throw new Error("TG_API_ID, TG_API_HASH, and TG_SESSION are required");
}

const waitMs = Number(process.env.DUTY_CONTEXT_WAIT_SECONDS || 90) * 1000;
const allowedChats = new Set((process.env.DUTY_CHAT_IDS || "").split(",").map(x => x.trim()).filter(Boolean));
if (allowedChats.size === 0) {
  throw new Error("DUTY_CHAT_IDS is required; refusing to monitor every chat");
}
const reportDir = path.join(serviceDir, "runtime", "reports");
const codexBin = process.env.CODEX_BIN || "codex";
const chatStates = new Map();
const seenMessages = new Set();

const client = new TelegramClient(new StringSession(session), apiId, apiHash, {
  connectionRetries: 5,
});

async function validateAnalysisProject() {
  if (await realpath(repoDir) !== repoDir) {
    throw new Error("The analysis project path must be a real directory, not a symlink");
  }
  try {
    await access(path.join(repoDir, ".codex", "config.toml"));
    throw new Error("Project Codex config exists; review its tools before duty analysis can run");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

async function investigate(context) {
  await validateAnalysisProject();
  const filesystem = {
    ":root": "deny",
    ":minimal": "read",
    ":tmpdir": "deny",
    ":slash_tmp": "deny",
    [repoDir]: "read",
  };
  const filesystemToml = `{${Object.entries(filesystem).map(([key, value]) => `${JSON.stringify(key)}=${JSON.stringify(value)}`).join(",")}}`;
  const prompt = buildAnalysisPrompt(repoDir, context);

  return new Promise((resolve, reject) => {
    const child = spawn(codexBin, [
      "exec", "--strict-config", "--ephemeral", "--ignore-user-config", "--ignore-rules",
      "--cd", repoDir,
      "--config", 'default_permissions="duty-project-only"',
      "--config", `permissions.duty-project-only.filesystem=${filesystemToml}`,
      "--config", "permissions.duty-project-only.network.enabled=false",
      "--config", 'web_search="disabled"',
      "--config", "features.hooks=false",
      "--config", "features.plugins=false",
      "--config", "features.multi_agent=false",
      "-",
    ], {
      cwd: repoDir,
      stdio: ["pipe", "pipe", "pipe"],
      env: Object.fromEntries(["PATH", "HOME", "USER", "LANG", "TMPDIR"].filter(key => process.env[key]).map(key => [key, process.env[key]])),
    });
    let output = "";
    let errorText = "";
    const timer = setTimeout(() => child.kill("SIGTERM"), 5 * 60 * 1000);
    child.stdout.on("data", chunk => {
      output += chunk;
      if (output.length > 200_000) child.kill("SIGTERM");
    });
    child.stderr.on("data", chunk => { errorText = (errorText + chunk).slice(-8000); });
    child.on("error", reject);
    child.on("close", code => {
      clearTimeout(timer);
      if (code === 0 && output.trim()) resolve(output.trim());
      else reject(new Error(`Codex exited ${code}: ${errorText}`));
    });
    child.stdin.end(prompt);
  });
}

async function handleBatch(message) {
  const chatId = String(message.chatId);
  const active = chatStates.get(chatId);
  if (active) {
    if (active.phase === "analyzing" && (!active.pending || message.id > active.pending.id)) {
      active.pending = message;
    }
    return;
  }
  const state = { phase: "collecting", pending: null, lastIncludedId: message.id };
  chatStates.set(chatId, state);
  const id = `${new Date().toISOString().replaceAll(":", "-")}-${randomUUID()}`;
  const basePath = path.join(reportDir, id);
  try {
    const peer = message.peerId;
    const before = await client.getMessages(peer, { limit: 12, maxId: message.id });
    await new Promise(resolve => setTimeout(resolve, waitMs));
    state.phase = "analyzing";
    const after = await client.getMessages(peer, { limit: 20, minId: message.id });
    state.lastIncludedId = Math.max(message.id, ...after.map(item => item.id));
    const context = formatMessages([...before, message, ...after], message.id);
    console.log(`[duty] classifying chat=${chatId} from_message=${message.id}`);
    const report = await investigate(context);
    const classification = parseDecision(report);
    if (classification === "NO_PROBLEM") {
      console.log(`[duty] no problem found: chat=${chatId} from_message=${message.id}`);
      return;
    }
    await mkdir(reportDir, { recursive: true });
    await writeFile(`${basePath}.context.txt`, context, { mode: 0o600 });
    const resultPath = `${basePath}.${classification === "PROBLEM" ? "report" : "review"}.txt`;
    await writeFile(resultPath, report, { mode: 0o600 });
    console.log(`[duty] ${classification} saved: ${resultPath}`);
  } catch (error) {
    await mkdir(reportDir, { recursive: true });
    await writeFile(`${basePath}.error.txt`, String(error?.stack || error), { mode: 0o600 });
    console.error(`[duty] analysis failed; details saved: ${basePath}.error.txt`);
  } finally {
    chatStates.delete(chatId);
    if (state.pending && state.pending.id > state.lastIncludedId) void handleBatch(state.pending);
  }
}

await validateAnalysisProject();
await client.connect();
if (!(await client.isUserAuthorized())) {
  throw new Error("TG_SESSION is not authorized; no login prompt is attempted");
}
client.addEventHandler(event => {
  const message = event.message;
  if (!message || message.out || !message.chatId) return;
  const mediaType = message.media?.className;
  if (!message.message && mediaType !== "MessageMediaPhoto" && mediaType !== "MessageMediaDocument") return;
  const chatId = String(message.chatId);
  if (allowedChats.size && !allowedChats.has(chatId)) return;
  const key = `${chatId}:${message.id}`;
  if (seenMessages.has(key)) return;
  seenMessages.add(key);
  if (seenMessages.size > 1000) seenMessages.clear();
  void handleBatch(message);
}, new NewMessage({}));
console.log(`[duty] listening for possible problems in ${[...allowedChats].join(",")}; reports stay local; Telegram sending is disabled`);
