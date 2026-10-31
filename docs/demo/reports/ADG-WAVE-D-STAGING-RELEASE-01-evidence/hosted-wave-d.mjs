#!/usr/bin/env node
/**
 * ADG-WAVE-D-STAGING-RELEASE-01: read-only hosted checks of the Wave D release on the public demo
 * staging, as the public `planner` account (development sign-in only; writes nothing).
 *
 * The canonical Genesee Press dataset holds no review campaign and the public accounts cannot
 * create one, so campaign → item navigation and scope names cannot be exercised here (owner
 * decision: keep hosted canonical). This covers what hosted can show:
 *
 *  API  1. governance campaigns and queue read (planner), viewer still refused, no campaign rows;
 *       2. scan-run scopes (ScanScopeView) are exactly {kind, key} over the 50 newest runs;
 *  UI   3–5. at 1360, 375 and 320 px: Overview → Findings → Reviews by client navigation with
 *       titles, Back and Forward, no document load, the Reviews empty state with no SID and no
 *       "(name not loaded)", an unknown campaign's coherent title, no sideways scroll;
 *       6. no page or hydration error.
 *
 *   BASE=https://… CHROMIUM_PATH=… [REPORT_JSON=…] node hosted-wave-d.mjs
 * Prints no cookie, token or hostname.
 */
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));
const BASE = process.env.BASE.replace(/\/+$/, "");
const SUFFIX = " — ADG";
const results = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    const detail = String(error?.message ?? error).split("\n")[0].replaceAll(BASE, "<staging>");
    results.push({ name, ok: false, detail });
    console.log(`FAIL  ${name} — ${detail}`);
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}
async function token(username) {
  const r = await fetch(`${BASE}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username }) });
  return r.ok ? (await r.json()).access_token : null;
}
const api = async (tok, path) => {
  const r = await fetch(`${BASE}${path}`, { headers: { Authorization: `Bearer ${tok}` } });
  return { status: r.status, body: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
};

const planner = await token("planner");
await check("1. API: governance reads as planner; viewer refused; no campaign rows on the canonical dataset", async () => {
  expect(planner, "planner sign-in failed");
  const list = await api(planner, "/api/v1/governance/campaigns");
  const queue = await api(planner, "/api/v1/governance/queue");
  expect(list.status === 200 && Array.isArray(list.body.items), `campaigns HTTP ${list.status}`);
  expect(queue.status === 200, `queue HTTP ${queue.status}`);
  const viewer = await token("viewer");
  const refused = await api(viewer, "/api/v1/governance/campaigns");
  expect(refused.status === 403, `viewer HTTP ${refused.status}`);
  return `campaigns 200 (${list.body.items.length} rows), queue 200, viewer 403`;
});

await check("2. API: scan-run scopes (ScanScopeView) are exactly {kind, key}", async () => {
  const runs = await api(planner, "/api/v1/scan-runs?limit=50");
  expect(runs.status === 200, `scan-runs HTTP ${runs.status}`);
  const rows = Object.values(runs.body).find(Array.isArray);
  const shapes = new Set();
  for (const row of rows) {
    const one = await api(planner, `/api/v1/scan-runs/${row.run_id}`);
    for (const field of ["declared_scopes", "reconciled_scopes"]) for (const s of one.body[field] ?? []) shapes.add(Object.keys(s).sort().join(","));
  }
  expect(shapes.size === 1 && shapes.has("key,kind"), `scope key sets ${[...shapes]}`);
  return `${rows.length} runs, every scope {kind, key}`;
});

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const pageErrors = [];
const consoleErrors = [];
let n = 3;
for (const width of [1360, 375, 320]) {
  await check(`${n++}. ${width}px: Overview → Findings → Reviews client-side, Back/Forward, Reviews empty state, unknown campaign title, no sideways scroll`, async () => {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    context.setDefaultTimeout(90_000);
    const signIn = await context.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "planner" } });
    expect(signIn.ok(), `web sign-in HTTP ${signIn.status()}`);
    const page = await context.newPage();
    const documents = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
    page.on("request", (r) => r.frame() === page.mainFrame() && r.resourceType() === "document" && documents.push(r.url()));
    const over = [];
    const sideways = async (label) => {
      const px = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (px > 0) over.push(`${label} +${px}px`);
    };
    const titled = async (want) => {
      await page.waitForFunction((t) => document.title === t, `${want}${SUFFIX}`, { timeout: 15_000 }).catch(() => {});
      const got = await page.title();
      expect(got === `${want}${SUFFIX}`, `title "${got}", wanted "${want}${SUFFIX}"`);
    };
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    await titled("Overview");
    await sideways("Overview");
    await page.evaluate(() => (window.__adgNoReload = true));
    documents.length = 0;
    const nav = page.getByRole("navigation", { name: "Primary" });
    const open = async (label) => {
      const before = page.url();
      const link = nav.getByRole("link", { name: label, exact: true });
      if (!(await link.isVisible())) await page.getByRole("button", { name: /menu/i }).first().click();
      await link.click();
      await page.waitForURL((u) => u.href !== before);
      await titled(label);
    };
    await open("Findings");
    await sideways("Findings");
    await open("Reviews");
    await page.waitForSelector("#main h1");
    await sideways("Reviews");
    const text = (await page.locator("#main").innerText()) ?? "";
    expect(!/S-1-5-21-/.test(text) && !text.includes("name not loaded"), "SID or fallback on the empty Reviews page");
    await page.goBack();
    await titled("Findings");
    await page.goForward();
    await titled("Reviews");
    const kept = await page.evaluate(() => window.__adgNoReload === true);
    expect(kept && documents.length === 0, `document loaded: ${documents.length}`);
    await page.goto(`${BASE}/governance/campaigns/00000000-0000-4000-8000-000000000000`, { waitUntil: "networkidle" });
    await titled("Review campaign");
    await sideways("unknown campaign");
    await context.close();
    expect(over.length === 0, over.join(", "));
    return `Reviews: "${text.split("\n").find((l) => /campaign/i.test(l))?.trim().slice(0, 80) ?? "(no campaign line)"}"`;
  });
}

await check(`${n}. no page error, no hydration error`, async () => {
  expect(pageErrors.length === 0, pageErrors.join(" | "));
  const hydration = consoleErrors.filter((e) => /hydrat|#418|#423|#425/i.test(e));
  expect(hydration.length === 0, hydration.join(" | "));
  return `0 page errors; console errors: ${consoleErrors.length ? consoleErrors.map((e) => e.replaceAll(BASE, "<staging>")).join(" | ") : "none"}`;
});

await browser.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (process.env.REPORT_JSON) writeFileSync(process.env.REPORT_JSON, JSON.stringify(results, null, 1));
process.exit(failed ? 1 : 0);
