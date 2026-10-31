// ADG-UX-WAVE-B-INTEGRATION-01: no nested links/buttons, no scripted non-interactive "cards", one h1,
// on the pages the Wave B lanes touch. Read-only apart from a development sign-in.
//   CHROMIUM_PATH=… NODE_PATH=<playwright> node nested-interactive-check.mjs   (web on 127.0.0.1:3021)
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
const W = "http://127.0.0.1:3021";
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
const c = await b.newContext(); await c.request.post(`${W}/api/auth/dev-login`, { data: { username: "admin" } });
const p = await c.newPage();
await p.goto(`${W}/identities/users`, { waitUntil: "networkidle" });
const user = await p.locator("main a[href^='/identities/principal/']").first().getAttribute("href");
let bad = 0;
for (const path of ["/", "/risks", user, `${user}/groups`, "/resources", "/access/investigate", "/governance"]) {
  await p.goto(W + path, { waitUntil: "networkidle" });
  await p.waitForFunction(() => !document.querySelector('#main [aria-busy="true"]'));
  const r = await p.evaluate(() => ({
    nested: document.querySelectorAll("a a, a button, button a, button button").length,
    clickableNonInteractive: [...document.querySelectorAll("main [onclick], main div[role=link], main li[role=link]")].length,
    h1: document.querySelectorAll("h1").length,
  }));
  if (r.nested || r.clickableNonInteractive || r.h1 !== 1) bad++;
  console.log(`${path.replace(/S-1-[\d-]+/, "<sid>")}: nested interactive ${r.nested}, scripted non-interactive ${r.clickableNonInteractive}, h1 ${r.h1}`);
}
console.log(bad ? `FAIL ${bad}` : "PASS no nested links/buttons, one h1 on every page");
await b.close(); process.exit(bad ? 1 : 0);
