// React #418 gate for ADG-NEXT16-POST-DEMO-INTEGRATION-01, version 2 (v1 plus phase D, a reload
// loop, and the document.readyState recorded with each hook report). One-off evidence script, not wired
// into any suite (the repository has no browser framework; ADG-DEMO-02/03 made the same choice).
//
// One run = one fresh browser context, signed in as the development auditor, then:
//   A. cold full-page loads of every reviewer route at 1280 px (the principal page resolved,
//      unresolved and unrecorded; the Apollo investigation and its explanation; SYSTEM over a
//      share; risks; collectors; the comparison drill; status; ...);
//   B. the reviewer path by client-side navigation from /demo (investigation -> unresolved SID
//      -> back -> removal analysis; risk totals; the drill timed_out -> incomplete -> recovered),
//      checking that each step stayed in the same document;
//   C. cold loads of the main reviewer pages at 390 px;
//   D. a reload loop: six reviewer pages, each loaded once and reloaded three times.
//
// Every hydration error is caught three ways: Playwright's console and pageerror events, and
// an init script (installed before any page script) that wraps window.reportError and
// console.error, which is where Next.js's onRecoverableError sends a recoverable #418.
//
//   NODE_PATH=$(npm root -g) node react418-gate.script.js <base url> <label> <runs> <out.jsonl>
//
// Appends one JSON line per run. Exits 0 even when #418 is seen: the result is the record.
const { chromium } = require("playwright");
const fs = require("fs");

const [BASE, LABEL, RUNS, OUT] = process.argv.slice(2);
const FATIMA = "S-1-5-21-3949361974-1583827165-633756269-1323";
const UNRESOLVED = "S-1-5-21-3949361974-1583827165-633756269-1108"; // recorded, unresolved (deleted)
const UNRECORDED = "S-1-5-21-3949361974-1583827165-633756269-99999"; // no run has described it
const APOLLO = "\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo";
const q = (o) => new URLSearchParams(o).toString();
const COMPARE = q({ from: "2026-09-03T06:00:00Z", to: "2026-10-01T06:00:00Z" });

const COLD = [
  "/",
  "/demo",
  "/identities",
  `/identities/principal?${q({ key: FATIMA })}`,
  `/identities/principal?${q({ key: UNRESOLVED })}`,
  `/identities/principal?${q({ key: UNRECORDED })}`,
  `/access/investigate?${q({ principal: FATIMA, resource: APOLLO })}`,
  `/access/explain?${q({ principal: FATIMA, resource: APOLLO, access_path: "remote" })}`,
  `/access/investigate?${q({ principal: "S-1-5-18", resource: "\\\\NM-FS-01\\Shared" })}`,
  "/risks",
  "/collectors",
  `/changes/compare?${COMPARE}&time_budget_ms=1`,
  "/status",
  "/resources",
  "/search?q=Apollo",
];
const NARROW = [
  "/demo",
  "/risks",
  `/access/investigate?${q({ principal: FATIMA, resource: APOLLO })}`,
  `/identities/principal?${q({ key: UNRESOLVED })}`,
  `/changes/compare?${COMPARE}&time_budget_ms=1`,
];

const RELOAD = [
  "/",
  "/demo",
  "/identities",
  `/access/investigate?${q({ principal: FATIMA, resource: APOLLO })}`,
  "/risks",
  `/changes/compare?${COMPARE}&time_budget_ms=1`,
];

const HOOK = `(() => {
  window.__adgErrors = [];
  const text = (x) => (x && (x.message || x.stack)) ? String(x.message || x.stack) : String(x);
  const re = window.reportError;
  window.reportError = function (e) { try { window.__adgErrors.push("reportError[" + document.readyState + "]: " + text(e)); } catch {} return re ? re.apply(this, arguments) : undefined; };
  const ce = console.error;
  console.error = function (...a) { try { window.__adgErrors.push("console.error[" + document.readyState + "]: " + a.map(text).join(" ")); } catch {} return ce.apply(this, a); };
  window.addEventListener("error", (e) => { try { window.__adgErrors.push("window.error: " + (e.message || "")); } catch {} });
})();`;

// A console error the application did not cause and that predates this work (DEMO-PUBLIC-01 §13.4).
const KNOWN = [/Failed to load resource: the server responded with a status of 404 .*@ https?:\/\/[^/]+\/favicon\.ico$/];
const IS_418 = /#418|Hydration failed|hydrat(ed|ion) .*(mismatch|didn't match)|did not match/i;

async function settle(page) {
  await page.waitForLoadState("load");
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(150);
}

async function oneRun(browser, run) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(HOOK);
  const page = await context.newPage();
  let events = [];
  page.on("console", (m) => { if (m.type() === "error") events.push(`console: ${m.text()} @ ${m.location().url}`); });
  page.on("pageerror", (e) => events.push(`pageerror: ${String(e.message || e)}`));
  const loads = [];
  const failures = [];

  async function record(kind, url, extra = {}) {
    const hooked = await page.evaluate(() => window.__adgErrors || []).catch(() => ["<hook unreadable>"]);
    const all = [...events, ...hooked.map((h) => `hook: ${h}`)];
    const r418 = all.filter((e) => IS_418.test(e));
    const app = events.filter((e) => !KNOWN.some((k) => k.test(e.replace(/^console: /, ""))));
    loads.push({ kind, url, react418: r418.length, react418_messages: r418.map((m) => m.slice(0, 220)), app_errors: app.filter((e) => !IS_418.test(e)), ...extra });
    events = [];
    await page.evaluate(() => { if (window.__adgErrors) window.__adgErrors.length = 0; }).catch(() => {});
  }

  const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  if (!login.ok()) failures.push(`sign-in ${login.status()}`);

  // A. cold loads at 1280 px
  for (const path of COLD) {
    const resp = await page.goto(`${BASE}${path}`, { waitUntil: "commit" });
    await settle(page);
    await record("cold", path, { status: resp ? resp.status() : null });
  }

  // B. the reviewer path by client-side navigation
  const soft = async (label, click, urlPattern, ready) => {
    try {
      await Promise.all([page.waitForURL(urlPattern, { timeout: 30000 }), click()]);
      if (ready) await ready();
      await settle(page);
      const same = await page.evaluate(() => window.__adgDocument === "marked");
      await record("client-nav", label, { same_document: same, url: page.url().replace(BASE, "") });
      if (!same) failures.push(`${label}: left the document`);
    } catch (e) {
      failures.push(`${label}: ${String(e.message || e).split("\n")[0]}`);
      await record("client-nav", label, { error: true });
    }
  };
  // The way back to the guide from any page: the Demo Mode indicator's panel, then its link.
  const toDemo = async () => {
    const summary = page.locator("summary", { hasText: "Demo environment" }).first();
    if (!(await page.getByRole("link", { name: "Explore the demo scenarios" }).first().isVisible())) await summary.click();
    await page.getByRole("link", { name: "Explore the demo scenarios" }).first().click();
  };
  await page.goto(`${BASE}/demo`, { waitUntil: "commit" });
  await settle(page);
  await record("cold", "/demo (start of client navigation)");
  await page.evaluate(() => { window.__adgDocument = "marked"; });
  const investigation = page.getByRole("region", { name: /Start here: a guided access investigation/ });
  const firstLink = (await investigation.count()) ? investigation.getByRole("link", { name: "Investigate access" }) : page.getByRole("link", { name: "Investigate access" }).first();
  await soft("demo -> investigation", () => firstLink.click(), /\/access\/investigate\?/,
    () => page.getByRole("region", { name: /What to inspect next/ }).waitFor());
  await soft("investigation -> unresolved SID", () => page.getByRole("region", { name: /What to inspect next/ }).getByRole("link", { name: "Inspect the SID" }).click(), /\/identities\/principal\?/,
    () => page.getByText(/could not resolve it/i).first().waitFor());
  await soft("back -> investigation", () => page.goBack(), /\/access\/investigate\?/,
    () => page.getByRole("region", { name: /What to inspect next/ }).waitFor());
  await soft("investigation -> removal analysis", () => page.getByRole("region", { name: /What to inspect next/ }).getByRole("link", { name: "Open the removal analysis" }).click(), /\/access\/explain\?/);
  await soft("explanation -> demo (nav)", toDemo, /\/demo$/);
  await soft("demo -> risk totals", () => page.getByRole("link", { name: /View the risk totals/ }).first().click(), /\/risks/,
    () => page.getByRole("heading", { name: "These counts are not exhaustive" }).waitFor());
  await soft("risks -> demo (nav)", toDemo, /\/demo$/);
  const outcome = page.getByRole("region", { name: "How this comparison ended" });
  await soft("demo -> drill (timed_out)", () => page.getByRole("link", { name: "Run the comparison drill" }).click(), /\/changes\/compare\?.*time_budget_ms=1/,
    () => outcome.getByText(/Timed out/).first().waitFor());
  await soft("drill -> retry (incomplete)", () => outcome.getByRole("navigation", { name: "Recovery actions" }).getByRole("link", { name: /Retry inside the standard 4-second window/ }).click(), /retry_of=/,
    () => outcome.getByText(/Incomplete — this is not the whole comparison/).waitFor());
  await soft("retry -> narrowed (recovered)", () => outcome.getByRole("navigation", { name: "Recovery actions" }).getByRole("link", { name: /Compare only folder permission entries/ }).click(), /kind=ntfs_ace/,
    () => outcome.getByText(/Complete — this comparison recovered the earlier attempt/).waitFor());

  // C. cold loads at 390 px
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of NARROW) {
    const resp = await page.goto(`${BASE}${path}`, { waitUntil: "commit" });
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await record("cold-390", path, { status: resp ? resp.status() : null, horizontal_overflow_px: overflow });
  }

  // D. reload loop at 1280 px
  await page.setViewportSize({ width: 1280, height: 900 });
  for (const path of RELOAD) {
    const resp = await page.goto(`${BASE}${path}`, { waitUntil: "commit" });
    await settle(page);
    await record("reload-first", path, { status: resp ? resp.status() : null });
    for (let k = 1; k <= 3; k++) {
      const again = await page.reload({ waitUntil: "commit" });
      await settle(page);
      await record("reload", path, { status: again ? again.status() : null, reload: k });
    }
  }

  await context.close();
  const total418 = loads.reduce((n, l) => n + l.react418, 0);
  const appErrors = loads.flatMap((l) => l.app_errors.map((e) => `${l.kind} ${l.url}: ${e}`));
  const bad = loads.filter((l) => l.status && l.status >= 400).map((l) => `${l.url} -> ${l.status}`);
  return {
    label: LABEL, run, at: new Date().toISOString(), base: BASE,
    loads: loads.length, cold_loads: loads.filter((l) => l.kind !== "client-nav").length,
    client_navigations: loads.filter((l) => l.kind === "client-nav").length,
    react418: total418, react418_loads: loads.filter((l) => l.react418 > 0).map((l) => `${l.kind} ${l.url}`),
    app_console_errors: appErrors, http_errors: bad, failures,
    detail: loads,
  };
}

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  for (let i = 1; i <= Number(RUNS); i++) {
    const r = await oneRun(browser, i);
    fs.appendFileSync(OUT, JSON.stringify(r) + "\n");
    console.log(`${LABEL} run ${i}: loads ${r.loads} (cold ${r.cold_loads}, client-nav ${r.client_navigations}), #418 ${r.react418}${r.react418 ? " on " + r.react418_loads.join(", ") : ""}, app console errors ${r.app_console_errors.length}, failures ${r.failures.length}${r.failures.length ? ": " + r.failures.join("; ") : ""}`);
  }
  await browser.close();
})();
