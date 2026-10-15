// Positive control for the React #418 gate (ADG-NEXT16-POST-DEMO-INTEGRATION-01).
// Proves the gate's detection works on a given build: Playwright rewrites one heading in the
// server HTML of /demo (the DOM text only; the RSC payload in the same document is untouched),
// so the client's render cannot match and React must report a hydration mismatch. The same
// three channels as react418-gate.script.js are read (console, pageerror, an init-script hook
// on window.reportError and console.error).
//
//   NODE_PATH=$(npm root -g) node positive-control.script.js <base url> <label> <loads> <out.jsonl>
const { chromium } = require("playwright");
const fs = require("fs");

const [BASE, LABEL, LOADS, OUT] = process.argv.slice(2);
const HOOK = `(() => {
  window.__adgErrors = [];
  const text = (x) => (x && (x.message || x.stack)) ? String(x.message || x.stack) : String(x);
  const re = window.reportError;
  window.reportError = function (e) { try { window.__adgErrors.push("reportError: " + text(e)); } catch {} return re ? re.apply(this, arguments) : undefined; };
  const ce = console.error;
  console.error = function (...a) { try { window.__adgErrors.push("console.error: " + a.map(text).join(" ")); } catch {} return ce.apply(this, a); };
})();`;
const IS_418 = /#418|Hydration failed|hydrat(ed|ion) .*(mismatch|didn't match)|did not match/i;
const FROM = '<h2 id="demo-investigation">Start here';
const TO = '<h2 id="demo-investigation">CONTROL here';

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(HOOK);
  const page = await context.newPage();
  let events = [];
  page.on("console", (m) => { if (m.type() === "error") events.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => events.push(`pageerror: ${String(e.message || e)}`));
  await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  let mutated = 0;
  await page.route(`${BASE}/demo`, async (route) => {
    const resp = await route.fetch();
    const body = await resp.text();
    if (body.includes(FROM)) mutated++;
    await route.fulfill({ response: resp, body: body.replace(FROM, TO) });
  });
  const loads = [];
  for (let i = 1; i <= Number(LOADS); i++) {
    events = [];
    await page.goto(`${BASE}/demo`, { waitUntil: "load" });
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
    const hooked = await page.evaluate(() => window.__adgErrors || []);
    const all = [...events, ...hooked.map((h) => `hook: ${h}`)];
    const heading = await page.locator("#demo-investigation").innerText();
    loads.push({ load: i, detected: all.some((e) => IS_418.test(e)), channels: {
      console: events.some((e) => e.startsWith("console:") && IS_418.test(e)),
      pageerror: events.some((e) => e.startsWith("pageerror:") && IS_418.test(e)),
      hook: hooked.some((h) => IS_418.test(h)),
    }, client_rerendered_heading: heading, sample: all.filter((e) => IS_418.test(e)).slice(0, 2).map((s) => s.slice(0, 300)) });
  }
  await browser.close();
  const detected = loads.filter((l) => l.detected).length;
  const r = { label: LABEL, base: BASE, at: new Date().toISOString(), loads: loads.length, html_mutated: mutated, detected, detail: loads };
  fs.appendFileSync(OUT, JSON.stringify(r) + "\n");
  console.log(`${LABEL}: ${detected}/${loads.length} mutated loads detected as #418 (html mutated ${mutated}); channels on load 1: ${JSON.stringify(loads[0].channels)}; heading after hydration: "${loads[0].client_rerendered_heading}"; sample: ${loads[0].sample[0] || "-"}`);
})();
