import assert from "node:assert/strict";
import { buildMrReviewPrompt, extractIssueNumberFromMrTitle, extractIssueRequirement, extractMrTitle, isMrReviewTrigger, parsePreviousMr } from "./mr_review_core.js";
import { verifyMrAccess } from "./mr_access.js";
import { readIssuePage } from "./qa_issue_access.js";

const trigger = "@Anforderungsfluss 累了嗎，放下手邊工作，放個一天假，明天再看 MR 吧！";
const url = "https://gitlab.k8s.adwqa.com/xingba/xingba_pcweb_vue3/-/merge_requests/966";
assert.equal(isMrReviewTrigger(trigger), true);
assert.equal(isMrReviewTrigger("明天再看 MR 吧！"), false);
assert.deepEqual(parsePreviousMr(`mr:pc\n\n[看 MR](${url})`), { number: "966", url });
assert.deepEqual(parsePreviousMr(`mr:pc\n\n[${url}](${url})`), { number: "966", url });
assert.equal(parsePreviousMr(`mr:h5\n${url}`), null);
assert.equal(parsePreviousMr("mr:pc\nhttps://evil.example/merge_requests/966"), null);
assert.equal(extractMrTitle("<title>fix: #19349 修正主題發布時間 (!966) · Merge requests · GitLab</title>", "966"), "fix: #19349 修正主題發布時間");
assert.equal(extractIssueNumberFromMrTitle("fix: #19349 修正主題發布時間"), "19349");
assert.equal(extractIssueNumberFromMrTitle("fix: no issue number"), null);
assert.equal(extractIssueNumberFromMrTitle("fix: #19349 and #19474"), null);
assert.equal(extractIssueNumberFromMrTitle("fix: #193490"), null);
const requirement = extractIssueRequirement('<div class="description"><div class="contextual">引用</div><p>描述</p><div class="wiki"><p>今天内且≥1小时：<code>X小时前</code></p><ul><li>昨天 &amp; 前天</li></ul></div></div>');
assert.match(requirement, /今天内且≥1小时：X小时前/);
assert.match(requirement, /昨天 & 前天/);
const prompt = buildMrReviewPrompt({ number: "966", url, repoDir: "/tmp/repo", mrTitle: "fix: #19349 修正主題發布時間", issue: { url: "https://nvshenn.bar/issues/19349", requirement } });
assert.match(prompt, /RESULT: PASS/);
assert.match(prompt, /pipeline/);
assert.match(prompt, /逐條比對工單需求/);
assert.match(prompt, /issues\/19349/);
assert.deepEqual(await readIssuePage("19349", "user", "secret", async () => ({
  status: 200,
  html: '<h2>工單 #19349</h2><div class="description"><div class="wiki"><p>顯示時間規則</p></div></div>',
})), {
  url: "https://nvshenn.bar/issues/19349",
  html: '<h2>工單 #19349</h2><div class="description"><div class="wiki"><p>顯示時間規則</p></div></div>',
});
await assert.rejects(() => readIssuePage("19349", "user", "secret", async () => ({
  status: 200,
  html: '<h2>工單 #19474</h2><p>參考 19349</p>',
})), /heading does not match/);
assert.deepEqual(await verifyMrAccess(url, async (_, options) => {
  assert.equal(options.redirect, "follow");
  return { ok: true, status: 200, url, text: async () => '<title>fix: #19349 修正主題發布時間 (!966) · Merge requests · GitLab</title><div class="merge-request">MR</div>' };
}), { url, title: "fix: #19349 修正主題發布時間" });
await assert.rejects(() => verifyMrAccess(url, async () => ({ ok: true, status: 200, url: "https://gitlab.k8s.adwqa.com/users/sign_in", text: async () => "login" })), /redirected/);
await assert.rejects(() => verifyMrAccess(url, async () => { throw new Error("timeout"); }), /timeout/);
console.log("mr review checks passed");
