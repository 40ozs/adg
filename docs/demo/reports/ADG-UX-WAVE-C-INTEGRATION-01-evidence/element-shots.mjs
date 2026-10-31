// ADG-UX-WAVE-C-INTEGRATION-01 element screenshots (screens/recently-viewed-*, explain-removals-1440).
// Records an identity, a server and a share in one tab, then shoots the Recently viewed section and the removal table.
//   NODE_PATH=<playwright 1.62.1> CHROMIUM_PATH=… node element-shots.mjs <WEB_URL> <OUT_DIR> "/access/explain?principal=…&resource=…"
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
const [WEB, OUT, explain] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.request.post(`${WEB}/api/auth/dev-login`, { data: { username: "admin" } });
const page = await ctx.newPage();
await page.goto(`${WEB}/identities/users`, { waitUntil: "networkidle" });
await page.locator("main a[href^='/identities/principal/']").first().click();
await page.locator("h1 .title-kind").waitFor();
await page.waitForTimeout(500);
await page.goto(`${WEB}/resources/server?key=gp-fin-fs01`, { waitUntil: "networkidle" });
await page.locator("main a[href^='/resources/share']").first().click();
await page.waitForURL((u) => u.pathname === "/resources/share");
await page.waitForTimeout(500);
for (const w of [1440, 320]) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
  const s = page.locator('#main section[aria-labelledby="overview-recent"]');
  await s.locator("ul").waitFor();
  await s.screenshot({ path: `${OUT}/recently-viewed-${w}.png` });
  await page.goto(`${WEB}${explain}`, { waitUntil: "networkidle" });
  const t = page.locator("main table:has([data-removal-edge])").first();
  await t.scrollIntoViewIfNeeded();
  await t.screenshot({ path: `${OUT}/explain-removals-${w}.png` });
}
await browser.close();
