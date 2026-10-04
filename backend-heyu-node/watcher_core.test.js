import assert from "node:assert/strict";
import { formatMessages, parseDecision, parseRoute, buildAnalysisPrompt } from "./watcher_core.js";

const messages = [
  { id: 3, date: new Date("2026-09-29T01:02:00Z"), senderId: 9n, message: "後續" },
  { id: 2, date: new Date("2026-09-29T01:01:00Z"), senderId: 8n, message: "QA 研发 看看" },
  { id: 2, date: new Date("2026-09-29T01:01:00Z"), senderId: 8n, message: "QA 研发 看看" },
];
const context = formatMessages(messages, 2);
assert.equal(context.match(/message_id=2/g)?.length, 1);
assert.ok(context.indexOf("message_id=2") < context.indexOf("message_id=3"));
assert.match(context, /message_id=2 sender_id=8 \[本批起點\]/);
assert.equal(parseDecision("CLASSIFICATION: PROBLEM\n有異常"), "PROBLEM");
assert.equal(parseDecision("CLASSIFICATION: NO_PROBLEM\n閒聊"), "NO_PROBLEM");
assert.equal(parseDecision("無法判定"), "NEEDS_REVIEW");
const prompt = buildAnalysisPrompt("/only/project", context);
assert.match(prompt, /只判斷 \[本批起點\] 與其後的新訊息/);
assert.match(prompt, /boss-question-orchestrator/);
assert.match(prompt, /不要實際發送/);
console.log("watcher_core checks passed");
assert.equal(parseRoute("CLASSIFICATION: PROBLEM\nROUTE: FIX_PC\n根因"), "FIX_PC");
assert.equal(parseRoute("CLASSIFICATION: PROBLEM\nROUTE: FIX_MOBILE\n根因"), "FIX_MOBILE");
assert.equal(parseRoute("CLASSIFICATION: NO_PROBLEM\nROUTE: FIX_PC"), "ORCHESTRATE");
assert.equal(parseRoute("CLASSIFICATION: NEEDS_REVIEW\nROUTE: FIX_PC"), "ORCHESTRATE");
assert.equal(parseRoute("CLASSIFICATION: PROBLEM\n沒有路由"), "ORCHESTRATE");
assert.equal(parseRoute("CLASSIFICATION: PROBLEM\nROUTE: FIX_OTHER"), "ORCHESTRATE");
