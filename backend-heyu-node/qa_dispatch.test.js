import assert from "node:assert/strict";
import { parseQaDispatch, buildQaPrompt } from "./qa_dispatch_core.js";
import { verifyIssueAccess } from "./qa_issue_access.js";

const sample = "【派單】\n工單：#19366 【派單】移动端搜索工具栏&搜索结果页优化\n涉及端別：H5\n指派技術：@Anforderungsfluss \n注意事項：";
assert.equal(parseQaDispatch(sample), "19366");
assert.equal(parseQaDispatch(sample.replace("@Anforderungsfluss", "@someone")), null);
assert.equal(parseQaDispatch(sample.replace("#19366", "#193660")), null);
assert.equal(parseQaDispatch(sample.replace("#19366", "#19366A")), null);
assert.equal(parseQaDispatch(sample.replace("【派單】", "一般通知")), "19366");
assert.equal(parseQaDispatch("工單：#19366 @Anforderungsfluss"), null);
const prompt = buildQaPrompt("19366", "user", "secret", "/xingba/project");
assert.match(prompt, /fix\/19366/);
assert.match(prompt, /唯一專案是 \/xingba\/project/);
assert.match(prompt, /RESULT: MR_CREATED/);
assert.throws(() => buildQaPrompt("193660", "user", "secret", "/xingba/project"));

let calls = 0;
const transport = async (url, options = {}) => {
  calls++;
  if (calls === 1) return { status: 302, location: "/login", cookie: "session=one", html: "" };
  if (calls === 2) return { status: 200, cookie: "", html: '<form id="login-form"><input name="authenticity_token" value="csrf"></form>' };
  if (calls === 3) {
    assert.equal(url, "https://nvshenn.bar/login");
    assert.equal(options.method, "POST");
    assert.match(options.body, /username=user/);
    return { status: 302, cookie: "session=two", html: "" };
  }
  assert.equal(options.cookie, "session=two");
  return { status: 200, html: "<h2>工單 #19366</h2>" };
};
assert.equal(await verifyIssueAccess("19366", "user", "secret", transport), "https://nvshenn.bar/issues/19366");
await assert.rejects(() => verifyIssueAccess("19366", "user", "secret", async () => { throw new Error("DNS failure"); }), /DNS failure/);
console.log("qa dispatch checks passed");
