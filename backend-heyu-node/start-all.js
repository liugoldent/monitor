import { spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const serviceDir = fileURLToPath(new URL(".", import.meta.url));
const lockPath = path.join(serviceDir, "runtime", "watcher.pid");
mkdirSync(path.dirname(lockPath), { recursive: true });

for (;;) {
  try {
    const fd = openSync(lockPath, "wx", 0o600);
    try {
      writeFileSync(fd, String(process.pid));
    } finally {
      closeSync(fd);
    }
    break;
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const previousPid = Number(readFileSync(lockPath, "utf8"));
    if (!Number.isInteger(previousPid) || previousPid <= 0) {
      throw new Error(`Monitor lock has no valid PID: ${lockPath}`);
    }
    let running = true;
    try {
      process.kill(previousPid, 0);
    } catch (probeError) {
      if (probeError.code === "ESRCH") running = false;
      else if (probeError.code !== "EPERM") throw probeError;
    }
    if (running) throw new Error(`Monitor is already running (pid=${previousPid})`);
    unlinkSync(lockPath);
  }
}

process.on("exit", () => {
  try {
    if (Number(readFileSync(lockPath, "utf8")) === process.pid) unlinkSync(lockPath);
  } catch (error) {
    if (error.code !== "ENOENT") console.error(`[all] lock cleanup failed: ${error.message}`);
  }
});

const services = [
  ["duty", new URL("./index.js", import.meta.url)],
  ["qa", new URL("./qa_dispatch.js", import.meta.url)],
  ["mr", new URL("./mr_review.js", import.meta.url)],
];
const children = new Map();
let stopping = false;
let killTimer;

function stop(signal = "SIGTERM", exitCode = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = exitCode;
  for (const child of children.values()) {
    if (child.exitCode === null) child.kill(signal);
  }
  killTimer = setTimeout(() => {
    for (const child of children.values()) {
      if (child.exitCode === null) child.kill("SIGKILL");
    }
  }, 5000);
}

for (const [name, script] of services) {
  const child = spawn(process.execPath, [fileURLToPath(script)], {
    cwd: fileURLToPath(new URL(".", import.meta.url)),
    stdio: "inherit",
    env: process.env,
  });
  children.set(name, child);
  console.log(`[all] ${name} started (pid=${child.pid})`);
  child.on("error", error => {
    console.error(`[all] ${name} failed: ${error.message}`);
    stop("SIGTERM", 1);
  });
  child.on("close", (code, signal) => {
    children.delete(name);
    if (!stopping) {
      console.error(`[all] ${name} exited (${signal || code}); stopping the other service`);
      stop("SIGTERM", code || 1);
    }
    if (children.size === 0) clearTimeout(killTimer);
  });
}

process.on("SIGINT", () => stop("SIGINT", 130));
process.on("SIGTERM", () => stop("SIGTERM", 143));
