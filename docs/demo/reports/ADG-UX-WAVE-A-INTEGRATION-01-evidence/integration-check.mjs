#!/usr/bin/env node
/**
 * ADG-UX-WAVE-A-INTEGRATION-01: the checks neither lane's gate makes, because each lane ran
 * without the other. UX-06's `frontend/cloudflare/checks/overview.mjs` covers the Overview body;
 * UX-01's `ADG-UX-01-evidence/nav-check.mjs` covers the grouped nav against a stub API on
 * /risks. This covers the two together, on the real stack:
 *
 * - the grouped nav on the Overview (as `admin`, every capability) at 1440/1280/1024/768/375/320 px (groups, one current
 *   page, no sideways scroll, every nav link inside the viewport);
 * - the rendered copy against the UX-01 terminology contract;
 * - nav-driven client navigation from the Overview, and Back;
 * - Tab order: skip link, the 10 nav links in group order, then the Overview body;
 * - `/runtime` read once per render on `/`, `/demo` and `/access/investigate`, and again on
 *   the next render (request-scoped, not a global cache).
 *
 * Same topology as overview.mjs: Next.js ADG_INTERNAL_API_URL → this script's recording proxy on
 * $PROXY_PORT → the API. Run it when overview.mjs is not running (same port). Read-only apart
 * from development sign-ins.
 *
 *   WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 PROXY_PORT=8100 \
 *   CHROMIUM_PATH=… SCREENSHOT_DIR=… REPORT_JSON=… node integration-check.mjs
 */
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

const requests = [];
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  requests.push({ path: url.pathname, at: Date.now() });
  const upstream = http.request(url, { method: req.method, headers: { ...req.headers, host: new URL(API).host } }, (up) => {
    res.writeHead(up.statusCode, up.headers);
    up.pipe(res);
  });
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502);
    res.end();
  });
  req.pipe(upstream);
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

const results = [];
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
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
async function pageFor(username, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(60_000);
  const login = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username } });
  expect(login.ok(), `sign-in as ${username}: HTTP ${login.status()}`);
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(`${username}: ${error}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${username}: ${message.text()}`);
  });
  return page;
}
const nav = (page) => page.getByRole("navigation", { name: "Primary" });
async function groups(page) {
  return nav(page).locator("ul[aria-labelledby]").evaluateAll((uls) => uls.map((ul) => document.getElementById(ul.getAttribute("aria-labelledby")).textContent.trim()));
}
async function current(page) {
  return nav(page).locator('[aria-current="page"]').allInnerTexts();
}
const NAV_ORDER = "Overview,Findings,Access,Changes,Identities,Resources,Reviews,What-if,Collectors,Settings";
const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 768, height: 1024 },
  { width: 375, height: 812 },
  { width: 320, height: 640 },
];

for (const viewport of VIEWPORTS) {
  await check(`admin ${viewport.width}px: Overview shows the four nav groups, Overview current, no sideways scroll, every nav link in view`, async () => {
    const page = await pageFor("admin", viewport);
    const response = await page.goto(`${WEB}/`);
    expect(response?.status() === 200, `HTTP ${response?.status()}`);
    expect((await page.locator("h1").innerText()).trim() === "Overview", "h1");
    const names = await groups(page);
    expect(names.join() === "Investigate,Directory,Act,System", names.join());
    const cur = await current(page);
    expect(cur.length === 1 && cur[0] === "Overview", `current=${cur}`);
    const links = await nav(page).getByRole("link").allInnerTexts();
    expect(links.join() === NAV_ORDER, links.join());
    const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    expect(sw <= iw, `scrollWidth ${sw} > ${iw}`);
    const outside = await nav(page).getByRole("link").evaluateAll((as) => as.filter((a) => { const r = a.getBoundingClientRect(); return r.left < 0 || r.right > innerWidth || r.width === 0; }).map((a) => a.textContent));
    expect(outside.length === 0, `nav links outside the viewport: ${outside}`);
    // The four operational sections are named regions (UX-06); the two static cards are h2-headed, as on main.
    for (const name of ["Needs attention", "Recent changes", "Reviews", "Data health"]) {
      expect((await page.getByRole("region", { name, exact: true }).count()) === 1, `no "${name}" region`);
    }
    for (const name of ["Where to start", "What ADG will not do"]) {
      expect((await page.getByRole("heading", { level: 2, name, exact: true }).count()) === 1, `no "${name}" heading`);
    }
    if (SCREENSHOTS && [1440, 375].includes(viewport.width)) await page.screenshot({ path: join(SCREENSHOTS, `integrated-overview-${viewport.width}.png`), fullPage: true });
    if (SCREENSHOTS && viewport.width === 1440) await nav(page).screenshot({ path: join(SCREENSHOTS, "integrated-nav-1440.png") });
    if (SCREENSHOTS && viewport.width === 320) await page.getByRole("region", { name: "Data health", exact: true }).screenshot({ path: join(SCREENSHOTS, "integrated-data-health-320.png") });
    await page.context().close();
    return `${sw} <= ${iw}`;
  });
}

await check("terminology: the rendered Overview and nav use no retired user-facing term (UX-01 §3)", async () => {
  const page = await pageFor("admin");
  await page.goto(`${WEB}/`);
  // Text plus the attributes a screen reader or tooltip exposes.
  let text = await page.evaluate(() => {
    const roots = [document.querySelector('nav[aria-label="Primary"]'), document.querySelector("#main")];
    const attrs = roots.flatMap((r) => [...r.querySelectorAll("[title], [aria-label]")].map((e) => `${e.getAttribute("title") ?? ""} ${e.getAttribute("aria-label") ?? ""}`));
    return [...roots.map((r) => r.textContent), ...attrs].join(" ");
  });
  // Rule titles are served by the backend's rule catalog (unchanged in this lane): report, don't fail.
  const login = await (await fetch(`${API}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "admin" }) })).json();
  const { rules } = await (await fetch(`${API}/api/v1/risks/rules`, { headers: { authorization: `Bearer ${login.access_token}` } })).json();
  const backendTitles = rules.map((r) => r.title).filter((t) => text.includes(t));
  const supplied = backendTitles.filter((t) => /\b(risks?|principals?|dependenc(y|ies)|places?)\b/i.test(t));
  text = backendTitles.reduce((acc, t) => acc.split(t).join(" "), text);
  const retired = [/\brisks?\b/gi, /\bprincipals?\b/gi, /\bdependenc(?:y|ies)\b/gi, /\bplaces?\b/gi];
  const hits = retired.flatMap((re) => [...text.matchAll(re)].map((m) => text.slice(Math.max(0, m.index - 30), m.index + 30).replace(/\s+/g, " ")));
  expect(hits.length === 0, `retired terms: ${hits.join(" | ")}`);
  expect(text.includes("Findings evaluation"), "no 'Findings evaluation'");
  const findings = await nav(page).getByRole("link", { name: "Findings", exact: true }).getAttribute("href");
  expect(findings === "/risks", `Findings href ${findings}`);
  await page.context().close();
  return `${text.length} characters scanned; Findings → ${findings}; backend-supplied rule titles with a retired term: ${supplied.length ? supplied.map((t) => `"${t}"`).join(", ") : "none"}`;
});

await check("client navigation: grouped nav from the Overview to Findings and Reviews, no reload, current item follows, Back returns", async () => {
  const page = await pageFor("admin");
  await page.goto(`${WEB}/`);
  const marker = await page.evaluate(() => (window.__waveA = Math.random()));
  const seen = [];
  for (const [label, path, h1] of [["Findings", "/risks", "Findings"], ["Reviews", "/governance", "Access reviews"]]) {
    await nav(page).getByRole("link", { name: label, exact: true }).click();
    await page.waitForURL((url) => url.pathname === path);
    await page.waitForFunction((want) => document.querySelector('nav[aria-label="Primary"] [aria-current="page"]')?.textContent.trim() === want, label);
    expect((await page.locator("h1").innerText()).trim() === h1, `h1 on ${path}`);
    expect(await page.evaluate((m) => window.__waveA === m, marker), `document reloaded on the way to ${path}`);
    await page.goBack();
    await page.waitForURL((url) => url.pathname === "/");
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Primary"] [aria-current="page"]')?.textContent.trim() === "Overview");
    expect((await page.locator("h1").innerText()).trim() === "Overview", "h1 after Back");
    expect(await page.evaluate((m) => window.__waveA === m, marker), "document reloaded on Back");
    seen.push(`${label} ↔ Overview`);
  }
  await page.context().close();
  return seen.join(", ");
});

await check("keyboard on the Overview: skip link, then the 10 nav links in group order with visible focus, then the Overview body", async () => {
  const page = await pageFor("admin");
  await page.goto(`${WEB}/`);
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement.textContent.trim());
  expect(first === "Skip to main content", `first stop: ${first}`);
  const inNav = [];
  let after = null;
  for (let i = 0; i < 40 && after === null; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      const style = getComputedStyle(el);
      return { nav: !!el.closest('nav[aria-label="Primary"]'), main: !!el.closest("#main"), tag: el.tagName, text: el.textContent.trim(), focus: style.outlineStyle !== "none" || style.boxShadow !== "none" };
    });
    if (info.nav) {
      expect(info.tag === "A" && info.focus, `nav stop ${info.tag} "${info.text}" focus=${info.focus}`);
      inNav.push(info.text);
    } else if (inNav.length > 0) after = info;
  }
  expect(inNav.join() === NAV_ORDER, inNav.join());
  expect(after?.main, `after the nav, focus went to ${after?.tag} "${after?.text}" outside #main`);
  await page.context().close();
  return `${inNav.length} nav links, then "${after.text.slice(0, 40)}" in #main`;
});

await check("viewer on the Overview: no Act group, no Reviews section, Overview current", async () => {
  const page = await pageFor("viewer");
  await page.goto(`${WEB}/`);
  const names = await groups(page);
  expect(names.join() === "Investigate,Directory,System", names.join());
  expect((await page.getByRole("region", { name: "Reviews", exact: true }).count()) === 0, "Reviews section rendered");
  const cur = await current(page);
  expect(cur.length === 1 && cur[0] === "Overview", `current=${cur}`);
  await page.context().close();
  return names.join(" / ");
});

for (const path of ["/", "/demo", "/access/investigate"]) {
  await check(`/runtime: read once per render on ${path}, and again on the next render (request-scoped)`, async () => {
    const page = await pageFor("planner");
    const counts = [];
    for (let i = 0; i < 2; i++) {
      const from = requests.length;
      const response = await page.goto(`${WEB}${path}`, { waitUntil: "load" });
      expect(response?.status() === 200, `HTTP ${response?.status()}`);
      const reads = requests.slice(from).map((r) => r.path);
      const runtime = reads.filter((p) => p === "/runtime").length;
      const dupes = reads.filter((p, j) => reads.indexOf(p) !== j);
      expect(runtime === 1, `render ${i + 1}: /runtime read ${runtime} times`);
      expect(dupes.length === 0, `render ${i + 1}: duplicate reads ${dupes}`);
      counts.push(reads.length);
    }
    await page.context().close();
    return `renders read ${counts.join(" and ")} paths, /runtime once each, no duplicates`;
  });
}

await check("no page errors", async () => {
  expect(pageErrors.length === 0, pageErrors.join(" | "));
  return `${consoleErrors.length} console error(s)${consoleErrors.length ? `: ${[...new Set(consoleErrors)].join(" | ").slice(0, 300)}` : ""}`;
});

await browser.close();
proxy.close();
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} passed`);
if (REPORT) writeFileSync(REPORT, JSON.stringify({ passed, total: results.length, results, consoleErrors: [...new Set(consoleErrors)], pageErrors }, null, 2));
process.exit(passed === results.length ? 0 : 1);
