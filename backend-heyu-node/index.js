import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import dotenv from "dotenv";
import { spawn } from "node:child_process";
import { access, mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildAnalysisPrompt, formatMessages, parseDecision, parseRoute } from "./watcher_core.js";
import { collectImages } from "./watcher_images.js";
import { repairProblem } from "./watcher_repair.js";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
const repoDir = "/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3";
const mobileDir = "/Users/kt/Desktop/work/heyu/xingba_mobileweb_vue3_rewrite";
const orchestratorSkill = await readFile("/Users/kt/.codex/skills/boss-question-orchestrator/SKILL.md", "utf8");
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
let repairQueue = Promise.resolve();

const client = new TelegramClient(new StringSession(session), apiId, apiHash, {
  connectionRetries: 5,
});

async function validateAnalysisProject() {
  for (const projectDir of [repoDir, mobileDir]) {
    if (await realpath(projectDir) !== projectDir) {
      throw new Error("The analysis project path must be a real directory, not a symlink");
    }
    try {
      await access(path.join(projectDir, ".codex", "config.toml"));
      throw new Error("Project Codex config exists; review its tools before duty analysis can run");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

async function investigate(context, images = []) {
  await validateAnalysisProject();
  const filesystem = {
    ":root": "deny",
    ":minimal": "read",
    ":tmpdir": "deny",
    ":slash_tmp": "deny",
    [repoDir]: "read",
    [mobileDir]: "read",
    ...Object.fromEntries(images.map(image => [image.path, "read"])),
  };
  const filesystemToml = `{${Object.entries(filesystem).map(([key, value]) => `${JSON.stringify(key)}=${JSON.stringify(value)}`).join(",")}}`;
  const prompt = buildAnalysisPrompt(repoDir, context, images, mobileDir, orchestratorSkill);

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
      ...images.flatMap(image => ["--image", image.path]),
      "--",
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
    console.log(`[duty] batch active: chat=${chatId} message=${message.id} phase=${active.phase}`);
    if (active.phase === "analyzing" && (!active.pending || message.id > active.pending.id)) {
      active.pending = message;
    }
    return;
  }
  const state = { phase: "collecting", pending: null, lastIncludedId: message.id };
  chatStates.set(chatId, state);
  console.log(`[duty] collecting: chat=${chatId} from_message=${message.id} wait_ms=${waitMs}`);
  const id = `${new Date().toISOString().replaceAll(":", "-")}-${randomUUID()}`;
  const basePath = path.join(reportDir, id);
  try {
    const peer = message.peerId;
    const before = await client.getMessages(peer, { limit: 12, maxId: message.id });
    await new Promise(resolve => setTimeout(resolve, waitMs));
    state.phase = "analyzing";
    const after = await client.getMessages(peer, { limit: 20, minId: message.id });
    state.lastIncludedId = Math.max(message.id, ...after.map(item => item.id));
    const messages = [...before, message, ...after];
    const { images, notes } = await collectImages(client, messages, `${basePath}.images`);
    const context = [formatMessages(messages, message.id), ...notes].join("\n\n");
    console.log(`[duty] classifying chat=${chatId} from_message=${message.id}`);
    const report = await investigate(context, images);
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
    const route = parseRoute(report);
    console.log(`[duty] route=${route} chat=${chatId} from_message=${message.id}`);
    if (route !== "ORCHESTRATE") {
      const job = { codexBin, repoDir: route === "FIX_PC" ? repoDir : mobileDir, basePath, context, report, images, skill: orchestratorSkill };
      repairQueue = repairQueue.then(() => repairProblem(job)).catch(async error => {
        console.error(`[duty] repair failed: ${error.message}`);
        await writeFile(`${basePath}.repair-error.txt`, String(error.stack || error), { mode: 0o600 });
      }).catch(error => console.error(`[duty] repair error log failed: ${error.message}`));
      console.log(`[duty] repair queued: route=${route} report=${resultPath}`);
    }
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
  if (!message || !message.chatId) return;
  const chatId = String(message.chatId);
  if (allowedChats.size && !allowedChats.has(chatId)) return;
  const mediaType = message.media?.className;
  const key = `${chatId}:${message.id}`;
  const decision = message.out ? "ignored_outgoing"
    : !message.message && mediaType !== "MessageMediaPhoto" && mediaType !== "MessageMediaDocument" ? "ignored_unsupported_content"
    : seenMessages.has(key) ? "ignored_duplicate"
    : "accepted";
  const sentAt = message.date instanceof Date ? message.date.toISOString()
    : typeof message.date === "number" ? new Date(message.date * 1000).toISOString()
    : String(message.date || "unknown");
  console.log(`[duty] received event at=${new Date().toISOString()} sent_at=${sentAt} chat=${chatId} message=${message.id} sender=${message.senderId?.toString() || "unknown"} decision=${decision} attachment=${mediaType || "none"} text=${JSON.stringify(message.message || "[無文字內容]")}`);
  if (decision !== "accepted") return;
  seenMessages.add(key);
  if (seenMessages.size > 1000) seenMessages.clear();
  void handleBatch(message);
}, new NewMessage({}));
console.log(`[duty] listening for possible problems in ${[...allowedChats].join(",")}; routing: ORCHESTRATE / FIX_PC / FIX_MOBILE; reports stay local; Telegram sending is disabled`);
