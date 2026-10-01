import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const services = [
  ["duty", new URL("./index.js", import.meta.url)],
  ["qa", new URL("./qa_dispatch.js", import.meta.url)],
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
