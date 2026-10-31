#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-10 browser check: loads the Overview in Chromium at 1360 and 375 px and
 * records, from navigation start, when the h1, each resolved section and the end of streaming
 * (no `[aria-busy]` left in #main) appear. Then checks the order (fast sections before the
 * delayed Reviews), duplicates, sideways scroll, page and hydration errors, and one client
 * navigation afterwards.
 *
 *   BASE=http://127.0.0.1:10090 ORIGIN_KEY_FILE=… SIGN_IN_AS=admin DELAY_MS=4000 \
 *   CHROMIUM_PATH=… node browser-stream.mjs
 *
 * Read-only apart from the development sign-in. Prints no cookie or key.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));
const BASE = process.env.BASE.replace(/\/+$/, "");
const AS = process.env.SIGN_IN_AS ?? "admin";
const DELAY = Number(process.env.DELAY_MS ?? 4000);
const key = process.env.ORIGIN_KEY_FILE ? readFileSync(process.env.ORIGIN_KEY_FILE, "utf8").trim() : null;
const SECTIONS = ["overview-attention", "overview-changes", "overview-health", "overview-reviews"];

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const results = [];
let failed = 0;
for (const [width, height] of [[1360, 900], [375, 812]]) {
  const context = await browser.newContext({ viewport: { width, height }, ...(key ? { extraHTTPHeaders: { "x-adg-origin-key": key } } : {}) });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: new RegExp(`Sign in as ${AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));

  const start = Date.now();
  const seen = {};
  const watch = (name, selector, state = "attached") =>
    page.locator(selector).first().waitFor({ state, timeout: 30_000 }).then(() => (seen[name] = Date.now() - start), () => (seen[name] = null));
  const nav = page.goto(`${BASE}/`, { waitUntil: "commit" }).then(() => (seen.commit = Date.now() - start));
  await Promise.all([
    nav,
    watch("h1", "#main h1", "visible"),
    ...SECTIONS.map((id) => watch(id, `#${id}`, "visible")),
    page.waitForFunction(() => document.querySelector("#main h1") && !document.querySelector("#main [aria-busy]"), null, { timeout: 30_000 }).then(() => (seen.settled = Date.now() - start), () => (seen.settled = null)),
  ]);
  await page.waitForLoadState("load");
  seen.load = Date.now() - start;

  const counts = await page.evaluate((ids) => Object.fromEntries(ids.map((id) => [id, document.querySelectorAll(`#${id}`).length])), SECTIONS);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const fast = SECTIONS.filter((id) => id !== "overview-reviews").map((id) => seen[id]);
  const problems = [];
  if (fast.some((t) => t === null) || seen["overview-reviews"] === null) problems.push("a section never appeared");
  if (!(Math.max(...fast) < seen["overview-reviews"])) problems.push("a fast section did not precede Reviews");
  if (!(seen.h1 < seen["overview-reviews"] - DELAY / 2)) problems.push("the h1 did not arrive well before Reviews");
  if (!(seen["overview-reviews"] >= DELAY)) problems.push("Reviews arrived before its delay elapsed");
  if (Object.values(counts).some((n) => n !== 1)) problems.push(`duplicate or missing section ${JSON.stringify(counts)}`);
  if (sideways > 0) problems.push(`scrolls sideways by ${sideways}px`);
  const hydration = errors.filter((t) => /hydrat|#418|#423|#425/i.test(t));
  if (hydration.length) problems.push(`hydration: ${hydration[0]}`);
  const pageErrors = errors.filter((t) => !/Failed to load resource/.test(t));
  if (pageErrors.length) problems.push(`errors: ${pageErrors[0]}`);

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Findings", exact: true }).click();
  const navOk = await page.waitForFunction(() => document.title === "Findings — ADG", null, { timeout: 15_000 }).then(() => true, () => false);
  if (!navOk) problems.push("client navigation to Findings did not land");

  results.push({ width, ms: seen, counts, sideways_px: sideways, errors: errors.length, problems });
  console.log(`${problems.length ? "FAIL" : "PASS"}  ${width}px — ${JSON.stringify(seen)}${problems.length ? ` — ${problems.join("; ")}` : ""}`);
  failed += problems.length ? 1 : 0;
  await context.close();
}
await browser.close();
if (process.env.REPORT_JSON) (await import("node:fs")).writeFileSync(process.env.REPORT_JSON, JSON.stringify(results, null, 1));
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
