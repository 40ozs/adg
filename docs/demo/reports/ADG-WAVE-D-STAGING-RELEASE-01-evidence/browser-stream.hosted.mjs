#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-11 browser check, a hosted variant of ADG-HOSTED-STREAMING-10's
 * browser-stream.mjs. That script assumes one injected 4 s delay on Reviews; hosted has no
 * injector, so this one names no slow section in advance. An init script records, in page time
 * (ms since navigation start), when the h1 and each resolved section first become visible, and
 * every moment the set of visible sections or loading placeholders changes. A run shows
 * progressive rendering when, at some moment, the h1 and at least one resolved section were
 * visible while at least one placeholder ([aria-busy]) was still on screen, and the last section
 * became visible at least MIN_GAP_MS after the h1.
 *
 *   BASE=https://… SIGN_IN_AS=planner RUNS=3 CHROMIUM_PATH=… SHOTS_DIR=… node browser-stream.hosted.mjs
 *
 * Read-only apart from the development sign-in. Prints no cookie or key.
 */
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));
const BASE = process.env.BASE.replace(/\/+$/, "");
const AS = process.env.SIGN_IN_AS ?? "planner";
const RUNS = Number(process.env.RUNS ?? 3);
const MIN_GAP = Number(process.env.MIN_GAP_MS ?? 100);
const SECTIONS = ["overview-health", "overview-attention", "overview-changes", "overview-reviews"];

// Runs in the page before any of its scripts; timestamps are performance.now() (navigation start = 0).
function recorder(ids) {
  const rec = { h1: null, visible: {}, attached: {}, timeline: [] };
  window.__adgStream = rec;
  let last = "";
  const tick = () => {
    const now = Math.round(performance.now());
    const h1 = document.querySelector("#main h1");
    if (h1 && rec.h1 === null && h1.checkVisibility()) rec.h1 = now;
    const shown = [];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el && rec.attached[id] === undefined) rec.attached[id] = now;
      if (el && el.checkVisibility()) {
        shown.push(id);
        if (rec.visible[id] === undefined) rec.visible[id] = now;
      }
    }
    const busy = [...document.querySelectorAll("#main [aria-busy='true']")].filter((e) => e.checkVisibility()).map((e) => e.getAttribute("aria-labelledby"));
    const state = JSON.stringify([!!rec.h1, shown, busy]);
    if (state !== last) rec.timeline.push({ t: now, h1: rec.h1 !== null, shown, busy }), (last = state);
  };
  new MutationObserver(tick).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "aria-busy", "style"] });
  const raf = () => (tick(), document.readyState === "complete" && !document.querySelector("#main [aria-busy='true']") ? null : requestAnimationFrame(raf));
  requestAnimationFrame(raf);
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const results = [];
let failed = 0;
for (const [width, height] of [[1360, 900], [375, 812]]) {
  const context = await browser.newContext({ viewport: { width, height } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: new RegExp(`Sign in as ${AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.addInitScript(recorder, SECTIONS);

  for (let run = 1; run <= RUNS; run++) {
    errors.length = 0;
    const response = await page.goto(`${BASE}/`, { waitUntil: "commit" });
    let shot = null;
    if (process.env.SHOTS_DIR) {
      // Best effort: a picture of the page while a placeholder is still on screen.
      const caught = await page.waitForFunction(() => document.querySelector("#main h1") && document.querySelector("#main [aria-busy='true']"), null, { timeout: 15_000, polling: 16 }).then(() => true, () => false);
      if (caught) {
        shot = `${process.env.SHOTS_DIR}/hosted-${width}-run${run}-partial.png`;
        await page.screenshot({ path: shot }).catch(() => (shot = null));
      }
    }
    await page.waitForFunction(() => document.querySelector("#main h1") && !document.querySelector("#main [aria-busy]"), null, { timeout: 60_000 });
    await page.waitForLoadState("load");
    const rec = await page.evaluate(() => window.__adgStream);
    const encoding = (await response.headerValue("content-encoding")) ?? "identity";
    const nav = await page.evaluate(() => { const n = performance.getEntriesByType("navigation")[0]; return { response_start: Math.round(n.responseStart), response_end: Math.round(n.responseEnd), dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd), protocol: n.nextHopProtocol }; });
    const counts = await page.evaluate((ids) => Object.fromEntries(ids.map((id) => [id, document.querySelectorAll(`#${id}`).length])), SECTIONS);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

    const present = SECTIONS.filter((id) => counts[id] > 0);
    const times = present.map((id) => rec.visible[id]).filter((t) => t !== undefined);
    const lastSection = Math.max(...times);
    const progressive = rec.timeline.some((s) => s.h1 && s.shown.length > 0 && s.busy.length > 0);
    const shellOnly = rec.timeline.some((s) => s.h1 && s.busy.length > 0);
    const problems = [];
    if (rec.h1 === null) problems.push("no h1");
    if (times.length !== present.length) problems.push("a section never became visible");
    if (!(lastSection - rec.h1 >= MIN_GAP)) problems.push(`last section only ${lastSection - rec.h1} ms after the h1`);
    if (!shellOnly) problems.push("never saw the shell with a loading placeholder");
    if (!progressive) problems.push("never saw a resolved section beside a loading placeholder");
    if (Object.values(counts).some((n) => n > 1)) problems.push(`duplicate section ${JSON.stringify(counts)}`);
    if (sideways > 0) problems.push(`scrolls sideways by ${sideways}px`);
    const hydration = errors.filter((t) => /hydrat|#418|#423|#425/i.test(t));
    if (hydration.length) problems.push(`hydration: ${hydration[0]}`);
    const pageErrors = errors.filter((t) => !/Failed to load resource/.test(t));
    if (pageErrors.length) problems.push(`errors: ${pageErrors[0]}`);

    results.push({ width, run, encoding, nav, h1: rec.h1, visible: rec.visible, attached: rec.attached, timeline: rec.timeline, counts, sideways_px: sideways, progressive, errors: errors.length, screenshot: shot ? shot.split("/").pop() : null, problems });
    console.log(`${problems.length ? "FAIL" : "PASS"}  ${width}px run ${run} — h1 ${rec.h1} ms, ${present.map((id) => `${id.slice(9)} ${rec.visible[id]}`).join(", ")}, ${nav.protocol} ${encoding}${problems.length ? ` — ${problems.join("; ")}` : ""}`);
    failed += problems.length ? 1 : 0;
  }

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Findings", exact: true }).click();
  const navOk = await page.waitForFunction(() => document.title === "Findings — ADG", null, { timeout: 15_000 }).then(() => true, () => false);
  console.log(`${navOk ? "PASS" : "FAIL"}  ${width}px client navigation to Findings`);
  if (!navOk) failed += 1;
  results.push({ width, client_navigation_findings: navOk });
  await context.close();
}
await browser.close();
if (process.env.REPORT_JSON) writeFileSync(process.env.REPORT_JSON, JSON.stringify(results, null, 1));
console.log(`\n${failed ? `${failed} failed` : "all passed"}`);
process.exit(failed ? 1 : 0);
