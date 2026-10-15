// Repeated React #418 hydration gate for ADG-NEXT16-POST-DEMO-INTEGRATION-01. Kept as evidence
// and not wired into any suite: the repository has no browser framework, and ADG-DEMO-02/03 and
// DEMO-PUBLIC-01 made the same choice. It drives the real application in the pre-installed
// Chromium: the API in demo mode over a reset Null Meridian database, and `next start` of a
// production build.
//
//   NODE_PATH=$(npm root -g) node hydration-gate.script.js <output directory>
//
// Environment:
//   ADG_WEB_URL    web origin to drive (default http://localhost:3000)
//   GATE_LABEL     label written into every record, e.g. "next-16.4.1 direct"
//   GATE_ROUNDS    rounds (default 5)
//   GATE_RELOADS   reloads of each route per round (default 3)
//   GATE_CONTROL   "1" runs the positive control: before React hydrates, one foreign element
//                  is put ahead of the app shell's first child (div.shell > a.skip-link, where
//                  ADG-UI-EDGE-01 caught #418), so every full load must report a hydration
//                  mismatch. It proves the detector sees #418 on the build under test.
//
// One round, signed in as the development auditor:
//   fresh   a new browser context (empty cache), then a full load of every route
//   reload  GATE_RELOADS reloads of every route
//   nav     client-side navigation: the guide's links, Back, the comparison drill and both
//           recovery actions, then every Primary navigation link
//   phone   390 px wide loads of the reviewer surfaces; records horizontal overflow
//   keys    keyboard only: Tab to and Enter on the hop evidence, the incomplete-scopes
//           disclosure and the comparison's Diagnostics
//
// Every load or transition is one record in <out>/loads.jsonl, written as it finishes. Nothing
// is retried or dropped. A record lists every console error and page error seen while it was
// current, classified as hydration (#418 and its family), the known /favicon.ico 404, or other.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const OUT = process.argv[2];
const BASE = process.env.ADG_WEB_URL || "http://localhost:3000";
const LABEL = process.env.GATE_LABEL || "unlabelled";
const ROUNDS = Number(process.env.GATE_ROUNDS || 5);
const RELOADS = Number(process.env.GATE_RELOADS || 3);
const CONTROL = process.env.GATE_CONTROL === "1";
const CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const enc = encodeURIComponent;
const FATIMA = "S-1-5-21-3949361974-1583827165-633756269-1323";
const UNRESOLVED = "S-1-5-21-3949361974-1583827165-633756269-1108";
const APOLLO = "\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo";
const FROM = enc("2026-09-03T06:00:00Z");
const TO = enc("2026-10-01T06:00:00Z");
const ROUTES = [
  ["home", "/"],
  ["demo guide", "/demo"],
  ["investigation: Fatima Gordon -> Apollo", `/access/investigate?principal=${FATIMA}&resource=${enc(APOLLO)}`],
  ["investigation: SYSTEM over \\\\NM-FS-01\\Shared", `/access/investigate?principal=S-1-5-18&resource=${enc("\\\\NM-FS-01\\Shared")}`],
  ["access explanation: Fatima Gordon -> Apollo", `/access/explain?principal=${FATIMA}&resource=${enc(APOLLO)}`],
  ["risks", "/risks"],
  ["collectors", "/collectors"],
  ["comparison drill: timed out", `/changes/compare?from=${FROM}&to=${TO}&time_budget_ms=1`],
  ["comparison: incomplete", `/changes/compare?from=${FROM}&to=${TO}`],
  ["comparison: folder permission entries", `/changes/compare?from=${FROM}&to=${TO}&kind=ntfs_ace`],
  ["identities", "/identities"],
  ["principal: Fatima Gordon", `/identities/principal?key=${FATIMA}`],
  ["principal: unresolved SID", `/identities/principal?key=${UNRESOLVED}`],
  ["status", "/status"],
  ["changes", "/changes"],
  ["search", "/search?q=Apollo"],
  ["resources", "/resources"],
];
const PHONE_ROUTES = ROUTES.filter(([name]) =>
  /^(demo guide|investigation: Fatima|risks|comparison|principal: unresolved)/.test(name),
);

const HYDRATION = /#418\b|react\.dev\/errors\/418|Hydration failed|hydrat/i;
const HYDRATION_FAMILY = /Minified React error #4(1[89]|2[0-9])\b|react\.dev\/errors\/4(1[89]|2[0-9])\b/;

fs.mkdirSync(OUT, { recursive: true });
const loadsFile = path.join(OUT, "loads.jsonl");
let current = null;
const records = [];

function begin(round, phase, route, url) {
  current = { label: LABEL, control: CONTROL, round, phase, route, url, started: new Date().toISOString(), ok: true, detail: "", hydration: [], family: [], known: [], other: [], failedResponses: [] };
  return current;
}
function finish(rec, extra = {}) {
  Object.assign(rec, extra);
  rec.n418 = rec.hydration.length;
  records.push(rec);
  fs.appendFileSync(loadsFile, JSON.stringify(rec) + "\n");
  current = null;
}
function classify(text, where) {
  if (!current) return;
  const line = `${text}${where ? " @ " + where : ""}`.slice(0, 700);
  if (HYDRATION.test(text)) current.hydration.push(line);
  else if (HYDRATION_FAMILY.test(text)) current.family.push(line);
  else if (/\/favicon\.ico$/.test(where || "")) current.known.push(line);
  else current.other.push(line);
}
function watch(page) {
  page.on("console", (m) => { if (m.type() === "error") classify(m.text(), m.location().url); });
  page.on("pageerror", (e) => classify(String(e && e.stack ? `${e.message}\n${e.stack}` : e), "pageerror"));
  page.on("response", (r) => { if (current && r.status() >= 400) current.failedResponses.push(`${r.status()} ${r.url()}`); });
}

// Records when React reports, relative to the document: readyState at the moment, as
// ADG-UI-EDGE-01 did. Diagnostic only; the count comes from the browser's console and page
// errors above, which is how the public-demo runs saw #418.
const INIT = `
  window.__adgHydration = [];
  (function () {
    const note = (kind, text) => { try { window.__adgHydration.push({ kind, readyState: document.readyState, at: Math.round(performance.now()), text: String(text).slice(0, 300) }); } catch (e) {} };
    const error = console.error;
    console.error = function (...args) { note("console.error", args.map((a) => (a && a.message) || a).join(" ")); return error.apply(this, args); };
    if (window.reportError) { const report = window.reportError; window.reportError = function (e) { note("reportError", (e && e.message) || e); return report.call(this, e); }; }
    if (${CONTROL ? "true" : "false"}) {
      const observer = new MutationObserver(() => {
        const shell = document.querySelector("div.shell");
        if (shell && shell.firstElementChild && !document.querySelector("[data-hydration-control]")) {
          const foreign = document.createElement("div");
          foreign.setAttribute("data-hydration-control", "");
          shell.insertBefore(foreign, shell.firstElementChild);
          observer.disconnect();
        }
      });
      observer.observe(document, { childList: true, subtree: true });
    }
  })();
`;

async function settle(page) {
  try { await page.waitForLoadState("load", { timeout: 30000 }); } catch (e) { /* recorded by the caller */ }
  try { await page.waitForLoadState("networkidle", { timeout: 8000 }); } catch (e) { /* streaming pages may keep a request open */ }
  await page.waitForTimeout(300);
}
async function reactNotes(page) {
  try { return await page.evaluate(() => window.__adgHydration || []); } catch (e) { return []; }
}

async function signedInContext(browser, viewport) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(INIT);
  const login = await context.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  if (!login.ok()) throw new Error(`dev-login failed: ${login.status()}`);
  return context;
}

async function load(page, round, phase, name, route, how) {
  const rec = begin(round, phase, name, `${BASE}${route}`);
  const t0 = Date.now();
  try {
    if (how === "reload") await page.reload({ waitUntil: "commit", timeout: 30000 });
    else await page.goto(`${BASE}${route}`, { waitUntil: "commit", timeout: 30000 });
    await settle(page);
    rec.title = await page.title();
    const h1 = await page.locator("h1").first().innerText({ timeout: 5000 }).catch(() => "");
    rec.h1 = h1.slice(0, 160);
  } catch (e) { rec.ok = false; rec.detail = String(e).slice(0, 400); }
  finish(rec, { ms: Date.now() - t0, react: await reactNotes(page) });
}

async function transition(page, round, name, act, urlPattern) {
  const rec = begin(round, "nav", name, "");
  const t0 = Date.now();
  try {
    await Promise.all([page.waitForURL(urlPattern, { timeout: 30000 }), act()]);
    await settle(page);
    rec.url = page.url();
    rec.h1 = (await page.locator("h1").first().innerText({ timeout: 5000 }).catch(() => "")).slice(0, 160);
  } catch (e) { rec.ok = false; rec.detail = String(e).slice(0, 400); rec.url = page.url(); }
  finish(rec, { ms: Date.now() - t0, react: await reactNotes(page) });
}

async function tabTo(page, predicate, limit = 150) {
  await page.locator("body").focus();
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(predicate)) return i + 1;
  }
  return null;
}

async function keyboard(page, round, name, route, predicate) {
  const rec = begin(round, "keys", name, `${BASE}${route}`);
  const t0 = Date.now();
  try {
    await page.goto(`${BASE}${route}`, { waitUntil: "commit", timeout: 30000 });
    await settle(page);
    const tabs = await tabTo(page, predicate);
    if (tabs === null) throw new Error("Tab did not reach the control");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    const opened = await page.evaluate(() => document.activeElement?.parentElement?.hasAttribute("open") === true);
    if (!opened) throw new Error("Enter did not open the disclosure");
    rec.detail = `reached in ${tabs} Tab presses, opened by Enter`;
  } catch (e) { rec.ok = false; rec.detail = String(e).slice(0, 400); }
  finish(rec, { ms: Date.now() - t0, react: await reactNotes(page) });
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });
  const started = new Date().toISOString();
  for (let round = 1; round <= ROUNDS; round++) {
    // fresh: a new context, so the first load of the round starts from an empty cache.
    const context = await signedInContext(browser, { width: 1280, height: 900 });
    const page = await context.newPage();
    watch(page);
    for (const [name, route] of ROUTES) await load(page, round, "fresh", name, route, "goto");

    // reload: the reload loop of ADG-UI-EDGE-01 and ADG-NEXT16-REACT418-01, per route.
    for (const [name, route] of ROUTES) {
      await load(page, round, "reload", name, route, "goto");
      for (let i = 0; i < RELOADS; i++) await load(page, round, "reload", name, route, "reload");
    }

    // nav: client-side transitions through the reviewer's path.
    await load(page, round, "nav", "demo guide (start)", "/demo", "goto");
    const guide = page.getByRole("region", { name: "Start here: a guided access investigation" });
    await transition(page, round, "guide -> investigation", () => guide.getByRole("link", { name: "Investigate access" }).click(), /\/access\/investigate\?/);
    await transition(page, round, "Back -> guide", () => page.goBack(), /\/demo$/);
    const coverage = page.getByRole("region", { name: "Then: how complete is the evidence, and what if a comparison cannot finish?" });
    await transition(page, round, "guide -> risk totals", () => coverage.getByRole("link", { name: /View the risk totals/ }).click(), /\/risks/);
    await transition(page, round, "Back -> guide", () => page.goBack(), /\/demo$/);
    await transition(page, round, "Forward -> risk totals", () => page.goForward(), /\/risks/);
    await transition(page, round, "Back -> guide", () => page.goBack(), /\/demo$/);
    await transition(page, round, "guide -> comparison drill (timed out)", () => page.getByRole("link", { name: "Run the comparison drill" }).click(), /time_budget_ms=1/);
    const actions = () => page.getByRole("region", { name: "How this comparison ended" }).getByRole("navigation", { name: "Recovery actions" });
    await transition(page, round, "drill -> retry (incomplete)", () => actions().getByRole("link", { name: /Retry inside the standard 4-second window/ }).click(), /retry_of=/);
    await transition(page, round, "retry -> folder permission entries (recovered)", () => actions().getByRole("link", { name: /Compare only folder permission entries/ }).click(), /kind=ntfs_ace/);
    const primary = page.getByRole("navigation", { name: "Primary" }).getByRole("link");
    const count = await primary.count();
    for (let i = 0; i < count; i++) {
      const link = primary.nth(i);
      const href = await link.getAttribute("href");
      if (!href || href.startsWith("http")) continue;
      const pathOnly = href.split("?")[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      await transition(page, round, `Primary nav -> ${href}`, () => link.click(), new RegExp(`${pathOnly}(\\?|$|#)`));
    }

    // keys: keyboard-only interaction on the three reviewer surfaces that have disclosures.
    await keyboard(page, round, "investigation: hop evidence", ROUTES[2][1], () => document.activeElement?.tagName === "SUMMARY" && /Evidence for hop 1/.test(document.activeElement.textContent ?? ""));
    await keyboard(page, round, "risks: incomplete scopes", "/risks", () => document.activeElement?.tagName === "SUMMARY" && /Which scopes are not complete/.test(document.activeElement.textContent ?? ""));
    await keyboard(page, round, "comparison: Diagnostics", ROUTES[7][1], () => document.activeElement?.tagName === "SUMMARY" && /Diagnostics/.test(document.activeElement.textContent ?? ""));
    await context.close();

    // phone: 390 px.
    const phoneContext = await signedInContext(browser, { width: 390, height: 844 });
    const phone = await phoneContext.newPage();
    watch(phone);
    for (const [name, route] of PHONE_ROUTES) {
      await load(phone, round, "phone", name, route, "goto");
      const last = records[records.length - 1];
      const overflow = await phone.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth })).catch(() => null);
      last.overflow = overflow;
      fs.appendFileSync(loadsFile, JSON.stringify({ overflowFor: last.route, round, overflow }) + "\n");
    }
    await phoneContext.close();
    const r = records.filter((x) => x.round === round);
    console.log(`[${LABEL}] round ${round}/${ROUNDS}: ${r.length} loads/transitions, #418 in ${r.filter((x) => x.n418 > 0).length}, other errors in ${r.filter((x) => x.other.length > 0).length}, failed steps ${r.filter((x) => !x.ok).length}`);
  }
  await browser.close();

  const by = (phase) => records.filter((r) => !phase || r.phase === phase);
  const sum = (rs) => ({
    records: rs.length,
    with418: rs.filter((r) => r.n418 > 0).length,
    events418: rs.reduce((n, r) => n + r.n418, 0),
    withHydrationFamily: rs.filter((r) => r.family.length > 0).length,
    withOtherErrors: rs.filter((r) => r.other.length > 0).length,
    failedSteps: rs.filter((r) => !r.ok).length,
  });
  const summary = {
    label: LABEL, control: CONTROL, base: BASE, rounds: ROUNDS, reloadsPerRoute: RELOADS, started, finished: new Date().toISOString(),
    total: sum(by()),
    phases: Object.fromEntries(["fresh", "reload", "nav", "keys", "phone"].map((p) => [p, sum(by(p))])),
    fullDocumentLoads: records.filter((r) => r.phase !== "nav" || /\(start\)/.test(r.route)).length,
    clientTransitions: records.filter((r) => r.phase === "nav" && !/\(start\)/.test(r.route)).length,
    phoneOverflow: records.filter((r) => r.phase === "phone" && r.overflow && r.overflow.scroll > r.overflow.client).map((r) => `${r.round}: ${r.route} ${JSON.stringify(r.overflow)}`),
    occurrences418: records.filter((r) => r.n418 > 0).map((r) => ({ round: r.round, phase: r.phase, route: r.route, url: r.url, first: r.hydration[0], react: r.react })),
    otherErrors: records.filter((r) => r.other.length > 0).map((r) => ({ round: r.round, phase: r.phase, route: r.route, errors: r.other })),
    failedSteps: records.filter((r) => !r.ok).map((r) => ({ round: r.round, phase: r.phase, route: r.route, detail: r.detail })),
    knownErrors: [...new Set(records.flatMap((r) => r.known))],
  };
  fs.writeFileSync(path.join(OUT, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify({ label: LABEL, control: CONTROL, total: summary.total, fullDocumentLoads: summary.fullDocumentLoads, clientTransitions: summary.clientTransitions }));
})().catch((e) => { console.error(e); process.exit(1); });
