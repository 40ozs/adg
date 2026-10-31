#!/usr/bin/env node
/**
 * ADG-UX-NAV-07: campaign → review item is a client-side move.
 *
 * One signed-in tab per viewport (1440 and 375 px), on the real stack:
 *
 * 1. Reviews, then a click on the first listed campaign that has items: "Review campaign — ADG",
 *    no document load;
 * 2. a side tab direct-loads the campaign (one server <title>) and the item about to be opened,
 *    recording the item page's web-tier API reads;
 * 3. a click on the item in the campaign's item table: no document request, the window survives
 *    (a sentinel on it), the URL is the link's href, "Review item — ADG", the move's API reads are
 *    within the direct load's (no duplicate read), and no item link was prefetched;
 * 4. after the move: focus is not on a detached node, the heading is in view, no sideways scroll;
 * 5. Back: the campaign URL and title, its item table unchanged (same rows, no duplicate, none
 *    selected); Forward: the item URL and title; Back again, then the item link by keyboard;
 * 6. no document load anywhere in the journey, no page error, no hydration error.
 *
 * Same topology as the Wave C gate: Next.js ADG_INTERNAL_API_URL → this script's recording
 * proxy on $PROXY_PORT → the API. Read-only apart from development sign-ins.
 *
 *   WEB_URL=http://127.0.0.1:3070 API_URL=http://127.0.0.1:8070 PROXY_PORT=8170 \
 *   CHROMIUM_PATH=… [SIGN_IN_AS=admin] [REPORT_JSON=…] node review-item-nav.mjs
 */
import { writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "admin";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}

/* Recording proxy: every API call the web tier makes (method, path, sorted query keys). */
const requests = [];
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const keys = [...new Set(url.searchParams.keys())].sort().join(",");
  const entry = { call: `${req.method} ${url.pathname}${keys ? `?${keys}` : ""}`, end: null };
  requests.push(entry);
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
const measurements = {};
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
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** Wait until the web tier has made no new API call, and none is open, for `ms`. */
async function quiet(ms = 600) {
  let seen;
  do {
    seen = requests.length;
    await sleep(ms);
  } while (requests.length !== seen || requests.some((r) => r.end === null));
}
function counts(list) {
  const map = new Map();
  for (const call of list) map.set(call, (map.get(call) ?? 0) + 1);
  return map;
}
/** Calls in `move` beyond what `direct` made (by multiset). */
function extra(move, direct) {
  const have = counts(direct);
  const out = [];
  for (const [call, n] of counts(move)) if (n > (have.get(call) ?? 0)) out.push(`${call} ×${n}`);
  return out;
}

const SUFFIX = " — ADG";
const ITEM_LINK = '#main table a[href^="/governance/items/"]';
const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "375", width: 375, height: 812 },
];

const login = await fetch(`${API}/auth/dev/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: SIGN_IN_AS }) });
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
async function hasItems(campaignPath) {
  const id = campaignPath.split("/").pop();
  const response = await fetch(`${API}/api/v1/governance/campaigns/${id}/items?limit=1`, { headers: { Authorization: `Bearer ${token}` } });
  return response.ok && (await response.json()).items.length > 0;
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});

for (const viewport of VIEWPORTS) {
  const tag = `[${viewport.name}]`;
  const m = (measurements[viewport.name] = {});
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
  context.setDefaultTimeout(60_000);
  const signIn = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username: SIGN_IN_AS } });
  expect(signIn.ok(), `sign-in as ${SIGN_IN_AS}: HTTP ${signIn.status()}`);
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const documents = [];
  const prefetches = [];
  page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${page.url()}: ${message.text()}`);
  });
  page.on("request", (request) => {
    if (request.frame() === page.mainFrame() && request.resourceType() === "document") documents.push(request.url());
    const url = new URL(request.url());
    const headers = Object.keys(request.headers()).join(" ");
    if (url.pathname.startsWith("/governance/items/") && /prefetch/i.test(headers)) prefetches.push(request.url());
  });
  async function titled(want) {
    await page.waitForFunction((t) => document.title === t, want, { timeout: 15_000 }).catch(() => {});
    return page.title();
  }
  const sentinel = () => page.evaluate(() => window.__nav07 === true);
  const table = () =>
    page.evaluate((selector) => {
      const links = [...document.querySelectorAll(selector)].map((a) => a.getAttribute("href"));
      const rows = document.querySelectorAll("#main table tbody tr").length;
      const checked = document.querySelectorAll('#main table input[type="checkbox"]:checked').length;
      return { rows, links: links.length, unique: new Set(links).size, checked };
    }, ITEM_LINK);
  const sideways = () =>
    page.evaluate(() => {
      const root = document.documentElement;
      return root.scrollWidth > root.clientWidth ? `${root.scrollWidth} > ${root.clientWidth}` : null;
    });

  let campaignUrl = "";
  let itemHref = "";
  let tableBefore = null;

  await check(`${tag} Reviews → campaign by click: "Review campaign — ADG", no document load`, async () => {
    await page.goto(`${WEB}/governance`, { waitUntil: "networkidle" });
    expect((await titled(`Reviews${SUFFIX}`)) === `Reviews${SUFFIX}`, `Reviews title "${await page.title()}"`);
    await page.evaluate(() => (window.__nav07 = true));
    documents.length = 0;
    const hrefs = await page.locator('#main a[href^="/governance/campaigns/"]:not([href*="?"])').evaluateAll((as) => [...new Set(as.map((a) => a.getAttribute("href")))]);
    let chosen = null;
    for (const href of hrefs) if (chosen === null && (await hasItems(href))) chosen = href;
    expect(chosen !== null, `none of ${hrefs.length} campaigns on Reviews has items`);
    await page.locator(`#main a[href="${chosen}"]`).first().click();
    await page.waitForURL((url) => url.pathname.startsWith("/governance/campaigns/"));
    const title = await titled(`Review campaign${SUFFIX}`);
    expect(title === `Review campaign${SUFFIX}`, `title "${title}"`);
    await page.locator(ITEM_LINK).first().waitFor();
    await quiet();
    campaignUrl = page.url();
    itemHref = await page.locator(ITEM_LINK).first().getAttribute("href");
    tableBefore = await table();
    expect(documents.length === 0 && (await sentinel()), `document load: ${documents.join(", ")}`);
    return `${new URL(campaignUrl).pathname}; ${tableBefore.rows} rows, ${tableBefore.links} item links`;
  });

  if (!itemHref) {
    await context.close();
    continue;
  }

  await check(`${tag} direct loads: one server <title> each; the item page's API reads recorded`, async () => {
    expect(itemHref, "no item link in the campaign's item table");
    const side = await context.newPage();
    const seen = [];
    for (const [path, label] of [
      [new URL(campaignUrl).pathname, "Review campaign"],
      [itemHref, "Review item"],
    ]) {
      const html = await (await side.request.get(`${WEB}${path}`)).text();
      const titles = [...html.matchAll(/<title>([^<]*)<\/title>/g)].map((x) => x[1]);
      expect(titles[0] === `${label}${SUFFIX}`, `${path}: server <title> ${JSON.stringify(titles.slice(0, 2))}`);
      seen.push(`${label}: "${titles[0]}"`);
    }
    await quiet();
    const from = requests.length;
    await side.goto(`${WEB}${itemHref}`, { waitUntil: "networkidle" });
    expect((await side.title()) === `Review item${SUFFIX}`, `item direct load title "${await side.title()}"`);
    await quiet();
    m.directItemReads = requests.slice(from).map((r) => r.call);
    await side.close();
    return `${seen.join(", ")}; item direct load = ${m.directItemReads.length} API reads`;
  });

  await check(`${tag} campaign → item by click: client-side, "Review item — ADG", href kept, no extra API read, no prefetch`, async () => {
    await quiet();
    documents.length = 0;
    const from = requests.length;
    m.prefetchesBeforeClick = prefetches.length;
    await page.locator(ITEM_LINK).first().click();
    await page.waitForURL((url) => url.pathname === itemHref);
    const title = await titled(`Review item${SUFFIX}`);
    await page.locator("#main h1").first().waitFor();
    await quiet();
    m.moveReads = requests.slice(from).map((r) => r.call);
    m.moveDocuments = documents.length;
    m.moveExtra = extra(m.moveReads, m.directItemReads);
    m.moveDuplicates = [...counts(m.moveReads)].filter(([, n]) => n > 1).map(([c, n]) => `${c} ×${n}`);
    expect(documents.length === 0, `full document reload: ${documents.join(", ")}`);
    expect(await sentinel(), "the window was replaced (document reload)");
    expect(title === `Review item${SUFFIX}`, `title "${title}"`);
    expect(new URL(page.url()).pathname === itemHref && new URL(page.url()).search === "", `URL ${page.url()}, link href ${itemHref}`);
    expect(m.moveExtra.length === 0, `reads beyond a direct load: ${m.moveExtra.join("; ")}`);
    expect(prefetches.length === 0, `item route prefetched: ${prefetches.join(" ")}`);
    return `0 document loads; ${m.moveReads.length} API reads (direct load ${m.directItemReads.length}), 0 extra, ${m.moveDuplicates.length} repeated; 0 item prefetches`;
  });

  await check(`${tag} after the move: focus on a live node, heading in view, no sideways scroll`, async () => {
    const focus = await page.evaluate(() => {
      const el = document.activeElement;
      return { tag: el?.tagName ?? null, live: !!el?.isConnected };
    });
    const top = await page.locator("#main h1").first().evaluate((h) => h.getBoundingClientRect().top);
    const wide = await sideways();
    m.focusAfterMove = focus.tag;
    m.headingTop = Math.round(top);
    expect(focus.live, `focus on a detached ${focus.tag}`);
    expect(top >= 0 && top < viewport.height, `heading at ${Math.round(top)} px`);
    expect(wide === null, `item page scrolls sideways (${wide})`);
    return `focus ${focus.tag}, heading at ${Math.round(top)} px, scrollWidth fits`;
  });

  await check(`${tag} Back: campaign URL and title, item table unchanged, no sideways scroll`, async () => {
    documents.length = 0;
    await page.goBack();
    await page.waitForURL((url) => url.href === campaignUrl);
    const title = await titled(`Review campaign${SUFFIX}`);
    await page.locator(ITEM_LINK).first().waitFor();
    const after = await table();
    const wide = await sideways();
    expect(title === `Review campaign${SUFFIX}`, `title "${title}"`);
    expect(after.rows === tableBefore.rows && after.links === tableBefore.links, `table ${JSON.stringify(after)} vs ${JSON.stringify(tableBefore)}`);
    expect(after.unique === after.links, `duplicate item rows: ${after.links} links, ${after.unique} distinct`);
    expect(after.checked === tableBefore.checked, `${after.checked} items selected, was ${tableBefore.checked}`);
    expect(wide === null, `campaign page scrolls sideways (${wide})`);
    expect(documents.length === 0 && (await sentinel()), `document load on Back: ${documents.join(", ")}`);
    return `${after.rows} rows, ${after.unique} distinct items, ${after.checked} selected`;
  });

  await check(`${tag} Forward: item URL and title, no document load`, async () => {
    documents.length = 0;
    await page.goForward();
    await page.waitForURL((url) => url.pathname === itemHref);
    const title = await titled(`Review item${SUFFIX}`);
    expect(title === `Review item${SUFFIX}`, `title "${title}"`);
    expect(documents.length === 0 && (await sentinel()), `document load on Forward: ${documents.join(", ")}`);
  });

  await check(`${tag} Back, then the item link by keyboard (focus + Enter): client-side`, async () => {
    documents.length = 0;
    await page.goBack();
    await page.waitForURL((url) => url.href === campaignUrl);
    await titled(`Review campaign${SUFFIX}`);
    const link = page.locator(ITEM_LINK).first();
    await link.focus();
    await page.keyboard.press("Enter");
    await page.waitForURL((url) => url.pathname === itemHref);
    const title = await titled(`Review item${SUFFIX}`);
    expect(title === `Review item${SUFFIX}`, `title "${title}"`);
    expect(documents.length === 0 && (await sentinel()), `document load: ${documents.join(", ")}`);
  });

  await check(`${tag} no page error, no hydration error`, async () => {
    const hydration = [...pageErrors, ...consoleErrors].filter((text) => /hydrat|#418|#423|#425/i.test(text));
    m.pageErrors = pageErrors;
    m.consoleErrors = consoleErrors;
    expect(pageErrors.length === 0, pageErrors[0] ?? "");
    expect(hydration.length === 0, hydration[0] ?? "");
    return `0 page errors, ${consoleErrors.length} console errors (0 hydration)`;
  });

  await context.close();
}

await browser.close();
proxy.close();
const failed = results.filter((result) => !result.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (REPORT) writeFileSync(REPORT, `${JSON.stringify({ results, measurements }, null, 2)}\n`);
process.exit(failed === 0 ? 0 : 1);
