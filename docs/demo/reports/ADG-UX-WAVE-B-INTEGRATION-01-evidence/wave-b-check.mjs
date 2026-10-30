#!/usr/bin/env node
/**
 * ADG-UX-WAVE-B-INTEGRATION-01: the cross-lane checks no source gate makes, because each Wave B
 * lane ran without the other two. PERF-01's `frontend/cloudflare/checks/overview.mjs` covers the
 * streamed Overview, ENTITY-02's `ADG-UX-ENTITY-02-evidence/entity-check.mjs` the identity copy,
 * TITLES-03's `frontend/cloudflare/checks/titles.mjs` the titles. This walks one signed-in
 * session through all three on the real stack:
 *
 * 1. the Overview, with the review-queue read delayed 4 s, is titled "Overview — ADG" while it
 *    streams: h1 and the other sections first, Reviews after its read, focus left where the
 *    user put it, Data health exactly once;
 * 2. the primary nav to Findings: "Findings — ADG", the identity rule title, no reload;
 * 3. the nav to Identities, then a user: "Identity — ADG", no generic "principal", no SID in
 *    the title, no reload;
 * 4. Back to the Overview and Forward again: each title restored, still no reload;
 * 5. Server, Share and Directory detail titles: generic, no identifier;
 * 6. every route title is static metadata (no generateMetadata, so no read for a title);
 * 7. no page error and no hydration error throughout.
 *
 * Same topology as overview.mjs: Next.js ADG_INTERNAL_API_URL → this script's recording proxy
 * on $PROXY_PORT → the API. Run it when overview.mjs is not running (same port). Read-only
 * apart from development sign-ins.
 *
 *   WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 PROXY_PORT=8100 APP_DIR=frontend/app \
 *   CHROMIUM_PATH=… SCREENSHOT_DIR=… REPORT_JSON=… node wave-b-check.mjs
 */
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const APP_DIR = process.env.APP_DIR ?? "frontend/app";
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

/* Recording proxy: every API read the web tier makes, with an optional per-path delay. */
const requests = [];
const delays = new Map();
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const entry = { path: url.pathname, start: performance.now(), end: null };
  requests.push(entry);
  const forward = () => {
    const upstream = http.request(url, { method: req.method, headers: { ...req.headers, host: new URL(API).host } }, (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
      up.on("end", () => (entry.end = performance.now()));
    });
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.pipe(upstream);
  };
  const delay = delays.get(url.pathname);
  if (delay) setTimeout(forward, delay);
  else forward();
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

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
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}

const SUFFIX = " — ADG";
// What a title must never carry: a SID, a GUID, a DN, a UNC or drive path, a scope token.
const IDENTIFIER = /S-1-\d|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b(?:CN|OU|DC)=|\\|[A-Z]:\/|principal:/i;
// ENTITY-02's allowed terms of art and deferred backend prose (its report §5).
const ALLOWED = [/foreign security principals?/gi, /user principal name/gi, /\bprincipal:\S+/g];
const DEFERRED = [/This principal holds rights[^.]*\./gi, /No entry on either ACL names this principal[^.]*\./gi, /No run has described this principal[^.]*\./gi, /this principal's access depends on the order of the entries\.?/gi, /Windows grants every principal full access[^.]*\./gi];
function genericPrincipal(text) {
  const rest = [...ALLOWED, ...DEFERRED].reduce((acc, pattern) => acc.replace(pattern, ""), text);
  return [...rest.matchAll(/[^.\n]{0,40}\bprincipals?\b[^.\n]{0,40}/gi)].map((m) => m[0].trim());
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
context.setDefaultTimeout(60_000);
const signIn = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username: "admin" } });
expect(signIn.ok(), `sign-in as admin: HTTP ${signIn.status()}`);
const page = await context.newPage();
page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(`${page.url()}: ${message.text()}`);
});
page.on("response", (response) => {
  if (response.status() >= 400) consoleErrors.push(`${page.url()}: HTTP ${response.status()} ${new URL(response.url()).pathname}`);
});
const documents = [];
page.on("request", (request) => {
  if (request.frame() === page.mainFrame() && request.resourceType() === "document") documents.push(new URL(request.url()).pathname);
});
const nav = () => page.getByRole("navigation", { name: "Primary" });
const h1 = async () => (await page.locator("h1").innerText()).trim();
async function titled(want) {
  await page.waitForFunction((t) => document.title === t, want, { timeout: 15_000 }).catch(() => {});
  return page.title();
}
async function settled() {
  await page.waitForFunction(() => !document.querySelector('#main [aria-busy="true"]'), null, { timeout: 20_000 });
}
async function shot(name) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage: false });
}

/* Milestones on the page's own clock: h1, each section (real, not its placeholder) inside #main, and the title then. */
const SECTION_IDS = { "Needs attention": "overview-attention", "Recent changes": "overview-changes", Reviews: "overview-reviews", "Data health": "overview-health" };
await page.addInitScript((ids) => {
  if (location.pathname !== "/") return;
  const seen = (window.__waveB = { h1: null, titleAtH1: null, ready: {} });
  const scan = () => {
    const main = document.getElementById("main");
    if (!main) return;
    const now = performance.now();
    if (seen.h1 === null && main.querySelector("h1")) {
      seen.h1 = now;
      seen.titleAtH1 = document.title;
    }
    for (const id of ids) if (seen.ready[id] == null && main.querySelector(`section[aria-labelledby="${id}"]`)) seen.ready[id] = now;
  };
  new MutationObserver(scan).observe(document, { childList: true, subtree: true });
}, Object.values(SECTION_IDS));

const QUEUE = "/api/v1/governance/queue";
let marker = null;

await check("Overview with a 4 s review-queue read: titled while streaming, Reviews alone waits, focus kept, Data health once", async () => {
  delays.set(QUEUE, 4000);
  const from = performance.now();
  const navigation = page.goto(`${WEB}/`, { waitUntil: "load" });
  // Mid-stream: another section is in, Reviews is still its busy placeholder; the user moves focus.
  const link = page.locator('#main section[aria-labelledby="overview-attention"] a').first();
  await link.waitFor({ timeout: 3500 });
  const midTitle = await page.title();
  const pending = await page.locator('#main section[aria-labelledby="overview-reviews-pending"]').getAttribute("aria-busy");
  await link.focus();
  const focused = await link.getAttribute("href");
  await shot("overview-reviews-midstream-1440");
  const response = await navigation;
  expect(response?.status() === 200, `HTTP ${response?.status()}`);
  await page.getByRole("region", { name: "Reviews", exact: true }).waitFor({ timeout: 15_000 });
  await settled();
  delays.clear();
  const { perf, origin, active } = await page.evaluate(() => ({
    perf: window.__waveB,
    origin: performance.timeOrigin,
    active: { href: document.activeElement?.getAttribute("href"), inAttention: !!document.activeElement?.closest('section[aria-labelledby="overview-attention"]') },
  }));
  const read = requests.find((r) => r.start >= from && r.path === QUEUE);
  expect(read?.end != null, "the review-queue read was not made");
  const readEnd = Math.round(performance.timeOrigin + read.end - origin);
  const sections = Object.fromEntries(Object.entries(SECTION_IDS).map(([name, id]) => [name, Math.round(perf.ready[id])]));
  expect(midTitle === `Overview${SUFFIX}` && perf.titleAtH1 === `Overview${SUFFIX}`, `title mid-stream "${midTitle}", at h1 "${perf.titleAtH1}"`);
  expect(pending === "true", `Reviews placeholder aria-busy=${pending} mid-stream`);
  expect(readEnd >= 4000, `review-queue read ended at ${readEnd} ms`);
  expect(perf.h1 < readEnd - 2000, `h1 at ${Math.round(perf.h1)} ms, read end ${readEnd} ms`);
  for (const name of ["Needs attention", "Recent changes", "Data health"]) expect(sections[name] < readEnd - 2000, `${name} at ${sections[name]} ms, read end ${readEnd} ms`);
  expect(sections.Reviews >= readEnd, `Reviews at ${sections.Reviews} ms, before its read ended (${readEnd} ms)`);
  expect(active.inAttention && active.href === focused, `focus moved to ${JSON.stringify(active)} (was ${focused})`);
  const health = await page.locator('#main section[aria-labelledby="overview-health"]').count();
  const healthPending = await page.locator('#main section[aria-labelledby="overview-health-pending"]').count();
  expect(health === 1 && healthPending === 0, `Data health ${health}, placeholders ${healthPending}`);
  expect((await page.locator("h1").count()) === 1 && (await h1()) === "Overview", "one h1, Overview");
  marker = await page.evaluate(() => (window.__waveBDoc = Math.random()));
  measurements.overview = { h1: Math.round(perf.h1), sections, reviewQueueReadEnd: readEnd, title: midTitle, focus: focused };
  await shot("overview-settled-1440");
  return `title "${midTitle}" from first paint; h1 ${Math.round(perf.h1)} ms; ${Object.entries(sections).map(([k, v]) => `${k} ${v}`).join(", ")} ms; queue read ends ${readEnd} ms; focus kept on ${focused}`;
});

const sameDocument = () => page.evaluate((m) => window.__waveBDoc === m, marker);

await check("primary nav to Findings: \"Findings — ADG\", identity rule title, no reload", async () => {
  await nav().getByRole("link", { name: "Findings", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/risks");
  const title = await titled(`Findings${SUFFIX}`);
  expect(title === `Findings${SUFFIX}`, `title "${title}"`);
  expect((await h1()) === "Findings", `h1 "${await h1()}"`);
  const text = await page.locator("main").textContent();
  expect(text.includes("One identity reaches a resource by several independent routes"), "identity rule title not rendered");
  expect(!text.includes("One principal reaches"), "old rule title rendered");
  expect(await sameDocument(), "document reloaded");
  return `title "${title}", rule title says identity`;
});

let identityHref = null;
await check("nav to Identities, then a user: \"Identity — ADG\", Identity copy, no SID in the title, no reload", async () => {
  await nav().getByRole("link", { name: "Identities", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/identities");
  expect((await titled(`Identities${SUFFIX}`)) === `Identities${SUFFIX}`, `Identities title "${await page.title()}"`);
  await page.locator("main a[href='/identities/users']").first().click();
  await page.waitForURL((url) => url.pathname === "/identities/users");
  const user = page.locator("main a[href^='/identities/principal/']").first();
  identityHref = await user.getAttribute("href");
  const before = requests.length;
  await user.click();
  await page.waitForURL((url) => url.pathname.startsWith("/identities/principal/"));
  await page.locator("h1 .title-kind").waitFor();
  const title = await titled(`Identity${SUFFIX}`);
  expect(title === `Identity${SUFFIX}`, `title "${title}"`);
  expect(!IDENTIFIER.test(title), `identifier in title "${title}"`);
  const kind = (await page.locator("h1 .title-kind").innerText()).trim();
  expect(/user/i.test(kind), `kind chip "${kind}"`);
  const names = await page.evaluate(() =>
    [...document.querySelectorAll("main [aria-label], main [title], main th, main label, main caption")].map((n) => [n.getAttribute("aria-label"), n.getAttribute("title"), n.textContent].filter(Boolean).join(" ")).join("\n"),
  );
  const found = genericPrincipal(`${await page.locator("main").innerText()}\n${names}`);
  expect(found.length === 0, `generic principal: ${found.join(" ‖ ")}`);
  expect(await sameDocument(), "document reloaded");
  await page.waitForLoadState("networkidle");
  measurements.identityReads = requests.slice(before).map((r) => r.path);
  await shot("identity-user-1440");
  return `h1 "${await h1()}", title "${title}", ${measurements.identityReads.length} API reads for the render`;
});

await check("Back to the Overview and Forward again: every title restored, no reload", async () => {
  const seen = [];
  for (const [path, want] of [["/identities/users", "Identities"], ["/identities", "Identities"], ["/risks", "Findings"], ["/", "Overview"]]) {
    await page.goBack();
    await page.waitForURL((url) => url.pathname === path);
    const title = await titled(`${want}${SUFFIX}`);
    expect(title === `${want}${SUFFIX}`, `Back to ${path}: "${title}"`);
    seen.push(`← ${want}`);
  }
  await settled();
  expect((await h1()) === "Overview", "Overview h1 after Back");
  await page.goForward();
  await page.waitForURL((url) => url.pathname === "/risks");
  const forward = await titled(`Findings${SUFFIX}`);
  expect(forward === `Findings${SUFFIX}`, `Forward: "${forward}"`);
  seen.push("→ Findings");
  expect(await sameDocument(), "document reloaded");
  return seen.join(" ");
});

let serverHref = null;
await check("Server, Share and Directory detail titles are generic and carry no identifier", async () => {
  const seen = [];
  await page.goto(`${WEB}/resources`, { waitUntil: "networkidle" });
  const server = (serverHref = await page.locator("main a[href^='/resources/server']").first().getAttribute("href"));
  await page.goto(`${WEB}${server}`, { waitUntil: "networkidle" });
  const share = await page.locator("main a[href^='/resources/share']").first().getAttribute("href");
  await page.goto(`${WEB}${share}`, { waitUntil: "networkidle" });
  const directory = await page.locator("main a[href^='/resources/directory']").first().getAttribute("href");
  for (const [label, href] of [["Server", server], ["Share", share], ["Directory", directory], ["Identity", identityHref]]) {
    expect(href, `no ${label} link`);
    const response = await page.goto(`${WEB}${href}`, { waitUntil: "load" });
    expect(response?.status() === 200, `${label}: HTTP ${response?.status()}`);
    const title = await page.title();
    const sent = (await response.text()).match(/<title>([^<]*)<\/title>/)?.[1];
    expect(title === `${label}${SUFFIX}` && sent === `${label}${SUFFIX}`, `${label}: document "${title}", server "${sent}"`);
    expect(!IDENTIFIER.test(title), `${label}: identifier in "${title}"`);
    seen.push(`"${title}"`);
  }
  return seen.join(", ");
});

await check("every route title is static metadata: no generateMetadata, so no read exists for a title", async () => {
  const files = readdirSync(APP_DIR, { recursive: true }).filter((f) => /(^|\/)(page|layout|not-found)\.tsx$/.test(f));
  const dynamic = files.filter((f) => /generateMetadata/.test(readFileSync(join(APP_DIR, f), "utf8")));
  expect(dynamic.length === 0, `generateMetadata in ${dynamic.join(", ")}`);
  const titles = files.flatMap((f) => [...readFileSync(join(APP_DIR, f), "utf8").matchAll(/export const metadata[^;]*title:\s*([^}]+?)\s*}/g)].map((m) => `${f}: ${m[1]}`));
  const nonLiteral = titles.filter((t) => !/:\s*(?:"[^"]*"|(?:documentTitle|sectionTitle)\("[^"]*"\))\s*,?$/.test(t));
  expect(nonLiteral.length === 0, `non-literal titles: ${nonLiteral.join(" | ")}`);
  return `${files.length} route files, ${titles.length} titles, all literal`;
});

await check("1440/1024/768/375/320: streaming Overview placeholder, Findings, a user and a server fit without sideways scroll", async () => {
  const seen = [];
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    delays.set(QUEUE, 3000);
    const navigation = page.goto(`${WEB}/`, { waitUntil: "load" });
    await page.locator('#main section[aria-labelledby="overview-reviews-pending"]').waitFor({ timeout: 2500 });
    const midStream = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    if (width === 320) await shot("overview-reviews-midstream-320");
    await navigation;
    await settled();
    delays.clear();
    const wide = [`/ mid-stream +${midStream}`];
    for (const path of ["/", "/risks?rule=redundant_access_paths", identityHref, serverHref]) {
      if (path !== "/") await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
      const over = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      wide.push(`${path.split("?")[0].split("/").slice(0, 3).join("/") || "/"} +${over}`);
    }
    const bad = wide.filter((w) => !w.endsWith("+0"));
    expect(bad.length === 0, `${width}px sideways scroll: ${bad.join(", ")}`);
    if (width === 320) await shot("server-320");
    seen.push(width);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  return `no overflow at ${seen.join("/")} px`;
});

await check("no page error and no hydration error across the walk", async () => {
  const hydration = consoleErrors.filter((e) => /hydrat|did not match|#418|#423|#425/i.test(e));
  expect(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);
  expect(hydration.length === 0, `hydration: ${hydration.join(" | ")}`);
  return `0 page errors, ${consoleErrors.length} console errors, none about hydration; ${documents.length} document loads (${documents.join(", ")})`;
});

await browser.close();
proxy.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
if (consoleErrors.length) console.log(`console errors:\n  ${consoleErrors.join("\n  ")}`);
if (REPORT) writeFileSync(REPORT, `${JSON.stringify({ results, measurements, pageErrors, consoleErrors, documents }, null, 2)}\n`);
process.exit(failed ? 1 : 0);
