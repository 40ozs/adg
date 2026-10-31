#!/usr/bin/env node
/**
 * ADG-UX-REVIEW-NAMES-08: review scope display names, on the real local demo stack.
 *
 * Two web builds read the same API through this script's recording proxies:
 * this branch (WEB_URL → PROXY_PORT) and the Wave C baseline 559dbaf (BASELINE_WEB_URL →
 * BASELINE_PROXY_PORT). The baseline is also the "old client": it ignores `display_name`.
 *
 * Checks:
 * 1. API: identity scopes carry `display_name`, an unknown SID carries null, other kinds null;
 *    kind/key unchanged;
 * 2. Reviews list: identity scopes named, no "(name not loaded)" where the API sent a name, no
 *    SID in that row, one compact line; the unknown SID keeps the explicit fallback;
 * 3. the baseline build against the new API still renders the list (fallback, no errors);
 * 4. web-tier API calls for Reviews and the campaign page: the same on both builds;
 * 5. campaign page: reviewer scopes named, title, no fallback;
 * 6. 375 and 320 px: no sideways scroll; 7. no page errors.
 *
 * Read-only apart from development sign-ins. The unknown-SID campaign is seeded beforehand
 * (report §6).
 *
 *   WEB_URL=http://127.0.0.1:3080 BASELINE_WEB_URL=http://127.0.0.1:3081 API_URL=http://127.0.0.1:8080 \
 *   PROXY_PORT=8180 BASELINE_PROXY_PORT=8181 CHROMIUM_PATH=… [SCREENSHOT_DIR=…] [REPORT_JSON=…] \
 *   node review-names.mjs
 */
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3080").replace(/\/+$/, "");
const BASE_WEB = (process.env.BASELINE_WEB_URL ?? "http://127.0.0.1:3081").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, BASE_WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

/* Recording proxies: every API call each web tier makes (method, path, sorted query keys). */
function recorder(port) {
  const calls = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, API);
    const keys = [...new Set(url.searchParams.keys())].sort().join(",");
    const entry = { call: `${req.method} ${url.pathname}${keys ? `?${keys}` : ""}`, end: null };
    calls.push(entry);
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
  return { calls, server, ready: new Promise((resolve) => server.listen(port, "127.0.0.1", resolve)) };
}
const lane = recorder(Number(process.env.PROXY_PORT ?? 8180));
const baseline = recorder(Number(process.env.BASELINE_PROXY_PORT ?? 8181));
await Promise.all([lane.ready, baseline.ready]);

const results = [];
const measurements = {};
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
async function quiet(rec, ms = 600) {
  let seen;
  do {
    seen = rec.calls.length;
    await sleep(ms);
  } while (rec.calls.length !== seen || rec.calls.some((r) => r.end === null));
}
const sorted = (list) => list.map((r) => r.call).sort();

const login = await fetch(`${API}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "admin" }) });
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
const campaigns = (await (await fetch(`${API}/api/v1/governance/campaigns`, { headers: { Authorization: `Bearer ${token}` } })).json()).items;
const isIdentity = (scope) => scope.kind === "principal";
const named = campaigns.find((c) => c.scopes.length > 0 && c.scopes.every((s) => isIdentity(s) && s.display_name));
const unresolved = campaigns.find((c) => c.scopes.some((s) => isIdentity(s) && s.display_name === null));

// --- 1. API ---------------------------------------------------------------------------------
await check("API: identity scopes carry display_name; unknown SID null; other kinds null; tokens unchanged", async () => {
  expect(named, "no campaign whose identity scopes are all named");
  expect(unresolved, "no campaign with an unresolved identity scope (seed it first)");
  for (const c of campaigns) {
    for (const s of c.scopes) {
      expect(Object.keys(s).sort().join() === "display_name,key,kind", `${c.name}: scope keys ${Object.keys(s)}`);
      if (!isIdentity(s)) expect(s.display_name === null, `${c.name}: ${s.kind} scope has a name`);
      if (isIdentity(s)) expect(/^S-1-/.test(s.key), `${c.name}: identity key is not a SID: ${s.key}`);
    }
  }
  return `${named.name}: ${named.scopes.map((s) => s.display_name).join(", ")}; ${unresolved.name}: ${unresolved.scopes.map((s) => s.display_name ?? "null").join(", ")}`;
});

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
async function signedIn(web, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(60_000);
  const signIn = await context.request.post(`${web}/api/auth/dev-login`, { data: { username: "admin" } });
  expect(signIn.ok(), `sign-in: HTTP ${signIn.status()}`);
  const page = await context.newPage();
  const errors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => errors.push(`${page.url()}: ${error}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${page.url()}: ${message.text()}`);
  });
  return { context, page, errors, consoleErrors };
}
/** Direct document load; returns the web tier's API calls for it. */
async function load(session, rec, web, path) {
  await quiet(rec);
  const before = rec.calls.length;
  const response = await session.page.goto(`${web}${path}`, { waitUntil: "load" });
  expect(response?.status() === 200, `${path}: HTTP ${response?.status()}`);
  await session.page.waitForSelector("#main h1");
  await quiet(rec);
  return rec.calls.slice(before);
}
async function rowText(page, name) {
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) });
  expect((await row.count()) === 1, `row for ${name}: ${await row.count()}`);
  return { row, text: (await row.textContent()) ?? "" };
}
async function shot(page, name) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage: true });
}

const ours = await signedIn(WEB);
const theirs = await signedIn(BASE_WEB);

// --- 2. Reviews list ------------------------------------------------------------------------
const listCalls = await load(ours, lane, WEB, "/governance");
await shot(ours.page, "reviews-1440");
await check("Reviews: identity scopes named from the API, no fallback, no SID, one compact line", async () => {
  const { row, text } = await rowText(ours.page, named.name);
  const wanted = `By identity · ${named.scopes.map((s) => `Identity: ${s.display_name}`).join(", ")}`;
  const line = (await row.locator(".muted").allTextContents()).map((t) => t.replace(/\s+/g, " ").trim());
  expect(line.length === 1, `${line.length} muted lines`);
  expect(line[0] === wanted, `line is "${line[0]}", wanted "${wanted}"`);
  expect(!text.includes("name not loaded"), "fallback shown although the API sent names");
  for (const s of named.scopes) expect(!text.includes(s.key), `SID ${s.key} in the row`);
  const height = await row.evaluate((el) => el.getBoundingClientRect().height);
  measurements.namedRowHeight = height;
  return `"${line[0]}", row ${Math.round(height)} px`;
});
await check("Reviews: the unknown identity keeps the explicit fallback; its known neighbour is named", async () => {
  const { text } = await rowText(ours.page, unresolved.name);
  for (const s of unresolved.scopes) {
    const label = s.display_name ? `Identity: ${s.display_name}` : `Identity: ${s.key} (name not loaded)`;
    expect(text.includes(label), `missing "${label}"`);
  }
  return unresolved.scopes.map((s) => (s.display_name ? s.display_name : `${s.key} (name not loaded)`)).join(", ");
});
await check("Reviews: resource scopes unchanged", async () => {
  const resource = campaigns.filter((c) => c.scopes.every((s) => !isIdentity(s)));
  for (const c of resource) {
    const { text } = await rowText(ours.page, c.name);
    expect(text.includes("By resource · "), `${c.name}: no resource line`);
    expect(!text.includes("Identity:"), `${c.name}: identity label`);
  }
  return `${resource.length} resource campaigns`;
});

// --- 3. old client --------------------------------------------------------------------------
const baseListCalls = await load(theirs, baseline, BASE_WEB, "/governance");
await check("Baseline 559dbaf build reads the new API unchanged (ignores display_name)", async () => {
  const { text } = await rowText(theirs.page, named.name);
  for (const s of named.scopes) expect(text.includes(`Identity: ${s.key} (name not loaded)`), `baseline row lacks ${s.key} fallback`);
  return "renders the Wave C fallback, as before";
});
await shot(theirs.page, "reviews-baseline-1440");

// --- 4. request counts ----------------------------------------------------------------------
await check("Reviews: web-tier API calls identical to Wave C (no per-row request)", async () => {
  measurements.reviews = { lane: sorted(listCalls), baseline: sorted(baseListCalls) };
  expect(JSON.stringify(sorted(listCalls)) === JSON.stringify(sorted(baseListCalls)), `lane ${sorted(listCalls)} vs baseline ${sorted(baseListCalls)}`);
  const rows = await ours.page.locator("#main table tbody tr").count();
  measurements.reviewsRows = rows;
  return `${listCalls.length} calls on both for ${rows} rows: ${sorted(listCalls).join(" | ")}`;
});

// --- 5. campaign page -----------------------------------------------------------------------
const campaignPath = `/governance/campaigns/${named.campaign_id}`;
await check("Campaign: client move from Reviews; title; reviewer scopes named; no fallback", async () => {
  await quiet(lane);
  await ours.page.getByRole("link", { name: named.name, exact: true }).click();
  await ours.page.waitForURL(`**${campaignPath}`);
  await ours.page.waitForSelector("#reviewers");
  const title = await ours.page.title();
  expect(title === "Review campaign — ADG", `title ${title}`);
  const reviewers = (await ours.page.locator("section[aria-labelledby=reviewers]").textContent()) ?? "";
  const status = await (await fetch(`${API}/api/v1/governance/campaigns/${named.campaign_id}/status`, { headers: { Authorization: `Bearer ${token}` } })).json();
  const scoped = status.reviewers.filter((r) => r.scope && isIdentity(r.scope));
  expect(scoped.length > 0, "no identity-scoped reviewer");
  for (const r of scoped) {
    expect(r.scope.display_name, `status reviewer scope ${r.scope.key} has no display_name`);
    expect(reviewers.includes(`Identity: ${r.scope.display_name}`), `reviewers lack ${r.scope.display_name}`);
  }
  expect(!reviewers.includes("name not loaded"), "fallback in reviewers");
  await shot(ours.page, "campaign-1440");
  return `${title}; ${scoped.map((r) => r.scope.display_name).join(", ")}`;
});
const campaignCalls = await load(ours, lane, WEB, campaignPath);
const baseCampaignCalls = await load(theirs, baseline, BASE_WEB, campaignPath);
await check("Campaign: web-tier API calls identical to Wave C", async () => {
  measurements.campaign = { lane: sorted(campaignCalls), baseline: sorted(baseCampaignCalls) };
  expect(JSON.stringify(sorted(campaignCalls)) === JSON.stringify(sorted(baseCampaignCalls)), `lane ${sorted(campaignCalls)} vs baseline ${sorted(baseCampaignCalls)}`);
  return `${campaignCalls.length} calls on both`;
});

// --- 6. narrow widths -----------------------------------------------------------------------
for (const width of [375, 320]) {
  await check(`${width}px: Reviews and campaign do not scroll sideways`, async () => {
    const narrow = await signedIn(WEB, { width, height: 800 });
    const over = [];
    for (const path of ["/governance", campaignPath]) {
      await load(narrow, lane, WEB, path);
      const extra = await narrow.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (extra > 0) over.push(`${path} +${extra}px`);
      if (path === "/governance") await shot(narrow.page, `reviews-${width}`);
    }
    ours.errors.push(...narrow.errors);
    await narrow.context.close();
    expect(over.length === 0, over.join(", "));
  });
}

// --- 7. errors ------------------------------------------------------------------------------
await check("No page errors (this branch)", async () => {
  expect(ours.errors.length === 0, ours.errors.join(" | "));
  const other = ours.consoleErrors.filter((e) => !/favicon\.ico|404/.test(e));
  expect(other.length === 0, other.join(" | "));
  return `console: ${ours.consoleErrors.length} (favicon 404 only)`;
});

await browser.close();
lane.server.close();
baseline.server.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
if (REPORT) writeFileSync(REPORT, JSON.stringify({ results, measurements }, null, 2));
process.exit(failed ? 1 : 0);
