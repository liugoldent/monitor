import { extractMrTitle } from "./mr_review_core.js";

export async function verifyMrAccess(url, transport = fetch) {
  const target = new URL(url);
  if (target.origin !== "https://gitlab.k8s.adwqa.com" || !/^\/xingba\/xingba_pcweb_vue3\/-\/merge_requests\/[1-9][0-9]*$/.test(target.pathname)) {
    throw new Error("Unexpected MR URL");
  }
  const response = await transport(url, { redirect: "follow", signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`MR page returned HTTP ${response.status}`);
  const finalUrl = new URL(response.url || url);
  if (finalUrl.origin !== target.origin || finalUrl.pathname !== target.pathname) {
    throw new Error("MR page redirected away from the requested MR (login or access required)");
  }
  const html = await response.text();
  if (!html.includes("merge-request") && !html.includes("merge_request") && !html.includes("Merge request")) {
    throw new Error("MR page content could not be verified");
  }
  const number = target.pathname.split("/").at(-1);
  const title = extractMrTitle(html, number);
  if (!title) throw new Error("MR title could not be read");
  return { url, title };
}
