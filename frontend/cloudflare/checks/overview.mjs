#!/usr/bin/env node
/**
 * Local browser gate for the Overview (ADG-UX-06B): the real page, rendered by the real Next.js
 * server against the real API and the Genesee Press demo dataset, driven with headless Chromium.
 *
 *   FastAPI (python -m app, ADG_RUNTIME_MODE=demo) 127.0.0.1:8000
 *     ◄── this script's recording proxy 127.0.0.1:$PROXY_PORT  (Next.js ADG_INTERNAL_API_URL)
 *           ◄── Next.js standalone server.js $WEB_URL  ◄── Chromium
 *
 *   WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 PROXY_PORT=8100 \
 *   CHROMIUM_PATH=… SCREENSHOT_DIR=… REPORT_JSON=… node cloudflare/checks/overview.mjs
 *
 * Why a proxy: the Overview is a server component, so its seven summary reads leave the Next.js
 * server, never the browser. The proxy records each one (path, start, end, status), which is how
 * this gate checks permission gating and fan-out at the request level. It can also delay, fail or
 * rewrite one path's response; the states the demo dataset does not contain (a complete
 * evaluation, zero findings, never evaluated, healthy collection, a past-due campaign) are made
 * that way, without touching the dataset or the backend. Every substituted state is named
 * "fixture" in its check.
 *
 * Writes: development sign-ins, and — when the `reviewer` queue is empty — one review campaign
 * created through the API as `governance`, as the demo mutation policy allows. Refuses to run
 * against anything but loopback. Needs the `playwright` package (NODE_PATH works) and
 * ADG_DEV_AUTH_USERS with viewer, auditor, reviewer, governance, planner and `nobody` (a
 * `remediator`-only account, which holds no capability). Exits non-zero on any failed check.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

// An ESM import ignores NODE_PATH; require() honors it, so a global install also works.
const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
const WARM_RUNS = Number(process.env.WARM_RUNS ?? 5);
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}. This gate writes to its database.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

/* ------------------------------------------------------------------ recording proxy */

/** Every API request the web tier made, in order. */
const requests = [];
/**
 * pathname → { status } | { delayMs } | { transform(json) → json } | { shiftMs }, applied by the
 * proxy. `shiftMs` moves a request's `from`/`to` back in time, so a window that ends now reads
 * the dataset's own last week (its scans are dated, not "now"), and every page asking the same
 * question gets the same shift.
 */
const faults = new Map();
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const fault = faults.get(url.pathname) ?? null;
  const entry = { method: req.method, path: url.pathname, query: url.search, start: performance.now(), end: null, status: null, fault: fault ? Object.keys(fault)[0] : null };
  requests.push(entry);
  for (const name of fault?.shiftMs ? ["from", "to"] : []) {
    const value = url.searchParams.get(name);
    if (value) url.searchParams.set(name, new Date(Date.parse(value) - fault.shiftMs).toISOString());
  }
  const finish = (status) => {
    entry.status = status;
    entry.end = performance.now();
  };
  const forward = () => {
    if (fault?.status) {
      res.writeHead(fault.status, { "content-type": "application/json" });
      res.end(JSON.stringify({ detail: "Injected by the overview gate." }));
      return finish(fault.status);
    }
    const headers = { ...req.headers, host: new URL(API).host };
    delete headers["accept-encoding"]; // identity, so a fixture can rewrite the body
    const upstream = http.request(url, { method: req.method, headers }, (up) => {
      if (!fault?.transform || up.statusCode !== 200) {
        res.writeHead(up.statusCode, up.headers);
        up.pipe(res);
        up.on("end", () => finish(up.statusCode));
        return;
      }
      const chunks = [];
      up.on("data", (chunk) => chunks.push(chunk));
      up.on("end", () => {
        const body = Buffer.from(JSON.stringify(fault.transform(JSON.parse(Buffer.concat(chunks).toString("utf8")))));
        const out = { ...up.headers, "content-length": String(body.length) };
        delete out.etag;
        res.writeHead(200, out);
        res.end(body);
        finish(200);
      });
    });
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
      finish(502);
    });
    req.pipe(upstream);
  };
  if (fault?.delayMs) setTimeout(forward, fault.delayMs);
  else forward();
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

/* ------------------------------------------------------------------ harness */

const results = [];
const measurements = {};
const pageErrors = [];
const consoleErrors = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    results.push({ name, ok: false, detail: String(error?.message ?? error).split("\n")[0] });
    console.log(`FAIL  ${name} — ${String(error?.message ?? error).split("\n")[0]}`);
  } finally {
    faults.clear();
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}
const squash = (value) => value.replace(/\s+/g, " ").trim();
const n = (value) => value.toLocaleString("en-US");

async function apiLogin(username) {
  const response = await fetch(`${API}/auth/dev/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username }),
  });
  expect(response.ok, `API sign-in as ${username}: HTTP ${response.status}`);
  return response.json();
}
async function apiToken(username) {
  return (await apiLogin(username)).access_token;
}
async function api(token, path, init = {}) {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json", ...(init.headers ?? {}) },
  });
  const body = await response.text();
  return { status: response.status, body: body ? JSON.parse(body) : null };
}

for (let attempt = 0; ; attempt += 1) {
  const ready = await fetch(`${WEB}/login`).then((r) => r.ok, () => false);
  const apiReady = await fetch(`${API}/health/ready`).then((r) => r.ok, () => false);
  if (ready && apiReady) break;
  expect(attempt < 120, "the web tier or the API never became ready");
  await new Promise((resolve) => setTimeout(resolve, 1000));
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const contexts = new Map();
/** A signed-in page for `username`, one browser context per account. */
async function pageFor(username, viewport = { width: 1440, height: 900 }) {
  const key = `${username}@${viewport.width}`;
  if (!contexts.has(key)) {
    const context = await browser.newContext({ viewport });
    context.setDefaultTimeout(60_000);
    const login = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username } });
    expect(login.ok(), `sign-in as ${username}: HTTP ${login.status()}`);
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(`${username}: ${error}`));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(`${username}: ${message.text()}`);
    });
    page.on("response", (response) => {
      if (response.status() >= 400) consoleErrors.push(`${username}: HTTP ${response.status()} ${new URL(response.url()).pathname}`);
    });
    contexts.set(key, page);
  }
  return contexts.get(key);
}

const OVERVIEW_READS = [
  "/api/v1/collection/status",
  "/runtime",
  "/api/v1/risks/summary",
  "/api/v1/risks/rules",
  "/api/v1/changes/summary",
  "/api/v1/changes",
  "/api/v1/governance/queue",
];
const SECTIONS = ["Needs attention", "Recent changes", "Reviews", "Data health", "Where to start", "What ADG will not do"];

/** Load the Overview; returns the API requests its render made and the browser's timings. */
async function overview(page) {
  const from = performance.now();
  const response = await page.goto(`${WEB}/`, { waitUntil: "load" });
  const to = performance.now();
  expect(response?.status() === 200, `/: HTTP ${response?.status()}`);
  await page.waitForFunction(() => performance.getEntriesByName("first-contentful-paint").length > 0, null, { timeout: 5000 }).catch(() => {});
  const timing = await page.evaluate(() => {
    const [nav] = performance.getEntriesByType("navigation");
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    return { ttfb: nav.responseStart, responseEnd: nav.responseEnd, domContentLoaded: nav.domContentLoadedEventEnd, load: nav.loadEventEnd, firstContentfulPaint: fcp?.startTime ?? null };
  });
  return { reads: requests.filter((r) => r.start >= from && r.start <= to), timing };
}
/** The Overview's section headings, in document order. */
async function order(page) {
  return page.evaluate((known) => [...document.querySelectorAll("#main h2")].map((h) => h.textContent.trim()).filter((t) => known.includes(t)), SECTIONS);
}
const region = (page, name) => page.getByRole("region", { name, exact: true });
/** The region's DOM text: what a screen reader reads, before CSS (badges are lower-cased by CSS). */
async function regionText(page, name) {
  return squash(await region(page, name).evaluate((el) => [...el.querySelectorAll("h2, h3, p, li, dt, dd")].filter((e) => !e.closest("li") || e.tagName === "LI").map((e) => e.textContent).join(" ")));
}
/** Marks the document, so a later read can tell a client navigation from a reload. */
async function mark(page) {
  return page.evaluate(() => (window.__ux06 = Math.random()));
}
async function marked(page, value) {
  return page.evaluate((expected) => window.__ux06 === expected, value);
}
async function shot(page, name, fullPage = true) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage });
}

/* ------------------------------------------------------------------ fixtures */

const HEALTHY = (status) => ({
  ...status,
  health: "healthy",
  summary: "Every collector's most recent run succeeded (gate fixture).",
  concerns: [],
  collectors: status.collectors.map((c) => ({ ...c, status: "succeeded", error_count: 0, trustworthy: true, concern: null, downgrade_reason: null })),
});
const COMPLETE = (summary) => ({
  ...summary,
  coverage: { ...summary.coverage, complete: true, truncation: null },
  interpretation: summary.interpretation && { ...summary.interpretation, reading: "complete", exhaustive: true, reasons: [], held: [], basis_current: true },
});
const ZERO = (summary) => ({
  ...summary,
  total: 0,
  severity_counts: Object.fromEntries(Object.keys(summary.severity_counts).map((k) => [k, 0])),
  rule_counts: {},
  status_counts: { ...summary.status_counts, open: 0 },
});
const NEVER = (summary) => ({
  ...ZERO(summary),
  coverage: { ...summary.coverage, has_ever_run: false, evaluated_at: null, trigger: null, complete: false, evaluation_id: null },
  interpretation: summary.interpretation && { ...summary.interpretation, reading: "not_evaluated", exhaustive: false, reasons: ["The rules have never been evaluated (gate fixture)."], held: [] },
});
const LONG = "Nested-privileged-membership-through-an-unbroken-chain-of-groups-without-any-spaces-at-all-0123456789-0123456789";

/* ------------------------------------------------------------------ state of the dataset */

const planner = await apiToken("planner");
const truth = {
  status: (await api(planner, "/api/v1/collection/status")).body,
  risk: (await api(planner, "/api/v1/risks/summary")).body,
  rules: (await api(planner, "/api/v1/risks/rules")).body.rules,
};
const titles = new Map(truth.rules.map((rule) => [rule.rule_id, rule.title]));
measurements.dataset = {
  collection_health: truth.status.health,
  scopes: truth.status.collectors.length,
  trustworthy_scopes: truth.status.collectors.filter((c) => c.trustworthy).length,
  concerns: truth.status.concerns.length,
  risk_reading: truth.risk.interpretation?.reading ?? null,
  open_findings: truth.risk.total,
  severity_counts: truth.risk.severity_counts,
};

/** The reviewer's queue, seeded with one real campaign when it is empty. */
async function seedCampaign() {
  const login = await apiLogin("reviewer");
  const reviewer = login.access_token;
  const queue = (await api(reviewer, "/api/v1/governance/queue")).body;
  if (queue.entries.length > 0) return `queue already holds ${queue.entries.length} campaign(s)`;
  const governance = await apiToken("governance");
  const shares = (await api(governance, "/api/v1/servers?limit=5")).body;
  const server = shares.items[0].key ?? shares.items[0].server_key;
  const serverShares = (await api(governance, `/api/v1/servers/${encodeURIComponent(server)}/shares?limit=5`)).body;
  const share = serverShares.items[0].key ?? serverShares.items[0].share_key;
  const created = await api(governance, "/api/v1/governance/campaigns", {
    method: "POST",
    body: JSON.stringify({
      name: "Overview gate review",
      focus: "resource",
      scopes: [{ kind: "share", key: share }],
      baseline_at: new Date(Date.now() - 60_000).toISOString(),
      due_at: new Date(Date.now() + 14 * 86_400_000).toISOString(),
    }),
  });
  expect(created.status === 201 || created.status === 200, `create campaign: HTTP ${created.status} ${JSON.stringify(created.body).slice(0, 200)}`);
  const id = created.body.campaign?.campaign_id ?? created.body.campaign_id;
  for (const [step, body] of [
    ["generation", {}],
    // The subject a sign-in carries (`dev:reviewer`), not the account name.
    ["assignments", { reviewer_subject: login.principal.subject, reviewer_display_name: login.principal.display_name }],
    ["activation", {}],
  ]) {
    const result = await api(governance, `/api/v1/governance/campaigns/${id}/${step}`, { method: "POST", body: JSON.stringify(body) });
    expect(result.status < 300, `${step}: HTTP ${result.status} ${JSON.stringify(result.body).slice(0, 200)}`);
  }
  return `created campaign ${id} on share ${share}, assigned to ${login.principal.subject}`;
}
await check("setup: the reviewer has a real campaign in their queue", seedCampaign);
truth.queue = (await api(await apiToken("reviewer"), "/api/v1/governance/queue")).body;

/* ------------------------------------------------------------------ structure and order */

await check("demo dataset as it is: one h1, one shell, every section a named region, Data health first", async () => {
  const page = await pageFor("planner");
  await overview(page);
  expect((await page.locator("h1").count()) === 1, `${await page.locator("h1").count()} h1 elements`);
  expect((await page.locator("header.shell-header").count()) === 1, "shell rendered more than once");
  expect((await page.getByRole("navigation", { name: /primary|sections/i }).count()) <= 1, "navigation rendered more than once");
  for (const name of SECTIONS.slice(0, 4)) {
    expect((await region(page, name).count()) === 1, `no region named ${name}`);
  }
  const sections = await order(page);
  const degraded = truth.status.health !== "healthy" || truth.risk.interpretation?.reading !== "complete";
  expect(degraded, "the demo dataset was expected to be incomplete");
  expect(sections[0] === "Data health", `order ${sections.join(" > ")}`);
  expect(sections.slice(1).join(">") === "Needs attention>Recent changes>Reviews>Where to start>What ADG will not do", `order ${sections.join(" > ")}`);
  await shot(page, "overview-demo-1440");
  return sections.join(" > ");
});

await check("fixture healthy + complete totals: the ordinary order returns", async () => {
  faults.set("/api/v1/collection/status", { transform: HEALTHY });
  faults.set("/api/v1/risks/summary", { transform: COMPLETE });
  const page = await pageFor("planner");
  await overview(page);
  const sections = await order(page);
  expect(sections.join(">") === SECTIONS.join(">"), `order ${sections.join(" > ")}`);
  await shot(page, "overview-healthy-fixture-1440");
  return sections.join(" > ");
});

await check("fixture: healthy collection but totals not complete still promotes Data health", async () => {
  faults.set("/api/v1/collection/status", { transform: HEALTHY });
  const page = await pageFor("planner");
  await overview(page);
  const sections = await order(page);
  expect(sections[0] === "Data health", `order ${sections.join(" > ")}`);
});

await check("fault: collection status HTTP 503 promotes Data health and says so, the rest stays useful", async () => {
  faults.set("/api/v1/collection/status", { status: 503 });
  const page = await pageFor("planner");
  await overview(page);
  const sections = await order(page);
  expect(sections[0] === "Data health", `order ${sections.join(" > ")}`);
  const health = region(page, "Data health");
  expect((await health.getByRole("alert").count()) === 1, "no alert in Data health");
  const text = await regionText(page, "Data health");
  expect(!/\bComplete\b|0 of/.test(text), `reads as healthy or zero: ${text}`);
  const attention = await regionText(page, "Needs attention");
  expect(attention.includes(`${n(truth.risk.severity_counts.critical ?? 0)} critical`), "Needs attention lost its counts");
  expect((await region(page, "Recent changes").count()) === 1 && (await region(page, "Reviews").count()) === 1, "a section vanished");
  return squash(await health.getByRole("alert").innerText()).slice(0, 80);
});

/* ------------------------------------------------------------------ needs attention */

await check("Needs attention, demo totals (incomplete): counts shown as Not a total with the backend's reason", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const text = await regionText(page, "Needs attention");
  expect(text.includes("Not a total"), "no Not a total");
  const reason = squash(truth.risk.interpretation.reasons.join(" ")).slice(0, 60);
  expect(text.includes(reason), `reason missing: ${reason}`);
  expect(!text.includes("latest completed evaluation"), "worded as a complete evaluation");
  return reason;
});

await check("fixture complete totals: critical, high, total, top three rules; no Not a total", async () => {
  faults.set("/api/v1/risks/summary", { transform: COMPLETE });
  const page = await pageFor("planner");
  await overview(page);
  const section = region(page, "Needs attention");
  const text = await regionText(page, "Needs attention");
  expect(!text.includes("Not a total"), "Not a total over a complete reading");
  const counts = truth.risk.severity_counts;
  for (const [severity, count] of [["critical", counts.critical ?? 0], ["high", counts.high ?? 0]]) {
    const name = `${n(count)} ${severity} ${count === 1 ? "finding" : "findings"}`;
    expect((await section.getByRole("link", { name, exact: true }).count()) === 1, `no link "${name}"`);
  }
  expect((await section.getByRole("link", { name: `${n(truth.risk.total)} open findings in all`, exact: true }).count()) === 1, "no total link");
  const top = Object.entries(truth.risk.rule_counts).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3);
  const listed = await section.getByRole("list", { name: "Most common open findings" }).getByRole("link").allInnerTexts();
  expect(listed.join("|") === top.map(([id]) => titles.get(id) ?? id).join("|"), `rules ${listed.join(", ")}`);
  return `${counts.critical} critical, ${counts.high} high, ${truth.risk.total} open; rules ${top.map(([id, c]) => `${id}=${c}`).join(", ")}`;
});

await check("navigation: each severity link opens Findings filtered to it (client navigation, Back returns)", async () => {
  const page = await pageFor("planner");
  const seen = [];
  for (const severity of ["critical", "high"]) {
    await overview(page);
    const token = await mark(page);
    const count = truth.risk.severity_counts[severity] ?? 0;
    await region(page, "Needs attention").getByRole("link", { name: new RegExp(` ${severity} finding`) }).click();
    await page.waitForURL((url) => url.pathname === "/risks" && url.searchParams.get("severity") === severity);
    await page.locator("h1").waitFor();
    expect(await marked(page, token), "full document reload");
    const active = await page.locator('nav[aria-label="Filters"] [aria-current="true"]').allInnerTexts();
    expect(active.includes(severity) && active.includes("open only"), `active filters ${active.join(", ")}`);
    const body = squash(await page.locator("#main").innerText());
    expect(body.includes(`${count} findings in total`), `Findings does not say ${count} findings in total`);
    await page.goBack();
    await page.waitForURL((url) => url.pathname === "/");
    expect((await page.locator("h1").innerText()).trim() === "Overview" && (await marked(page, token)), "Back did not return to the same Overview document");
    seen.push(`${severity}: ${count} in total, filters ${active.join("+")}`);
  }
  return seen.join("; ");
});

await check("navigation: each rule link opens Findings filtered to that rule", async () => {
  const page = await pageFor("planner");
  const top = Object.entries(truth.risk.rule_counts).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3);
  for (const [ruleId, count] of top) {
    await overview(page);
    const token = await mark(page);
    await page.getByRole("list", { name: "Most common open findings" }).getByRole("link", { name: titles.get(ruleId) ?? ruleId, exact: true }).click();
    await page.waitForURL((url) => url.pathname === "/risks" && url.searchParams.get("rule") === ruleId);
    await page.locator("h1").waitFor();
    expect(await marked(page, token), "full document reload");
    const active = await page.locator('nav[aria-label="Filters"] [aria-current="true"]').allInnerTexts();
    expect(active.includes(ruleId), `active filters ${active.join(", ")}`);
    expect(squash(await page.locator("#main").innerText()).includes(`${count} findings in total`), `${ruleId}: not ${count} in total`);
  }
  return top.map(([id, c]) => `${id}=${c}`).join(", ");
});

await check("fixture never evaluated: no numerical finding totals at all", async () => {
  faults.set("/api/v1/risks/summary", { transform: NEVER });
  const page = await pageFor("planner");
  await overview(page);
  const text = await regionText(page, "Needs attention");
  expect(text.includes("Findings have not been evaluated yet."), text.slice(0, 120));
  expect(!/\d/.test(text), `a number is shown: ${text}`);
  expect((await region(page, "Needs attention").getByRole("link").allInnerTexts()).every((t) => !/\d/.test(t)), "a count link");
  expect((await regionText(page, "Data health")).includes("never been evaluated"), "Data health does not say never evaluated");
});

await check("fixture zero findings: 'No open findings' only over a complete evaluation", async () => {
  faults.set("/api/v1/risks/summary", { transform: (s) => ZERO(COMPLETE(s)) });
  const page = await pageFor("planner");
  await overview(page);
  expect((await regionText(page, "Needs attention")).includes("No open findings in the latest completed evaluation."), "complete zero not stated");
  faults.set("/api/v1/risks/summary", { transform: ZERO });
  await overview(page);
  const text = await regionText(page, "Needs attention");
  expect(text.includes("No open findings were returned, but that is not a complete answer.") && !text.includes("latest completed evaluation"), text.slice(0, 160));
});

await check("fault: findings summary HTTP 500 shows an error, no zero, and the other sections stay", async () => {
  faults.set("/api/v1/risks/summary", { status: 500 });
  const page = await pageFor("planner");
  await overview(page);
  const section = region(page, "Needs attention");
  expect((await section.getByRole("alert").count()) === 1, "no alert");
  expect(!/\b0 critical|\b0 open/.test(await regionText(page, "Needs attention")), "a zero is shown");
  expect((await section.getByRole("link", { name: /finding/ }).count()) === 0, "count links over a failure");
  expect((await regionText(page, "Data health")).includes("Collection"), "Data health blank");
  expect((await region(page, "Recent changes").count()) === 1, "Recent changes blank");
  return squash(await section.getByRole("alert").innerText()).slice(0, 80);
});

/* ------------------------------------------------------------------ recent changes */

/** The dataset's newest change, plus a margin: "now" for the shifted change window. */
const DATASET_NOW = Date.parse(process.env.DATASET_NOW ?? "2026-10-01T12:00:00Z");
const shiftChanges = () => {
  const shift = { shiftMs: Date.now() - DATASET_NOW };
  faults.set("/api/v1/changes", shift);
  faults.set("/api/v1/changes/summary", shift);
  return shift.shiftMs;
};

await check("demo dataset as it is: the last 7 days hold no changes, worded against collection rather than as a quiet week", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const section = region(page, "Recent changes");
  const text = await regionText(page, "Recent changes");
  expect((await section.getByRole("alert").count()) === 1, `no warning: ${text}`);
  expect(!text.includes("0 permission") && !/quiet/i.test(text), "a zero or a quiet week");
  expect(/collector failed|collection is incomplete/.test(text), text.slice(0, 160));
  return squash(await section.getByRole("alert").innerText()).slice(0, 100);
});

await check("fixture time shift: count, excluded, most severe match the API's 7-day default view; rows link to history", async () => {
  const page = await pageFor("planner");
  const shiftMs = shiftChanges();
  const now = Date.now() - shiftMs;
  const window = `from=${encodeURIComponent(new Date(now - 7 * 86_400_000).toISOString())}&to=${encodeURIComponent(new Date(now).toISOString())}`;
  const summary = (await api(planner, `/api/v1/changes/summary?${window}`)).body;
  await overview(page);
  const text = await regionText(page, "Recent changes");
  measurements.changes = { shifted_window: [new Date(now - 7 * 86_400_000).toISOString(), new Date(now).toISOString()], total: summary.total, returned: summary.returned, excluded: summary.excluded, highest_severity: summary.highest_severity };
  expect(summary.total > 0, "the shifted window is empty too");
  expect(text.includes(`${n(summary.returned)} permission or unclassified change`), `count ${summary.returned} missing: ${text.slice(0, 120)}`);
  if (summary.excluded > 0) expect(text.includes(`${n(summary.excluded)} more change`), "excluded count missing");
  if (summary.highest_severity) expect(text.includes(`most severe ${summary.highest_severity}`), "most severe missing");
  const rows = region(page, "Recent changes").getByRole("list", { name: "Most recent changes" }).getByRole("listitem");
  const count = await rows.count();
  expect(count >= 1 && count <= 3, `${count} rows`);
  expect(!/\d{4}-\d{2}-\d{2}/.test(text), "a timestamp is presented as the change time");
  const token = await mark(page);
  const link = rows.first().getByRole("link");
  const href = await link.getAttribute("href");
  await link.click();
  await page.waitForURL((url) => url.pathname === "/changes" && url.searchParams.has("kind") && url.searchParams.has("key"));
  expect((await page.locator("h1").innerText()).trim() === "History", "not the object history page");
  expect(await marked(page, token), "full document reload");
  await page.goBack();
  await page.waitForURL((url) => url.pathname === "/");
  return `${summary.returned} shown, ${summary.excluded} excluded, most severe ${summary.highest_severity}, ${count} rows; first → ${decodeURIComponent(href).slice(0, 70)}`;
});

await check("fixture time shift: the Recent changes link lands on /changes?days=7 showing the same count", async () => {
  const page = await pageFor("planner");
  shiftChanges();
  await overview(page);
  const text = await regionText(page, "Recent changes");
  const match = text.match(/([\d,]+) permission or unclassified change/);
  const token = await mark(page);
  await region(page, "Recent changes").getByRole("link", { name: "View changes from the last 7 days", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/changes" && url.searchParams.get("days") === "7");
  expect(await marked(page, token), "full document reload");
  const strip = squash(await page.getByRole("region", { name: "Summary" }).innerText());
  expect(match !== null, "no count on the Overview");
  expect(strip.includes(`${match[1]} shown`), `Changes says "${strip.slice(0, 90)}", Overview said ${match[1]}`);
  await page.goBack();
  return `Overview ${match[1]}; Changes: ${strip.slice(0, 60)}`;
});

/* ------------------------------------------------------------------ reviews */

await check("Reviews (reviewer): assigned, campaigns, past due, entries, due dates, campaign link", async () => {
  const page = await pageFor("reviewer");
  await overview(page);
  const q = truth.queue;
  const text = await regionText(page, "Reviews");
  expect(text.includes(`${n(q.total_pending)} item${q.total_pending === 1 ? "" : "s"} assigned to you across ${n(q.entries.length)} campaign`), text.slice(0, 140));
  expect(q.overdue_campaigns === 0 ? !text.includes("past due") : text.includes(`${q.overdue_campaigns} campaign`), "past-due count");
  const links = region(page, "Reviews").getByRole("list", { name: "Campaigns in your queue" }).getByRole("link");
  expect((await links.count()) === Math.min(3, q.entries.length), "entry count");
  const due = q.entries[0].due_at ? `Due ${q.entries[0].due_at.slice(0, 10)}` : "No deadline";
  expect(text.includes(due), `due date "${due}" missing`);
  const token = await mark(page);
  await links.first().click();
  await page.waitForURL((url) => url.pathname === `/governance/campaigns/${q.entries[0].campaign_id}` && url.searchParams.get("mine") === "1");
  expect((await page.locator("h1").innerText()).trim() === q.entries[0].name, "campaign page heading");
  expect(await marked(page, token), "full document reload");
  await page.goBack();
  await page.waitForURL((url) => url.pathname === "/");
  await overview(page);
  await region(page, "Reviews").getByRole("link", { name: "Open my review queue", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/governance");
  return `${q.total_pending} items, ${q.entries.length} campaign(s), ${due}`;
});

await check("fixture past-due campaign: the count and the entry say past due in words", async () => {
  faults.set("/api/v1/governance/queue", {
    transform: (q) => ({ ...q, overdue_campaigns: 1, entries: q.entries.map((e, i) => (i === 0 ? { ...e, overdue: true, due_at: "2026-10-01T00:00:00Z" } : e)) }),
  });
  const page = await pageFor("reviewer");
  await overview(page);
  const text = await regionText(page, "Reviews");
  expect(text.includes("1 campaign past due") && text.includes("Was due 2026-10-01") && text.includes("Overdue"), text.slice(0, 200));
});

await check("Reviews (auditor, governance:read, nothing assigned): 'Nothing is assigned to you', no collection warning", async () => {
  const page = await pageFor("auditor");
  await overview(page);
  const text = await regionText(page, "Reviews");
  expect(text.startsWith("Reviews Nothing is assigned to you."), text.slice(0, 120));
  expect((await region(page, "Reviews").getByRole("alert").count()) === 0, "an alert in Reviews");
  expect((await region(page, "Reviews").getByRole("link", { name: "See review campaigns", exact: true }).count()) === 1, "no campaigns link");
});

/* ------------------------------------------------------------------ permissions, at the request level */

const VARIANTS = [
  { user: "planner", expect: ["Needs attention", "Recent changes", "Reviews"], reads: OVERVIEW_READS },
  { user: "auditor", expect: ["Needs attention", "Recent changes", "Reviews"], reads: OVERVIEW_READS },
  { user: "viewer", expect: ["Needs attention", "Recent changes"], reads: OVERVIEW_READS.filter((p) => p !== "/api/v1/governance/queue") },
  { user: "nobody", expect: [], reads: ["/api/v1/collection/status", "/runtime"] },
];
measurements.permissions = {};
for (const variant of VARIANTS) {
  await check(`permissions (${variant.user}): sections ${variant.expect.join(", ") || "none"}; only their reads are sent`, async () => {
    const page = await pageFor(variant.user);
    const { reads } = await overview(page);
    for (const name of ["Needs attention", "Recent changes", "Reviews"]) {
      const present = (await region(page, name).count()) === 1;
      expect(present === variant.expect.includes(name), `${name} ${present ? "present" : "absent"}`);
    }
    expect((await region(page, "Data health").count()) === 1, "Data health absent");
    const sent = [...new Set(reads.map((r) => r.path).filter((p) => OVERVIEW_READS.includes(p)))];
    const extra = sent.filter((p) => !variant.reads.includes(p));
    const missing = variant.reads.filter((p) => !sent.includes(p));
    expect(extra.length === 0, `unauthorized reads sent: ${extra.join(", ")}`);
    expect(missing.length === 0, `expected reads not sent: ${missing.join(", ")}`);
    const refused = reads.filter((r) => r.status === 403).map((r) => r.path);
    measurements.permissions[variant.user] = { sent, refused };
    return `${sent.length} reads${refused.length ? `; 403 from ${refused.join(", ")}` : ""}`;
  });
}

/* ------------------------------------------------------------------ data health */

await check("Data health, demo dataset: state word + symbol, clean scopes, coverage, evaluation, concerns, demo source", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const text = await regionText(page, "Data health");
  const s = truth.status;
  const words = { healthy: "✓ Complete", incomplete: "◐ Incomplete", failed: "✕ Failed", no_data: "∅ Never collected" };
  expect(text.includes(words[s.health]), `state "${words[s.health]}" missing`);
  expect(text.includes(`${n(s.collectors.filter((c) => c.trustworthy).length)} of ${n(s.collectors.length)} collection scope`), "scope line");
  if (truth.risk.collection) expect(text.includes(truth.risk.collection.headline), "coverage headline");
  expect(text.includes("Findings evaluation") && text.includes("Last ran"), "evaluation line");
  expect(text.includes(truth.risk.interpretation.reading === "complete" ? "✓ Total" : "⚠ Not a total"), "total/not-a-total badge");
  const concerns = await region(page, "Data health").getByRole("list", { name: "Collection concerns" }).getByRole("listitem").allInnerTexts();
  const shown = Math.min(3, s.concerns.length) + (s.concerns.length > 3 ? 1 : 0);
  expect(concerns.length === shown, `${concerns.length} concern rows for ${s.concerns.length} concerns`);
  if (s.concerns.length > 3) expect(concerns.at(-1).includes(`and ${s.concerns.length - 3} more`), "no 'and N more'");
  expect(text.includes("Synthetic demo data for Genesee Press. No production directory or file server is connected."), "demo source line");
  expect(!/%|\bscore\b/i.test(text), "a percentage or score");
  return `${words[s.health]}, ${s.collectors.filter((c) => c.trustworthy).length}/${s.collectors.length} scopes, ${s.concerns.length} concerns`;
});

for (const [health, word] of [["healthy", "✓ Complete"], ["incomplete", "◐ Incomplete"], ["failed", "✕ Failed"], ["no_data", "∅ Never collected"]]) {
  await check(`fixture Data health ${health}: worded "${word}" (text and symbol)`, async () => {
    faults.set("/api/v1/collection/status", {
      transform: (s) => (health === "healthy" ? HEALTHY(s) : health === "no_data" ? { ...s, health, summary: "No collector has reported (gate fixture).", concerns: [], collectors: [] } : { ...s, health }),
    });
    const page = await pageFor("planner");
    await overview(page);
    const badge = region(page, "Data health").locator("dd .badge").first();
    expect(squash(await badge.textContent()) === word, `badge "${await badge.textContent()}"`);
    const text = await regionText(page, "Data health");
    if (health === "no_data") expect(!text.includes("0 of 0"), "0 of 0 scopes");
    expect(!/%/.test(text), "a percentage");
  });
}

/* ------------------------------------------------------------------ fan-out and timing */

await check("fan-out: one /runtime shared with the shell; the six page reads start together after /auth/me; no duplicates; no table reads", async () => {
  const page = await pageFor("planner");
  const { reads } = await overview(page);
  // Every page read needs the viewer (its token and capabilities), so they follow /auth/me.
  const me = reads.find((r) => r.path === "/auth/me");
  const ours = reads.filter((r) => OVERVIEW_READS.includes(r.path) && r.start >= me.end);
  const t0 = reads[0].start;
  const starts = ours.map((r) => r.start);
  const spread = Math.max(...starts) - Math.min(...starts);
  const firstEnd = Math.min(...ours.map((r) => r.end));
  const byPath = {};
  for (const r of reads) byPath[r.path] = (byPath[r.path] ?? 0) + 1;
  const duplicates = Object.entries(byPath).filter(([p, c]) => c > 1 && OVERVIEW_READS.includes(p));
  const slowest = ours.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
  measurements.fanout = {
    reads: reads.map((r) => ({ path: r.path, query: r.query, status: r.status, start_ms: Math.round(r.start - t0), duration_ms: Math.round(r.end - r.start) })),
    viewer_read_ends_ms: Math.round(me.end - t0),
    total_requests: reads.length,
    overview_reads: ours.length,
    start_spread_ms: Math.round(spread),
    slowest: { path: slowest.path, ms: Math.round(slowest.end - slowest.start) },
    by_path: byPath,
    failed: reads.filter((r) => r.status >= 400).map((r) => `${r.path} ${r.status}`),
  };
  // /runtime is shared with the shell (one cached read per request), so it may start before /auth/me ends.
  expect(new Set(ours.map((r) => r.path)).size === 6 && ours.every((r) => r.path !== "/runtime"), `page reads ${ours.map((r) => r.path).join(", ")}`);
  expect(byPath["/runtime"] === 1, `/runtime read ${byPath["/runtime"]} times`);
  expect(Math.max(...starts) < firstEnd, "a read started only after another finished (waterfall)");
  expect(duplicates.length === 0, `duplicates ${JSON.stringify(duplicates)}`);
  const feed = ours.find((r) => r.path === "/api/v1/changes");
  expect(/limit=6\b/.test(feed.query), `change feed query ${feed.query}`);
  expect(!reads.some((r) => /\/risks\/findings|\/principals|\/scan-runs|\/governance\/campaigns/.test(r.path)), "a table read");
  return `${reads.length} API requests: ${Object.entries(byPath).map(([p, c]) => `${p}×${c}`).join(" ")}; the ${ours.length} page reads start ${Math.round(Math.min(...starts) - t0)} ms in (after /auth/me), spread ${Math.round(spread)} ms; slowest ${slowest.path} ${Math.round(slowest.end - slowest.start)} ms`;
});

await check(`timing: one first load, then ${WARM_RUNS} warm loads (TTFB, FCP, load, slowest read)`, async () => {
  const runs = [];
  for (let i = 0; i <= WARM_RUNS; i += 1) {
    const page = await pageFor("admin", { width: 1440, height: 900 + i }); // a new context per run: no browser cache
    const { reads, timing } = await overview(page);
    const ours = reads.filter((r) => OVERVIEW_READS.includes(r.path));
    const slowest = ours.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
    runs.push({ ...Object.fromEntries(Object.entries(timing).map(([k, v]) => [k, v === null ? null : Math.round(v)])), slowest: `${slowest.path} ${Math.round(slowest.end - slowest.start)}` });
  }
  measurements.timing = runs;
  const warm = runs.slice(1);
  const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
  return `first: TTFB ${runs[0].ttfb} ms, FCP ${runs[0].firstContentfulPaint} ms, load ${runs[0].load} ms; warm median: TTFB ${median(warm.map((r) => r.ttfb))} ms, FCP ${median(warm.map((r) => r.firstContentfulPaint))} ms, load ${median(warm.map((r) => r.load))} ms`;
});

await check("fault: a 4 s changes summary delays the page, not other sections' content; a 12 s one times out in its section only", async () => {
  const page = await pageFor("planner");
  const shiftMs = shiftChanges();
  faults.set("/api/v1/changes/summary", { shiftMs, delayMs: 4000 });
  const slow = await overview(page);
  expect((await regionText(page, "Recent changes")).includes("permission or unclassified"), "the slow section lost its content");
  faults.set("/api/v1/changes/summary", { shiftMs, delayMs: 12_000 });
  const hung = await overview(page);
  const section = region(page, "Recent changes");
  expect((await section.getByRole("alert").innerText()).includes("ADG did not answer in time"), "no timeout message");
  expect((await regionText(page, "Needs attention")).includes("critical"), "Needs attention lost its counts");
  measurements.slow = { delay_4s_ttfb_ms: Math.round(slow.timing.ttfb), delay_12s_ttfb_ms: Math.round(hung.timing.ttfb) };
  return `TTFB with a 4 s read ${Math.round(slow.timing.ttfb)} ms; with a 12 s read ${Math.round(hung.timing.ttfb)} ms (client timeout 10 s)`;
});

/* ------------------------------------------------------------------ responsive */

const VIEWPORTS = [
  { name: "wide-desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "tablet", width: 1024, height: 768 },
  { name: "narrow", width: 768, height: 1024 },
  { name: "phone", width: 375, height: 812 },
  { name: "minimum", width: 320, height: 640 },
];
measurements.responsive = {};
for (const viewport of VIEWPORTS) {
  await check(`responsive ${viewport.name} ${viewport.width}px: no sideways scroll, pair side by side or stacked, no overlap`, async () => {
    const page = await pageFor("planner", viewport);
    await overview(page);
    const layout = await page.evaluate(() => {
      const box = (name) => {
        const heading = [...document.querySelectorAll("#main section h2")].find((h) => h.textContent.trim() === name);
        const r = heading.closest("section").getBoundingClientRect();
        return { top: r.top + window.scrollY, bottom: r.bottom + window.scrollY, left: r.left, right: r.right };
      };
      const headings = [...document.querySelectorAll("#main h2, #main h3")].map((h) => h.getBoundingClientRect());
      const overlaps = headings.some((a, i) => headings.some((b, j) => j > i && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom));
      const clipped = [...document.querySelectorAll("#main section, #main section *")].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === "hidden").length;
      const sections = [...document.querySelectorAll("#main section")].map((s) => ({ w: s.scrollWidth, cw: s.clientWidth }));
      // A value squeezed beside its label is unreadable without overflowing: measure it.
      const health = [...document.querySelectorAll("#main section h2")].find((h) => h.textContent.trim() === "Data health").closest("section");
      const narrowest = Math.min(...[...health.querySelectorAll("dd")].map((dd) => dd.getBoundingClientRect().width));
      const healthWidth = health.clientWidth;
      return { narrowest, healthWidth, scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth, changes: box("Recent changes"), reviews: box("Reviews"), overlaps, clipped, wide: sections.filter((s) => s.w > s.cw + 1).length };
    });
    expect(layout.scrollWidth <= layout.innerWidth, `page scrolls sideways: ${layout.scrollWidth} > ${layout.innerWidth}`);
    expect(!layout.overlaps, "headings overlap");
    expect(layout.clipped === 0 && layout.wide === 0, `${layout.clipped} clipped, ${layout.wide} overflowing sections`);
    expect(layout.narrowest >= Math.min(240, layout.healthWidth * 0.6), `a Data health value is ${Math.round(layout.narrowest)} px wide in a ${layout.healthWidth} px section`);
    const side = Math.abs(layout.changes.top - layout.reviews.top) < 2 && layout.reviews.left >= layout.changes.right;
    const stacked = layout.reviews.top >= layout.changes.bottom - 1;
    expect(side || stacked, "Recent changes and Reviews neither side by side nor stacked");
    measurements.responsive[viewport.name] = side ? "side by side" : "stacked";
    await shot(page, `overview-demo-${viewport.width}`);
    return side ? "side by side" : "stacked";
  });
}

await check("fixture long names at 320 px and 1440 px: an unbroken rule title, change headline and campaign name keep the page in bounds", async () => {
  faults.set("/api/v1/risks/rules", { transform: (r) => ({ ...r, rules: r.rules.map((rule) => ({ ...rule, title: `${LONG}-${rule.rule_id}` })) }) });
  faults.set("/api/v1/risks/summary", { transform: COMPLETE });
  const shiftMs = shiftChanges();
  faults.set("/api/v1/changes", { shiftMs, transform: (f) => ({ ...f, changes: f.changes.map((c) => ({ ...c, edit: null, reasons: [`\\\\GP-FS-01\\${LONG}\\${LONG}`] })) }) });
  faults.set("/api/v1/governance/queue", { transform: (q) => ({ ...q, entries: q.entries.map((e) => ({ ...e, name: LONG })) }) });
  const out = [];
  for (const viewport of [VIEWPORTS[5], VIEWPORTS[0]]) {
    const page = await pageFor("reviewer", viewport);
    await overview(page);
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    await shot(page, `overview-long-names-${viewport.width}`);
    expect(widths.scroll <= widths.inner, `${viewport.width}px scrolls sideways: ${widths.scroll}`);
    out.push(`${viewport.width}px ok`);
  }
  return out.join(", ");
});

/* ------------------------------------------------------------------ accessibility (browser semantics) */

await check("a11y: one h1, named regions, descriptive links, no nested links or clickable cards, no positive tabindex", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const facts = await page.evaluate(() => {
    const main = document.querySelector("#main");
    const links = [...main.querySelectorAll("a[href]")];
    const name = (a) => (a.getAttribute("aria-label") ?? a.textContent).replace(/\s+/g, " ").trim();
    return {
      h1: document.querySelectorAll("h1").length,
      emptyLinks: links.filter((a) => name(a) === "").length,
      vague: links.map(name).filter((t) => /^(learn more|details|explore|here|click here|more|view)$/i.test(t)),
      nested: main.querySelectorAll("a a, a button, button a").length,
      cardLinks: main.querySelectorAll("a section, a .card, a h2, section[onclick], .card[onclick], section[role=link], .card[tabindex]").length,
      positiveTabindex: [...document.querySelectorAll("[tabindex]")].filter((e) => Number(e.getAttribute("tabindex")) > 0).length,
      badgesWithoutWords: [...main.querySelectorAll("section .badge")].filter((b) => !/[A-Za-z]/.test(b.textContent)).length,
      linkCount: links.length,
    };
  });
  expect(facts.h1 === 1, `${facts.h1} h1`);
  expect(facts.emptyLinks === 0, `${facts.emptyLinks} links without a name`);
  expect(facts.vague.length === 0, `vague links: ${facts.vague.join(", ")}`);
  expect(facts.nested === 0 && facts.cardLinks === 0, `nested ${facts.nested}, card links ${facts.cardLinks}`);
  expect(facts.positiveTabindex === 0, "positive tabindex");
  expect(facts.badgesWithoutWords === 0, "a status badge without a word");
  measurements.a11y = facts;
  return `${facts.linkCount} links in main, all named`;
});

await check("a11y keyboard: Tab reaches every link in the Overview sections, in DOM order, each with a visible focus indicator", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const expected = await page.evaluate(() => [...document.querySelectorAll("#main section a[href]")].map((a, i) => (a.dataset.ux06 = String(i))));
  await page.locator("body").focus();
  const reached = [];
  const invisible = [];
  for (let i = 0; i < 200 && reached.length < expected.length; i += 1) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el?.dataset?.ux06) return null;
      const style = getComputedStyle(el);
      const ring = (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none";
      return { id: el.dataset.ux06, ring, text: el.textContent.trim().slice(0, 40) };
    });
    if (info) {
      reached.push(info.id);
      if (!info.ring) invisible.push(info.text);
    }
  }
  expect(reached.length === expected.length, `reached ${reached.length} of ${expected.length}`);
  expect(reached.join(",") === expected.join(","), "focus order differs from DOM order");
  expect(invisible.length === 0, `no visible focus on: ${invisible.join(", ")}`);
  return `${reached.length} section links reached in order, all with a focus ring`;
});

for (const viewport of [VIEWPORTS[0], VIEWPORTS[4]]) {
  await check(`a11y reading order at ${viewport.width}px: section DOM order matches visual order`, async () => {
    const page = await pageFor("planner", viewport);
    await overview(page);
    const boxes = await page.evaluate((known) => [...document.querySelectorAll("#main h2")].filter((h) => known.includes(h.textContent.trim())).map((h) => { const r = h.getBoundingClientRect(); return { t: h.textContent.trim(), top: Math.round(r.top + scrollY), left: Math.round(r.left) }; }), SECTIONS);
    for (let i = 1; i < boxes.length; i += 1) {
      const [a, b] = [boxes[i - 1], boxes[i]];
      expect(b.top > a.top + 1 || (Math.abs(b.top - a.top) <= 1 && b.left > a.left), `${b.t} is visually before ${a.t}`);
    }
    return boxes.map((b) => b.t).join(" > ");
  });
}

/* ------------------------------------------------------------------ "Where to start" links */

await check("navigation: every 'Where to start' link opens its section by client navigation, and Back returns", async () => {
  const page = await pageFor("planner");
  await overview(page);
  const card = page.locator("#main .card").filter({ has: page.getByRole("heading", { name: "Where to start", exact: true }) });
  const targets = await card.getByRole("link").evaluateAll((links) => links.map((a) => ({ name: a.textContent.trim(), href: a.getAttribute("href") })));
  const visited = [];
  for (const target of targets) {
    if (page.url() !== `${WEB}/`) await overview(page);
    const token = await mark(page);
    await card.getByRole("link", { name: target.name, exact: true }).click();
    await page.waitForURL((url) => url.pathname === target.href);
    await page.locator("h1").waitFor();
    expect(await marked(page, token), `${target.href}: full document reload`);
    const body = squash(await page.locator("#main").innerText());
    expect(!body.includes("could not reach its API") && !body.includes("Your session has ended"), `${target.href}: broken page`);
    await page.goBack();
    await page.waitForURL((url) => url.pathname === "/");
    expect((await page.locator("h1").innerText()).trim() === "Overview", `Back from ${target.href}`);
    visited.push(target.href);
  }
  return visited.join(" ");
});

/* ------------------------------------------------------------------ errors */

await check("no page errors across every check", async () => {
  expect(pageErrors.length === 0, `${pageErrors.length}: ${pageErrors.slice(0, 2).join(" | ")}`);
  return `${consoleErrors.length} console errors${consoleErrors.length ? `: ${[...new Set(consoleErrors)].slice(0, 3).join(" | ")}` : ""}`;
});

await browser.close();
proxy.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
if (REPORT) writeFileSync(REPORT, `${JSON.stringify({ results, measurements, consoleErrors: [...new Set(consoleErrors)], pageErrors }, null, 2)}\n`);
process.exit(failed.length === 0 ? 0 : 1);
