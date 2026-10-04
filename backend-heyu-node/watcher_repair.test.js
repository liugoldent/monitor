import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { repairProblem } from "./watcher_repair.js";

const run = promisify(execFile);
const root = await mkdtemp(path.join(os.tmpdir(), "duty-repair-"));
try {
  const repoDir = path.join(root, "repo");
  await mkdir(repoDir);
  await run("git", ["init", repoDir]);
  await writeFile(path.join(repoDir, "fixture.txt"), "original");
  await run("git", ["-C", repoDir, "add", "."]);
  await run("git", ["-C", repoDir, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]);
  const codexBin = path.join(root, "fake-codex");
  await writeFile(codexBin, '#!/bin/sh\ncat >/dev/null\nprintf "changed" > fixture.txt\nprintf "RESULT: FIXED\\nfixture verified\\n"\n', { mode: 0o700 });
  const basePath = path.join(root, "reports", "fixture");
  await repairProblem({ codexBin, repoDir, basePath, context: "fixture", report: "fixture", images: [], skill: "fixture" });
  const job = JSON.parse(await readFile(`${basePath}.repair-job.json`, "utf8"));
  assert.equal(job.status, "FIXED");
  assert.equal(await readFile(path.join(job.worktree, "fixture.txt"), "utf8"), "changed");
  assert.equal(await readFile(path.join(repoDir, "fixture.txt"), "utf8"), "original");
  assert.match(await readFile(`${basePath}.repair.txt`, "utf8"), /RESULT: FIXED/);
  console.log("watcher_repair isolation checks passed (mock Codex)");
} finally {
  await rm(root, { recursive: true, force: true });
}
