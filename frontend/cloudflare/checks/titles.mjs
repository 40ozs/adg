#!/usr/bin/env node
/**
 * Browser gate for document titles (ADG-UX-TITLES-03): the server-rendered <title> on a
 * direct load, the title after each client-side move through the primary navigation, and
 * the title Back and Forward restore, each checked against the nav item marked current.
 *
 *   STAGING_URL=… SIGN_IN_AS=admin node cloudflare/checks/titles.mjs
 *
 * Runs against the hosted demo or a local `next start`; needs `playwright` as browser.mjs
 * does. Titles depend on the route alone, and the check writes nothing. The deeper-page
 * journey (ADG-UX-DEEP-TITLES-06) clicks real links, so it needs a dataset that offers them.
 * SIGN_IN_AS should hold every section's capability (`admin` locally); a narrower account
 * checks only the sections its nav offers, and says how many.
 */

import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const BASE = (process.env.STAGING_URL ?? "").replace(/\/+$/, "");
if (!BASE) {
  console.error("Set STAGING_URL.");
  process.exit(2);
}
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "admin";
const TIMEOUT = Number(process.env.CHECK_TIMEOUT_MS ?? 90_000);
const SUFFIX = " — ADG";
// A syntactically valid SID nobody has; the title must not carry it either way.
const DETAIL_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001";

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(TIMEOUT);
const page = await context.newPage();

const pageErrors = [];
const consoleErrors = [];
const documents = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("request", (request) => {
  if (request.frame() === page.mainFrame() && request.resourceType() === "document") {
    documents.push(request.url());
  }
});

const results = [];
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

const nav = () => page.getByRole("navigation", { name: "Primary" });
const current = async () => (await nav().locator('[aria-current="page"]').allInnerTexts()).join("|");
/** The title once it settles on `want`, or whatever it still is when the wait gives up. */
async function titleBecomes(want) {
  await page.waitForFunction((t) => document.title === t, want, { timeout: 10_000 }).catch(() => {});
  return page.title();
}
/** The <title> the server sent, before any script ran. */
async function serverTitle(path) {
  const response = await page.request.get(`${BASE}${path}`);
  const html = await response.text();
  const titles = [...html.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => m[1].replaceAll("&amp;", "&"));
  return { status: response.status(), titles };
}
async function expectCoherent(label) {
  const title = await titleBecomes(`${label}${SUFFIX}`);
  expect(title === `${label}${SUFFIX}`, `title "${title}", wanted "${label}${SUFFIX}"`);
  const marked = await current();
  expect(marked === label, `nav marks "${marked}", wanted "${label}"`);
}

await check(`sign in as ${SIGN_IN_AS}`, async () => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  const title = await page.title();
  expect(title === `Sign in${SUFFIX}`, `login title "${title}"`);
  await page.getByRole("button", { name: new RegExp(`Sign in as ${SIGN_IN_AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  return `login page titled "${title}"`;
});

await check("direct load: the server sends the section title (/risks → Findings)", async () => {
  const { status, titles } = await serverTitle("/risks");
  expect(status === 200, `HTTP ${status}`);
  expect(titles.length === 1 && titles[0] === `Findings${SUFFIX}`, `server <title> ${JSON.stringify(titles)}`);
  await page.goto(`${BASE}/risks`, { waitUntil: "networkidle" });
  await expectCoherent("Findings");
  return `server <title>${titles[0]}</title>, nav marks Findings`;
});

const visited = [];
await check("client navigation: every primary nav link updates the title", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await expectCoherent("Overview");
  visited.push("Overview");
  await page.evaluate(() => {
    window.__adgNoReload = true;
  });
  documents.length = 0;
  const labels = await nav().getByRole("link").allInnerTexts();
  for (const label of labels.slice(1)) {
    const before = page.url();
    await nav().getByRole("link", { name: label, exact: true }).click();
    await page.waitForURL((url) => url.href !== before);
    await expectCoherent(label);
    visited.push(label);
  }
  const kept = await page.evaluate(() => window.__adgNoReload === true);
  expect(kept && documents.length === 0, `a move reloaded the document (${documents.join(", ")})`);
  return `${visited.length} sections, no reload: ${visited.join(", ")}`;
});

await check("Back restores each earlier title", async () => {
  const seen = [];
  for (let i = visited.length - 2; i >= Math.max(0, visited.length - 5); i--) {
    await page.goBack();
    await expectCoherent(visited[i]);
    seen.push(visited[i]);
  }
  return seen.join(" ← ");
});

await check("Forward restores each later title", async () => {
  const seen = [];
  const from = Math.max(0, visited.length - 5);
  for (let i = from + 1; i <= from + 2; i++) {
    await page.goForward();
    await expectCoherent(visited[i]);
    seen.push(visited[i]);
  }
  return seen.join(" → ");
});

await check("hard reload keeps the title", async () => {
  await page.goto(`${BASE}/changes`, { waitUntil: "networkidle" });
  await page.reload({ waitUntil: "networkidle" });
  await expectCoherent("Changes");
});

await check("a filtered list keeps its section title", async () => {
  await page.goto(`${BASE}/risks?severity=critical`, { waitUntil: "networkidle" });
  await expectCoherent("Findings");
});

await check("detail route: a generic title, no SID", async () => {
  const path = `/identities/principal/${encodeURIComponent(DETAIL_SID)}`;
  const { titles } = await serverTitle(path);
  expect(titles.length === 1 && titles[0] === `Identity${SUFFIX}`, `server <title> ${JSON.stringify(titles)}`);
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  const title = await titleBecomes(`Identity${SUFFIX}`);
  expect(title === `Identity${SUFFIX}`, `title "${title}"`);
  expect(!/S-1-\d/.test(title), `SID in title "${title}"`);
  const marked = await current();
  expect(marked === "Identities", `nav marks "${marked}"`);
  return `"${title}", nav marks Identities`;
});

await check("an unknown page: a stable not-found title", async () => {
  const { status, titles } = await serverTitle("/no-such-page");
  expect(status === 404, `HTTP ${status}`);
  await page.goto(`${BASE}/no-such-page`, { waitUntil: "networkidle" });
  const title = await page.title();
  expect(title !== "" && !/undefined|loading/i.test(title), `title "${title}"`);
  return `HTTP 404, server ${JSON.stringify(titles)}, document "${title}"`;
});

/* ---------------------------------------------- deeper pages (ADG-UX-DEEP-TITLES-06) */

// A title must never carry an identifier or a path: SID, GUID, UNC/drive path, raw token.
const LEAKS = /S-1-\d|[0-9a-f]{8}-[0-9a-f]{4}-|\\|[A-Z]:\/|principal:|\/\//i;

/**
 * One journey through the deeper pages, every move a click on a link the page offers (or a
 * nav item), so each is a client-side navigation. `link` is a selector inside the main
 * content; `nav` is a primary nav label. A missing link fails the step: the journey needs a
 * dataset with changes in the last 90 days, a review campaign with an item, and one What-if
 * proposal. `fullLoad` marks a move whose link is a plain <a> before this lane: the campaign's
 * item list (components/DecisionForm.tsx) loads the item as a new document. Its title is still
 * checked; the document loads it causes, there and on Back/Forward across it, are not reloads.
 */
const JOURNEY = [
  { nav: "Findings", title: "Findings", current: "Findings" },
  { link: 'a[href="/risks/alerts"]', title: "Alerts", current: "Findings" },
  { nav: "Changes", title: "Changes", current: "Changes" },
  { link: 'a[href="/changes?days=90"]', title: "Changes", current: "Changes" },
  { link: 'a[href^="/changes?kind="]', title: "History", current: "Changes" },
  { link: 'a[href="/changes"]', title: "Changes", current: "Changes" },
  { link: 'a[href="/changes?days=90"]', title: "Changes", current: "Changes" },
  { link: 'a[href^="/changes/impact?"]', title: "Why access changed", current: "Changes" },
  { nav: "Changes", title: "Changes", current: "Changes" },
  { link: 'a[href="/changes/compare"]', title: "Compare two points in time", current: "Changes" },
  { nav: "Identities", title: "Identities", current: "Identities" },
  { link: 'a[href^="/identities/users"]', title: "Users", current: "Identities" },
  { link: 'a[href^="/identities/groups"]', title: "Groups", current: "Identities" },
  { link: 'a[href^="/identities/principal/"]', title: "Identity", current: "Identities" },
  { nav: "Reviews", title: "Reviews", current: "Reviews" },
  { link: 'a[href^="/governance/campaigns/"]', title: "Review campaign", current: "Reviews" },
  { link: 'a[href^="/governance/items/"]', title: "Review item", current: "Reviews", fullLoad: true },
  { nav: "What-if", title: "What-if", current: "What-if" },
  { link: 'a[href^="/simulations/"]:not([href^="/simulations/new"])', title: "What-if proposal", current: "What-if" },
  { nav: "What-if", title: "What-if", current: "What-if" },
  { link: 'a[href^="/simulations/new"]', title: "Propose a change", current: "What-if" },
];
const deepVisited = [];
const deepPaths = new Map();
// Pages either side of a `fullLoad` move: entering or leaving one by history loads a document.
const fullLoadPages = new Set();
const reloads = () => documents.filter((href) => !fullLoadPages.has(new URL(href).pathname));

await check("deeper pages: each client move titles the page it lands on", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    window.__adgNoReload = true;
  });
  documents.length = 0;
  for (const step of JOURNEY) {
    const before = page.url();
    if (step.fullLoad) fullLoadPages.add(new URL(before).pathname);
    const target = step.nav
      ? nav().getByRole("link", { name: step.nav, exact: true })
      : page.locator(`#main ${step.link}`).first();
    expect((await target.count()) > 0, `no link for "${step.title}" (${step.link ?? step.nav}) on ${before}`);
    await target.click();
    await page.waitForURL((url) => url.href !== before);
    const want = `${step.title}${SUFFIX}`;
    const title = await titleBecomes(want);
    expect(title === want, `${page.url()}: title "${title}", wanted "${want}"`);
    expect(!LEAKS.test(title), `identifier in title "${title}"`);
    const marked = await current();
    expect(marked === step.current, `${page.url()}: nav marks "${marked}", wanted "${step.current}"`);
    const url = new URL(page.url());
    if (step.fullLoad) fullLoadPages.add(url.pathname);
    deepVisited.push(step.title);
    deepPaths.set(step.title, `${url.pathname}${url.search}`);
  }
  expect(reloads().length === 0, `a move reloaded the document (${reloads().join(", ")})`);
  return `${deepVisited.length} moves, no reload (known full load: ${[...fullLoadPages].pop()})`;
});

await check("deeper pages: Back restores every earlier title", async () => {
  for (let i = deepVisited.length - 2; i >= 0; i--) {
    await page.goBack();
    const want = `${deepVisited[i]}${SUFFIX}`;
    const title = await titleBecomes(want);
    expect(title === want, `Back to ${page.url()}: title "${title}", wanted "${want}"`);
  }
  await page.goBack();
  const title = await titleBecomes(`Overview${SUFFIX}`);
  expect(title === `Overview${SUFFIX}`, `Back to the start: "${title}"`);
  return `${deepVisited.length} steps back to Overview`;
});

await check("deeper pages: Forward restores every later title", async () => {
  for (const label of deepVisited) {
    await page.goForward();
    const want = `${label}${SUFFIX}`;
    const title = await titleBecomes(want);
    expect(title === want, `Forward to ${page.url()}: title "${title}", wanted "${want}"`);
  }
  expect(reloads().length === 0, `Back/Forward reloaded the document (${reloads().join(", ")})`);
  return `${deepVisited.length} steps forward, no reload outside the known full load`;
});

await check("deeper pages: the server sends one matching <title> on a direct load", async () => {
  const direct = new Map(deepPaths);
  const sid = new URLSearchParams(direct.get("History")?.split("?")[1] ?? "").get("key") ?? DETAIL_SID;
  direct.set("Access investigation", `/access/investigate?principal=${encodeURIComponent(sid)}`);
  direct.set("Access explanation", `/access/explain?principal=${encodeURIComponent(sid)}`);
  const seen = [];
  for (const [label, path] of direct) {
    const { status, titles } = await serverTitle(path);
    expect(status === 200, `${path}: HTTP ${status}`);
    expect(titles.length === 1 && titles[0] === `${label}${SUFFIX}`, `${path}: server <title> ${JSON.stringify(titles)}`);
    seen.push(label);
  }
  await page.goto(`${BASE}${direct.get("History")}`, { waitUntil: "networkidle" });
  await page.reload({ waitUntil: "networkidle" });
  const title = await titleBecomes(`History${SUFFIX}`);
  expect(title === `History${SUFFIX}`, `History after a hard reload: "${title}"`);
  return `${seen.length} pages: ${seen.join(", ")}; History survives a hard reload`;
});

await check("deeper pages: an unknown or invalid object keeps a coherent title", async () => {
  const cases = [
    ["/governance/campaigns/00000000-0000-4000-8000-000000000000", "Review campaign"],
    ["/governance/items/00000000-0000-4000-8000-000000000000", "Review item"],
    ["/simulations/00000000-0000-4000-8000-000000000000", "What-if proposal"],
    [`/identities/principal/${encodeURIComponent(DETAIL_SID)}/no-such-view`, "Identity"],
  ];
  const seen = [];
  for (const [path, label] of cases) {
    const { status, titles } = await serverTitle(path);
    expect(titles.length === 1 && titles[0] === `${label}${SUFFIX}`, `${path}: server <title> ${JSON.stringify(titles)}`);
    seen.push(`${label} (HTTP ${status})`);
  }
  return seen.join(", ");
});

await check("no hydration error on any page", async () => {
  const hydration = [...pageErrors, ...consoleErrors].filter((text) => /hydrat|#418|#423|#425/i.test(text));
  expect(hydration.length === 0, hydration[0] ?? "");
  return `${pageErrors.length} page errors, ${consoleErrors.length} console errors, none about hydration`;
});

await browser.close();
const failed = results.filter((result) => !result.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed === 0 ? 0 : 1);
