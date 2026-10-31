// Element screenshots for the report: the removal table and the reviewer table.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
const WEB = process.env.WEB_URL, C = JSON.parse(readFileSync(process.env.CAMPAIGNS_JSON, "utf8"));
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
for (const width of [1440, 375]) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username: "admin" } });
  const page = await context.newPage();
  const q = new URLSearchParams({ principal: C.person.sid, resource: C.person.resource });
  await page.goto(`${WEB}/access/explain?${q}`, { waitUntil: "networkidle" });
  const table = page.locator("table:has([data-removal-edge])");
  await table.locator("tbody tr").nth(4).evaluate((row) => { for (const r of [...row.parentElement.rows].slice(5)) r.remove(); });
  await table.screenshot({ path: `screens/explain-removal-table-${width}.png` });
  await page.goto(`${WEB}/governance/campaigns/${C.identity_campaign}`, { waitUntil: "networkidle" });
  await page.locator("section[aria-labelledby='reviewers']").screenshot({ path: `screens/campaign-reviewers-table-${width}.png` });
  await context.close();
}
await browser.close();
