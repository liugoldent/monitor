import { request } from "node:https";

const origin = "https://nvshenn.bar";

function extractCookie(headers) {
  return (headers["set-cookie"] || []).map(value => value.split(";", 1)[0]).join("; ");
}

function mergeCookies(current, incoming) {
  const jar = new Map();
  for (const pair of [current, incoming].filter(Boolean).join("; ").split("; ")) {
    const equal = pair.indexOf("=");
    if (equal > 0) jar.set(pair.slice(0, equal), pair.slice(equal + 1));
  }
  return [...jar].map(([name, value]) => `${name}=${value}`).join("; ");
}

function httpRequest(url, { method = "GET", cookie = "", body = "" } = {}) {
  return new Promise((resolve, reject) => {
    const target = new URL(url, origin);
    if (target.origin !== origin) return reject(new Error("Unexpected issue site redirect"));
    const headers = { Cookie: cookie };
    if (body) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      headers["Content-Length"] = Buffer.byteLength(body);
    }
    const req = request(target, { method, headers, timeout: 10000 }, response => {
      const chunks = [];
      let size = 0;
      response.on("data", chunk => {
        size += chunk.length;
        if (size > 2_000_000) req.destroy(new Error("Issue response too large"));
        else chunks.push(chunk);
      });
      response.on("end", () => resolve({
        status: response.statusCode,
        location: response.headers.location,
        cookie: extractCookie(response.headers),
        html: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    req.on("timeout", () => req.destroy(new Error("Issue site timed out")));
    req.on("error", reject);
    req.end(body);
  });
}

function csrfToken(html) {
  return /<input[^>]+name=["']authenticity_token["'][^>]+value=["']([^"']+)["']/i.exec(html)?.[1]
    || /<meta[^>]+name=["']csrf-token["'][^>]+content=["']([^"']+)["']/i.exec(html)?.[1];
}

export async function readIssuePage(issue, username, password, transport = httpRequest) {
  if (!/^[0-9]{5}$/.test(issue)) throw new Error("Invalid issue number");
  const issueUrl = `${origin}/issues/${issue}`;
  let response = await transport(issueUrl);
  let cookie = response.cookie;
  if (response.status >= 300 && response.status < 400 && response.location) {
    const next = new URL(response.location, origin);
    if (next.origin !== origin) throw new Error("Issue redirects outside expected site");
    response = await transport(next.href, { cookie });
    cookie = mergeCookies(cookie, response.cookie);
  }
  if (response.status !== 200) throw new Error(`Issue site returned HTTP ${response.status}`);
  if (/<form[^>]+(?:id=["']login-form|action=["'][^"']*\/login)/i.test(response.html)) {
    if (!username || !password) throw new Error("Issue login credentials are missing");
    const token = csrfToken(response.html);
    if (!token) throw new Error("Issue login token is missing");
    const form = new URLSearchParams({ authenticity_token: token, username, password, login: "Login" });
    const login = await transport(`${origin}/login`, { method: "POST", cookie, body: form.toString() });
    cookie = mergeCookies(cookie, login.cookie);
    if (![200, 302, 303].includes(login.status)) throw new Error(`Issue login returned HTTP ${login.status}`);
    response = await transport(issueUrl, { cookie });
  }
  if (response.status !== 200 || /<form[^>]+(?:id=["']login-form|action=["'][^"']*\/login)/i.test(response.html)
    || !response.html.includes(issue)) {
    throw new Error("Issue page is not readable after login");
  }
  const heading = /<h2\b[^>]*>([\s\S]*?)<\/h2>/i.exec(response.html)?.[1]?.replace(/<[^>]*>/g, "") || "";
  if (!new RegExp(`#\\s*${issue}(?![0-9])`).test(heading)) {
    throw new Error("Issue page heading does not match requested number");
  }
  return { url: issueUrl, html: response.html };
}

export async function verifyIssueAccess(issue, username, password, transport = httpRequest) {
  return (await readIssuePage(issue, username, password, transport)).url;
}
