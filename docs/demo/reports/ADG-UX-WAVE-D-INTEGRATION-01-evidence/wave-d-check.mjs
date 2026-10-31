#!/usr/bin/env node
/**
 * ADG-UX-WAVE-D-INTEGRATION-01: the cross-lane checks no source gate makes, because each Wave D
 * lane ran without the other two. NAV-07's review-item-nav.mjs covers the item move in depth,
 * REVIEW-NAMES-08's review-names.mjs the scope names against the Wave C build, and the titles
 * gate (frontend/cloudflare/checks/titles.mjs) the full title journey with TEST-STABILITY-09's
 * date anchor. This walks one signed-in tab through all three at once:
 *
 *  1. Reviews loads;  2. identity scopes show their current name, the unresolved one the fallback;
 *  3. a click opens the named campaign;  4. its reviewers use the current scope names;
 *  5. a click opens a review item: no document load, "Review item — ADG", no layout re-read and
 *     no repeated read;  6. Back and  7. Forward, client-side, titles restored;
 *  8. the Changes journey reaches History and "Why access changed" while every window read is
 *     first moved to SIMULATED_NOW (a future clock) and then anchored to DATASET_NOW;
 *  9. Reviews and campaign make the Wave C web-tier calls and no identity lookup, in the web tier
 *     or the browser;  10–14. at 1440/1024/768/375/320 px Reviews → campaign → item stays
 *     client-side and no page scrolls sideways;  15. no page error, no hydration error.
 *
 * Topology: Next.js ADG_INTERNAL_API_URL → this script's proxy on $PROXY_PORT → the API.
 * Read-only apart from development sign-ins; needs setup-fixtures.sh's campaigns first.
 *
 *   WEB_URL=http://127.0.0.1:3110 API_URL=http://127.0.0.1:8110 PROXY_PORT=8210 \
 *   CHROMIUM_PATH=… [SIMULATED_NOW=2028-01-01T00:00:00Z] [REPORT_JSON=…] node wave-d-check.mjs
 */
import { writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const DATASET_NOW = Date.parse(process.env.DATASET_NOW ?? "2026-10-01T12:00:00Z");
const SIMULATED_NOW = Date.parse(process.env.SIMULATED_NOW ?? "2028-01-01T00:00:00Z");
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
const SUFFIX = " — ADG";
const WINDOW_READS = new Set(["/api/v1/changes", "/api/v1/changes/summary"]);
const IDENTITY_LOOKUP = /\/(identities|principals)\b/;

/* Proxy: records each web-tier call; moves each window read to SIMULATED_NOW, then anchors it. */
const calls = [];
let simulated = 0;
let anchored = 0;
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const keys = [...new Set(url.searchParams.keys())].sort().join(",");
  const entry = { call: `${req.method} ${url.pathname}${keys ? `?${keys}` : ""}`, end: null };
  calls.push(entry);
  if (WINDOW_READS.has(url.pathname)) {
    const from = Date.parse(url.searchParams.get("from"));
    const to = Date.parse(url.searchParams.get("to"));
    if (!Number.isNaN(from) && !Number.isNaN(to)) {
      const offset = SIMULATED_NOW - Date.now(); // the window the page would ask for on SIMULATED_NOW
      const [simFrom, simTo] = [from + offset, to + offset];
      simulated++;
      // TEST-STABILITY-09's anchor, computed from the (simulated) request's own `to`.
      const shift = simTo > DATASET_NOW ? simTo - DATASET_NOW : 0;
      if (shift) anchored++;
      url.searchParams.set("from", new Date(simFrom - shift).toISOString());
      url.searchParams.set("to", new Date(simTo - shift).toISOString());
    }
  }
  const upstream = http.request(url, { method: req.method, headers: { ...req.headers, host: new URL(API).host } }, (up) => {
    res.writeHead(up.statusCode, up.headers);
    up.pipe(res);
    up.on("end", () => (entry.end = Date.now()));
  });
  upstream.on("error", () => {
    entry.end = Date.now();
    if (!res.headersSent) res.writeHead(502);
    res.end();
  });
  req.pipe(upstream);
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

const results = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    const detail = String(error?.message ?? error).split("\n")[0];
    results.push({ name, ok: false, detail });
    console.log(`FAIL  ${name} — ${detail}`);
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function quiet(ms = 600) {
  let seen;
  do {
    seen = calls.length;
    await sleep(ms);
  } while (calls.length !== seen || calls.some((c) => c.end === null));
}
const since = (mark) => calls.slice(mark).map((c) => c.call);

/* The campaigns setup-fixtures.sh made, as the API names them. */
const login = await fetch(`${API}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "admin" }) });
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
const apiGet = async (path) => (await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } })).json();
const campaigns = (await apiGet("/api/v1/governance/campaigns")).items;
const isIdentity = (scope) => scope.kind === "principal";
const named = campaigns.find((c) => c.item_count > 0 && c.scopes.length > 0 && c.scopes.every((s) => isIdentity(s) && s.display_name));
const unresolved = campaigns.find((c) => c.scopes.some((s) => isIdentity(s) && s.display_name === null));
if (!named || !unresolved) {
  console.error("Run setup-fixtures.sh first: no named identity campaign with items, or no unresolved one.");
  process.exit(2);
}
const campaignPath = `/governance/campaigns/${named.campaign_id}`;

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const pageErrors = [];
const consoleErrors = [];
async function tab(viewport) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(60_000);
  const signIn = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username: "admin" } });
  expect(signIn.ok(), `sign-in: HTTP ${signIn.status()}`);
  const page = await context.newPage();
  const documents = [];
  const browserRequests = [];
  page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(`${page.url()}: ${m.text()}`));
  page.on("request", (request) => {
    if (request.frame() === page.mainFrame() && request.resourceType() === "document") documents.push(request.url());
    browserRequests.push(new URL(request.url()).pathname);
  });
  return { context, page, documents, browserRequests };
}
const titleBecomes = async (page, want) => {
  await page.waitForFunction((t) => document.title === t, want, { timeout: 10_000 }).catch(() => {});
  return page.title();
};
async function clickTo(t, locator, title) {
  const before = t.page.url();
  expect((await locator.count()) > 0, `no link for "${title}" on ${before}`);
  await locator.first().click();
  await t.page.waitForURL((url) => url.href !== before);
  const got = await titleBecomes(t.page, `${title}${SUFFIX}`);
  expect(got === `${title}${SUFFIX}`, `${t.page.url()}: title "${got}", wanted "${title}${SUFFIX}"`);
}
const noReload = async (t) => {
  const kept = await t.page.evaluate(() => window.__adgNoReload === true);
  expect(kept && t.documents.length === 0, `document loaded (${t.documents.join(", ")})`);
};
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const itemLink = (page) => page.locator('#main a[href^="/governance/items/"]');

const t = await tab({ width: 1440, height: 900 });
const { page } = t;
const recorded = {};

await check("1. Reviews loads", async () => {
  await quiet();
  const mark = calls.length;
  await page.goto(`${WEB}/governance`, { waitUntil: "networkidle" });
  await quiet();
  recorded.reviews = since(mark).sort();
  const title = await titleBecomes(page, `Reviews${SUFFIX}`);
  expect(title === `Reviews${SUFFIX}`, `title "${title}"`);
  await page.evaluate(() => (window.__adgNoReload = true));
  t.documents.length = 0;
  return `${await page.locator("#main table tbody tr").count()} campaigns`;
});

await check("2. Reviews: identity scopes show the current display name; unresolved keeps the fallback", async () => {
  const row = (name) => page.getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) });
  const text = (await row(named.name).textContent()) ?? "";
  for (const s of named.scopes) {
    expect(text.includes(`Identity: ${s.display_name}`), `"${named.name}" lacks "Identity: ${s.display_name}"`);
    expect(!text.includes(s.key), `"${named.name}" shows SID ${s.key}`);
  }
  expect(!text.includes("name not loaded"), "fallback beside a name the API sent");
  const other = (await row(unresolved.name).textContent()) ?? "";
  const missing = unresolved.scopes.find((s) => s.display_name === null);
  expect(other.includes(`Identity: ${missing.key} (name not loaded)`), `"${unresolved.name}" lacks the fallback`);
  return `${named.scopes.map((s) => s.display_name).join(", ")}; unresolved ${missing.key.split("-").pop()} → fallback`;
});

await check("3. Reviews → campaign by click: client-side, \"Review campaign — ADG\"", async () => {
  await clickTo(t, page.getByRole("link", { name: named.name, exact: true }), "Review campaign");
  await page.waitForSelector("#reviewers");
  await noReload(t);
  return `${named.name}, ${named.item_count} items`;
});

await check("4. Campaign: reviewer scopes use the current display names", async () => {
  const reviewers = (await page.locator("section[aria-labelledby=reviewers]").textContent()) ?? "";
  const status = await apiGet(`/api/v1/governance/campaigns/${named.campaign_id}/status`);
  const scoped = status.reviewers.filter((r) => r.scope && isIdentity(r.scope));
  expect(scoped.length > 0, "no identity-scoped reviewer");
  for (const r of scoped) expect(reviewers.includes(`Identity: ${r.scope.display_name}`), `reviewers lack ${r.scope.display_name}`);
  expect(!reviewers.includes("name not loaded") && !/S-1-5-21-/.test(reviewers), "SID or fallback in reviewers");
  return scoped.map((r) => r.scope.display_name).join(", ");
});

await check("5. Campaign → item by click: no document load, \"Review item — ADG\", no layout re-read, no repeated read", async () => {
  await quiet();
  const mark = calls.length;
  await clickTo(t, itemLink(page), "Review item");
  await page.waitForSelector("#main h1");
  await quiet();
  await noReload(t);
  const move = since(mark);
  recorded.itemMove = move;
  expect(!move.some((c) => /\/auth\/config$|\/runtime$/.test(c)), `layout re-read: ${move.join(" | ")}`);
  expect(new Set(move).size === move.length, `repeated read: ${move.join(" | ")}`);
  return `${move.length} API reads: ${move.join(" | ")}`;
});

await check("6. Back: the campaign, its title, no document load", async () => {
  await page.goBack();
  await page.waitForURL(`**${campaignPath}`);
  const got = await titleBecomes(page, `Review campaign${SUFFIX}`);
  expect(got === `Review campaign${SUFFIX}`, `title "${got}"`);
  await noReload(t);
});

await check("7. Forward: the item, its title, no document load", async () => {
  await page.goForward();
  await page.waitForURL("**/governance/items/**");
  const got = await titleBecomes(page, `Review item${SUFFIX}`);
  expect(got === `Review item${SUFFIX}`, `title "${got}"`);
  await noReload(t);
});

await check(`8. Changes journey on a ${new Date(SIMULATED_NOW).toISOString().slice(0, 10)} clock: History and "Why access changed", anchored, client-side`, async () => {
  const nav = page.getByRole("navigation", { name: "Primary" });
  const before = { simulated, anchored };
  await clickTo(t, nav.getByRole("link", { name: "Changes", exact: true }), "Changes");
  await clickTo(t, page.locator('#main a[href="/changes?days=90"]'), "Changes");
  await clickTo(t, page.locator('#main a[href^="/changes?kind="]'), "History");
  await page.goBack();
  await titleBecomes(page, `Changes${SUFFIX}`);
  await clickTo(t, page.locator('#main a[href^="/changes/impact?"]'), "Why access changed");
  await noReload(t);
  const moved = simulated - before.simulated;
  expect(moved > 0 && anchored - before.anchored === moved, `window reads: ${moved} simulated, ${anchored - before.anchored} anchored`);
  return `${moved} window reads moved to ${new Date(SIMULATED_NOW).toISOString()} and anchored to ${new Date(DATASET_NOW).toISOString()}`;
});

await check("9. Reviews and campaign: Wave C web-tier calls, no identity lookup in the web tier or the browser", async () => {
  const wanted = ["GET /api/v1/governance/campaigns?limit", "GET /api/v1/governance/queue", "GET /auth/config", "GET /auth/me", "GET /runtime"];
  expect(JSON.stringify(recorded.reviews) === JSON.stringify(wanted), `Reviews: ${recorded.reviews.join(" | ")}`);
  await quiet();
  const mark = calls.length;
  await page.goto(`${WEB}${campaignPath}`, { waitUntil: "networkidle" });
  await quiet();
  recorded.campaign = since(mark).sort();
  expect(recorded.campaign.length === 6, `campaign: ${recorded.campaign.length} calls (Wave C: 6): ${recorded.campaign.join(" | ")}`);
  const lookups = [...recorded.reviews, ...recorded.campaign, ...recorded.itemMove].filter((c) => IDENTITY_LOOKUP.test(c));
  expect(lookups.length === 0, `web-tier identity lookups: ${lookups.join(" | ")}`);
  const fromBrowser = t.browserRequests.filter((p) => p.startsWith("/api/") && IDENTITY_LOOKUP.test(p));
  expect(fromBrowser.length === 0, `browser identity lookups: ${fromBrowser.join(" | ")}`);
  return `Reviews 5, campaign 6; 0 identity lookups over ${t.browserRequests.length} browser requests`;
});

let n = 10;
for (const width of [1440, 1024, 768, 375, 320]) {
  await check(`${n++}. ${width}px: Reviews → campaign → item stays client-side; no page scrolls sideways`, async () => {
    const w = await tab({ width, height: 900 });
    const over = [];
    await w.page.goto(`${WEB}/governance`, { waitUntil: "networkidle" });
    await w.page.evaluate(() => (window.__adgNoReload = true));
    w.documents.length = 0;
    if ((await overflow(w.page)) > 0) over.push(`Reviews +${await overflow(w.page)}px`);
    await clickTo(w, w.page.getByRole("link", { name: named.name, exact: true }), "Review campaign");
    await w.page.waitForSelector("#reviewers");
    if ((await overflow(w.page)) > 0) over.push(`campaign +${await overflow(w.page)}px`);
    await clickTo(w, itemLink(w.page), "Review item");
    await w.page.waitForSelector("#main h1");
    if ((await overflow(w.page)) > 0) over.push(`item +${await overflow(w.page)}px`);
    await noReload(w);
    await w.context.close();
    expect(over.length === 0, over.join(", "));
  });
}

await check(`${n}. no page error, no hydration error`, async () => {
  expect(pageErrors.length === 0, pageErrors.join(" | "));
  const hydration = consoleErrors.filter((e) => /hydrat|did not match|#418/i.test(e));
  expect(hydration.length === 0, hydration.join(" | "));
  return `0 page errors, 0 hydration; console errors: ${consoleErrors.length ? consoleErrors.join(" | ") : "none"}`;
});

await browser.close();
proxy.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (REPORT) writeFileSync(REPORT, JSON.stringify({ results, recorded, simulated, anchored }, null, 2));
process.exit(failed ? 1 : 0);
