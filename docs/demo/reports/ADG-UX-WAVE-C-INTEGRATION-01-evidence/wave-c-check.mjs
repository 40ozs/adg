#!/usr/bin/env node
/**
 * ADG-UX-WAVE-C-INTEGRATION-01: the cross-lane checks no source gate makes, because each Wave C
 * lane ran without the other two. BACKEND-COPY-04's `copy-check.mjs` covers the identity
 * wording, DEEP-TITLES-06's `frontend/cloudflare/checks/titles.mjs` the deeper titles,
 * RECENT-05B's `frontend/cloudflare/checks/recently-viewed.mjs` the Recently viewed contract.
 * This walks one signed-in tab through all three on the real stack:
 *
 * 1. the Overview with the review-queue read delayed 4 s: streams as in Wave B, and Recently
 *    viewed is in the first HTML, outside every busy slot, after the operational sections and
 *    before "Where to start";
 * 2. client navigation to a user: "Identity — ADG", no generic "principal", and the identity
 *    is recorded in this tab's storage with a non-UPN label;
 * 3. client navigation to Changes, then "Compare two points in time": the deep title arrives
 *    on the client move, the nav marks Changes;
 * 4. Back and Forward across those moves: every title and the nav state restored, no reload;
 * 5. back on the Overview (client move): the identity is listed first in Recently viewed;
 * 6. a server detail records too; the access explanation is titled "Access explanation — ADG",
 *    its backend verdict says "identity", its removal rows are named, and it is not recorded;
 * 7. the Overview's web-tier API reads are the same multiset with an empty and a populated list,
 *    no API path is about history, and no browser request carries the list;
 * 8. 1440/1024/768/375/320: Overview (populated), identity, server and explanation do not
 *    scroll sideways;
 * 9. no page error and no hydration error throughout.
 *
 * Same topology as overview.mjs: Next.js ADG_INTERNAL_API_URL → this script's recording proxy
 * on $PROXY_PORT → the API. Read-only apart from development sign-ins.
 *
 *   WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 PROXY_PORT=8100 \
 *   CHROMIUM_PATH=… SCREENSHOT_DIR=… REPORT_JSON=… node wave-c-check.mjs
 */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

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

const KEY = "adg.recentlyViewed.v1";
const OWNER = createHash("sha256").update("dev:admin").digest("hex");

/* Recording proxy: every API call the web tier makes (method, path, sorted query keys), with an optional per-path delay. */
const requests = [];
const delays = new Map();
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const keys = [...new Set(url.searchParams.keys())].sort().join(",");
  const entry = { call: `${req.method} ${url.pathname}${keys ? `?${keys}` : ""}`, path: url.pathname, raw: req.url, start: performance.now(), end: null };
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
// Terms of art BACKEND-COPY-04 §7 keeps on purpose. Nothing is deferred any more.
const ALLOWED = [/foreign security principals?/gi, /user principal name/gi, /Windows grants every principal[^.]*\./gi, /the well-known SYSTEM principal/gi, /\[[^\]\n]*\]/g, /\bprincipal:\S+/g, /\bprincipal_budget\b/g];
function genericPrincipal(text) {
  const rest = ALLOWED.reduce((acc, pattern) => acc.replace(pattern, ""), text);
  return [...rest.matchAll(/[^.\n]{0,40}\bprincipal(s|'s)?\b[^.\n]{0,40}/gi)].map((m) => m[0].trim());
}
function multiset(list) {
  return [...list].sort().join("\n");
}

const login = await fetch(`${API}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "admin" }) });
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
async function apiGet(path) {
  const response = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(response.ok, `${path}: HTTP ${response.status}`);
  return response.json();
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
const browserRequests = [];
const documents = [];
page.on("request", (request) => {
  browserRequests.push({ url: request.url(), headers: JSON.stringify(request.headers()), body: request.postData() ?? "" });
  if (request.frame() === page.mainFrame() && request.resourceType() === "document") documents.push(new URL(request.url()).pathname);
});
const nav = () => page.getByRole("navigation", { name: "Primary" });
const h1 = async () => (await page.locator("h1").first().innerText()).trim();
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
const stored = () => page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) ?? "null"), KEY);
const currentNav = () => nav().locator('[aria-current="page"]').innerText().then((t) => t.trim()).catch(() => null);
const recentRows = () => page.locator('#main section[aria-labelledby="overview-recent"] li').evaluateAll((lis) => lis.map((li) => ({ text: li.textContent.trim(), href: li.querySelector("a")?.getAttribute("href") })));

/* Milestones on the page's own clock: h1, each operational section (real, not its placeholder), and Recently viewed. */
const SECTION_IDS = { "Needs attention": "overview-attention", "Recent changes": "overview-changes", Reviews: "overview-reviews", "Data health": "overview-health", "Recently viewed": "overview-recent" };
await page.addInitScript((ids) => {
  if (location.pathname !== "/") return;
  const seen = (window.__waveC = { h1: null, ready: {}, recentBusy: null });
  const scan = () => {
    const main = document.getElementById("main");
    if (!main) return;
    const now = performance.now();
    if (seen.h1 === null && main.querySelector("h1")) seen.h1 = now;
    for (const id of ids) {
      const section = main.querySelector(`section[aria-labelledby="${id}"]`);
      if (seen.ready[id] == null && section) {
        seen.ready[id] = now;
        if (id === "overview-recent") seen.recentBusy = !!section.closest("[aria-busy]");
      }
    }
  };
  new MutationObserver(scan).observe(document, { childList: true, subtree: true });
}, Object.values(SECTION_IDS));

const QUEUE = "/api/v1/governance/queue";
let marker = null;
const sameDocument = () => page.evaluate((m) => window.__waveCDoc === m, marker);

await check("Overview, 4 s review-queue read: streams, Recently viewed in the first HTML outside every busy slot, placed before \"Where to start\"", async () => {
  await page.goto(`${WEB}/login`, { waitUntil: "load" });
  await page.evaluate(() => sessionStorage.clear());
  delays.set(QUEUE, 4000);
  const from = performance.now();
  const navigation = page.goto(`${WEB}/`, { waitUntil: "load" });
  await page.locator('#main section[aria-labelledby="overview-attention"] a').first().waitFor({ timeout: 3500 });
  const pending = await page.locator('#main section[aria-labelledby="overview-reviews-pending"]').getAttribute("aria-busy");
  const recentMidStream = await page.locator('#main section[aria-labelledby="overview-recent"] h2').innerText();
  await shot("overview-midstream-1440");
  const response = await navigation;
  expect(response?.status() === 200, `HTTP ${response?.status()}`);
  const html = await response.text();
  await page.getByRole("region", { name: "Reviews", exact: true }).waitFor({ timeout: 15_000 });
  await settled();
  delays.clear();
  const { perf, origin } = await page.evaluate(() => ({ perf: window.__waveC, origin: performance.timeOrigin }));
  const read = requests.find((r) => r.start >= from && r.path === QUEUE);
  expect(read?.end != null, "the review-queue read was not made");
  const readEnd = Math.round(performance.timeOrigin + read.end - origin);
  const at = Object.fromEntries(Object.entries(SECTION_IDS).map(([name, id]) => [name, Math.round(perf.ready[id])]));
  expect(pending === "true", `Reviews placeholder aria-busy=${pending} mid-stream`);
  expect(perf.h1 < readEnd - 2000, `h1 at ${Math.round(perf.h1)} ms, read end ${readEnd} ms`);
  for (const name of ["Needs attention", "Recent changes", "Data health", "Recently viewed"]) expect(at[name] < readEnd - 2000, `${name} at ${at[name]} ms, read end ${readEnd} ms`);
  expect(at.Reviews >= readEnd, `Reviews at ${at.Reviews} ms, before its read ended (${readEnd} ms)`);
  expect(recentMidStream.trim() === "Recently viewed" && perf.recentBusy === false, `Recently viewed mid-stream "${recentMidStream}", inside busy slot ${perf.recentBusy}`);
  expect(html.includes('aria-labelledby="overview-recent"') && html.includes("Recently viewed"), "Recently viewed heading not in the server HTML");
  const order = await page.evaluate(() =>
    [...document.querySelectorAll("#main h2")].map((h) => h.textContent.trim()).filter((t) => ["Needs attention", "Recent changes", "Reviews", "Data health", "Recently viewed", "Where to start", "What ADG will not do"].includes(t)),
  );
  const pos = (t) => order.indexOf(t);
  expect(pos("Recently viewed") > pos("Reviews") && pos("Recently viewed") > pos("Recent changes") && pos("Recently viewed") > pos("Needs attention"), `order ${order.join(" > ")}`);
  expect(pos("Recently viewed") === pos("Where to start") - 1 && pos("Where to start") < pos("What ADG will not do"), `order ${order.join(" > ")}`);
  expect(order.filter((t) => t === "Data health").length === 1, `Data health ${order.filter((t) => t === "Data health").length}×`);
  const empty = await page.locator('#main section[aria-labelledby="overview-recent"]').innerText();
  expect(/Nothing yet\./.test(empty), `empty state: ${empty}`);
  measurements.overviewEmpty = requests.filter((r) => r.start >= from).map((r) => r.call);
  measurements.overview = { h1: Math.round(perf.h1), sections: at, reviewQueueReadEnd: readEnd, order };
  marker = await page.evaluate(() => (window.__waveCDoc = Math.random()));
  await shot("overview-empty-1440");
  return `h1 ${Math.round(perf.h1)} ms; ${Object.entries(at).map(([k, v]) => `${k} ${v}`).join(", ")} ms; queue read ends ${readEnd} ms; order ${order.join(" > ")}`;
});

let identityHref = null;
let identityKey = null;
await check("client nav to a user: \"Identity — ADG\", identity copy, recorded in this tab with a non-UPN label, no reload", async () => {
  await nav().getByRole("link", { name: "Identities", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/identities");
  await page.locator("main a[href='/identities/users']").first().click();
  await page.waitForURL((url) => url.pathname === "/identities/users");
  expect((await titled(`Users${SUFFIX}`)) === `Users${SUFFIX}`, `Users title "${await page.title()}"`);
  const user = page.locator("main a[href^='/identities/principal/']").first();
  identityHref = await user.getAttribute("href");
  identityKey = decodeURIComponent(identityHref.split("/")[3]);
  await user.click();
  await page.waitForURL((url) => url.pathname.startsWith("/identities/principal/"));
  await page.locator("h1 .title-kind").waitFor();
  const title = await titled(`Identity${SUFFIX}`);
  expect(title === `Identity${SUFFIX}` && !IDENTIFIER.test(title), `title "${title}"`);
  const names = await page.evaluate(() =>
    [...document.querySelectorAll("main [aria-label], main [title], main th, main label, main caption")].map((n) => [n.getAttribute("aria-label"), n.getAttribute("title"), n.textContent].filter(Boolean).join(" ")).join("\n"),
  );
  const found = genericPrincipal(`${await page.locator("main").innerText()}\n${names}`);
  expect(found.length === 0, `generic principal: ${found.join(" ‖ ")}`);
  await page.waitForFunction((key) => JSON.parse(sessionStorage.getItem(key) ?? "null")?.entries?.length > 0, KEY, { timeout: 10_000 });
  const value = await stored();
  const entry = value.entries[0];
  expect(value.owner === OWNER, `owner ${value.owner}`);
  expect(entry.kind === "identity" && entry.key === identityKey, `entry ${JSON.stringify(entry)}`);
  expect(!/@/.test(entry.label) && entry.label.length > 0, `label "${entry.label}"`);
  const heading = (await h1()).replace((await page.locator("h1 .title-kind").innerText()).trim(), "").trim();
  expect(await sameDocument(), "document reloaded");
  await shot("identity-user-1440");
  measurements.identity = { label: entry.label, heading };
  return `title "${title}", recorded "${entry.label}" (heading "${heading}")`;
});

await check("client nav to Changes, then \"Compare two points in time\": the deep title arrives on the client move, nav marks Changes, no reload", async () => {
  await nav().getByRole("link", { name: "Changes", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/changes");
  expect((await titled(`Changes${SUFFIX}`)) === `Changes${SUFFIX}`, `Changes title "${await page.title()}"`);
  await page.locator('main a[href="/changes/compare"]').first().click();
  await page.waitForURL((url) => url.pathname === "/changes/compare");
  const title = await titled(`Compare two points in time${SUFFIX}`);
  expect(title === `Compare two points in time${SUFFIX}`, `title "${title}"`);
  const current = await currentNav();
  expect(current === "Changes", `nav current "${current}"`);
  expect((await stored()).entries.length === 1, "Changes or compare was recorded");
  expect(await sameDocument(), "document reloaded");
  return `title "${title}", nav current "${current}"`;
});

await check("Back and Forward across identity, Changes and compare: titles and nav state restored, no reload", async () => {
  const seen = [];
  const steps = [
    ["back", (p) => p === "/changes", "Changes", "Changes"],
    ["back", (p) => p.startsWith("/identities/principal/"), "Identity", "Identities"],
    ["forward", (p) => p === "/changes", "Changes", "Changes"],
    ["forward", (p) => p === "/changes/compare", "Compare two points in time", "Changes"],
  ];
  for (const [move, at, want, navWant] of steps) {
    await (move === "back" ? page.goBack() : page.goForward());
    await page.waitForURL((url) => at(url.pathname));
    const title = await titled(`${want}${SUFFIX}`);
    const current = await currentNav();
    expect(title === `${want}${SUFFIX}` && current === navWant, `${move}: title "${title}", nav "${current}"`);
    seen.push(`${move === "back" ? "←" : "→"} ${want} [${current}]`);
  }
  expect(await sameDocument(), "document reloaded");
  return seen.join(" ");
});

await check("client nav back to the Overview: the identity is listed first, linking to its page, no reload", async () => {
  await nav().getByRole("link", { name: "Overview", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/");
  await settled();
  await page.locator('#main section[aria-labelledby="overview-recent"] ul').waitFor({ timeout: 10_000 });
  const rows = await recentRows();
  expect(rows.length === 1 && rows[0].href === identityHref && rows[0].text.includes(measurements.identity.label), `rows ${JSON.stringify(rows)}`);
  expect((await titled(`Overview${SUFFIX}`)) === `Overview${SUFFIX}`, `title "${await page.title()}"`);
  expect(await sameDocument(), "document reloaded");
  return `1 row: ${rows[0].text}`;
});

let serverHref = null;
let explainHref = null;
await check("server detail records; access explanation: deep title, backend verdict says identity, removal rows named, not recorded", async () => {
  await nav().getByRole("link", { name: "Resources", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/resources");
  const server = page.locator("main a[href^='/resources/server']").first();
  serverHref = await server.getAttribute("href");
  await server.click();
  await page.waitForURL((url) => url.pathname === "/resources/server");
  expect((await titled(`Server${SUFFIX}`)) === `Server${SUFFIX}`, `server title "${await page.title()}"`);
  await page.waitForFunction((key) => JSON.parse(sessionStorage.getItem(key) ?? "null")?.entries?.[0]?.kind === "server", KEY, { timeout: 10_000 });
  expect(await sameDocument(), "document reloaded on the server move");

  const demo = await apiGet("/api/v1/demo");
  const person = demo.scenarios.flatMap((s) => (s.subjects ?? []).map((x) => ({ ...x, scenario: s.key, resource: s.resource }))).find((x) => x.kind === "person" && x.scenario === "F");
  expect(person, "no scenario F person in /api/v1/demo");
  const query = new URLSearchParams({ principal: person.sid, resource: person.resource }).toString();
  const api = await apiGet(`/api/v1/access/explain?${query}`);
  explainHref = `/access/explain?${query}`;
  const before = JSON.stringify(await stored());
  const response = await page.goto(`${WEB}${explainHref}`, { waitUntil: "networkidle" });
  expect(response?.status() === 200, `explain HTTP ${response?.status()}`);
  const sent = (await response.text()).match(/<title>([^<]*)<\/title>/)?.[1];
  const title = await page.title();
  expect(title === `Access explanation${SUFFIX}` && sent === title, `title "${title}", server "${sent}"`);
  expect(/\bidentity\b/i.test(api.verdict.reason) && !/\bprincipal\b/i.test(api.verdict.reason), `API reason "${api.verdict.reason}"`);
  const text = await page.locator("main").innerText();
  expect(text.includes(api.verdict.reason), "verdict reason not rendered");
  expect(!/principal:S-1-/.test(text), "a principal:<sid> id rendered as text");
  const rows = await page.locator("main [data-removal-edge]").evaluateAll((els) => els.map((el) => [el.dataset.removalEdge, el.textContent.trim()]));
  expect(rows.length === api.removal_targets.length && rows.length > 0, `rows ${rows.length} vs ${api.removal_targets.length}`);
  expect(rows.every(([id]) => id.length > 0) && rows.some(([, label]) => label.includes(`${person.name} is a member of`)), `labels: ${rows.slice(0, 3).map((r) => r[1]).join(" | ")}`);
  expect(api.removal_targets.some((t) => t.source.startsWith("principal:")), "API no longer sends principal:<sid> sources");
  const found = genericPrincipal(text);
  expect(found.length === 0, `generic principal: ${found.join(" ‖ ")}`);
  expect(JSON.stringify(await stored()) === before, "the explanation page changed Recently viewed");
  await shot("explain-1440");
  return `verdict "${api.verdict.reason}"; ${rows.length} removal rows, first "${rows[0][1]}"; storage unchanged`;
});

await check("Overview reads: the same web-tier API calls with an empty and a populated list; no history path; no request carries the list", async () => {
  const from = performance.now();
  const response = await page.goto(`${WEB}/`, { waitUntil: "load" });
  expect(response?.status() === 200, `HTTP ${response?.status()}`);
  await settled();
  await page.locator('#main section[aria-labelledby="overview-recent"] ul').waitFor({ timeout: 10_000 });
  await page.waitForLoadState("networkidle");
  const rows = await recentRows();
  expect(rows.length === 2 && rows[0].href === serverHref && rows[1].href === identityHref, `rows ${JSON.stringify(rows)}`);
  const populated = requests.filter((r) => r.start >= from).map((r) => r.call);
  measurements.overviewPopulated = populated;
  expect(multiset(populated) === multiset(measurements.overviewEmpty), `empty [${measurements.overviewEmpty.join(", ")}] vs populated [${populated.join(", ")}]`);
  const historyPaths = requests.filter((r) => /recent|viewed|histor/i.test(r.path));
  expect(historyPaths.length === 0, `history-like API paths: ${historyPaths.map((r) => r.path).join(", ")}`);
  const value = await stored();
  const labels = value.entries.map((e) => e.label);
  const needles = [KEY, "recentlyViewed", OWNER, '"entries"', ...labels.filter((l) => l.length >= 6)];
  const leaks = [];
  for (const r of browserRequests) for (const n of needles) if (r.url.includes(n) || r.headers.includes(n) || r.body.includes(n)) leaks.push(`${n} in ${r.url}`);
  for (const r of requests) for (const n of [KEY, "recentlyViewed", OWNER, '"entries"']) if (r.raw.includes(n)) leaks.push(`${n} in API ${r.raw}`);
  expect(leaks.length === 0, `leaks: ${leaks.slice(0, 3).join(" ‖ ")}`);
  await shot("overview-populated-1440");
  return `${populated.length} API calls either way; 0 history paths; ${browserRequests.length} browser + ${requests.length} web-tier requests scanned, 0 carry the list`;
});

await check("1440/1024/768/375/320: Overview with the list, identity, server and explanation do not scroll sideways", async () => {
  const seen = [];
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const wide = [];
    for (const [name, path] of [["overview", "/"], ["identity", identityHref], ["server", serverHref], ["explain", explainHref]]) {
      await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
      if (path === "/") {
        await settled();
        await page.locator('#main section[aria-labelledby="overview-recent"] ul').waitFor({ timeout: 10_000 });
      }
      const over = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      const recentOver = path === "/" ? await page.locator('#main section[aria-labelledby="overview-recent"]').evaluate((s) => s.scrollWidth - s.clientWidth) : 0;
      if (over > 0 || recentOver > 0) wide.push(`${name} +${over}${recentOver ? ` (list +${recentOver})` : ""}`);
      if (width <= 375) await shot(`w${width}-${name}`);
    }
    expect(wide.length === 0, `${width}px: ${wide.join(", ")}`);
    seen.push(`${width} ok`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  return seen.join(", ");
});

await check("no page error, no hydration error", async () => {
  const hydration = consoleErrors.filter((e) => /hydrat|#418|did not match/i.test(e));
  expect(pageErrors.length === 0, pageErrors.slice(0, 3).join(" ‖ "));
  expect(hydration.length === 0, hydration.slice(0, 3).join(" ‖ "));
  return `${pageErrors.length} page errors, ${consoleErrors.length} console errors (${hydration.length} hydration)`;
});

measurements.documents = documents;
measurements.consoleErrors = consoleErrors;
await browser.close();
proxy.close();
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} checks passed`);
if (REPORT) writeFileSync(REPORT, JSON.stringify({ web: WEB, results, measurements }, null, 2));
process.exit(passed === results.length ? 0 : 1);
