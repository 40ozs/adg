// ADG-UX-WAVE-C-INTEGRATION-01: width of the explain removal table vs its .table-scroll container, per build and width
// (explain-removal-table-width.txt).  NODE_PATH=… CHROMIUM_PATH=… node explain-table-probe.mjs "/access/explain?…" WaveC=<web> WaveB=<web>
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
const [explain, ...webs] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
for (const [name, web] of webs.map((w) => w.split("="))) for (const width of [1440, 1024, 768, 375, 320]) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  await ctx.request.post(`${web}/api/auth/dev-login`, { data: { username: "admin" } });
  const page = await ctx.newPage();
  await page.goto(`${web}${explain}`, { waitUntil: "networkidle" });
  const tables = await page.evaluate(() => [...document.querySelectorAll("main table")].map((t) => {
    let c = t.parentElement; while (c && getComputedStyle(c).overflowX === "visible") c = c.parentElement;
    const heads = [...t.querySelectorAll("thead th")].map((th) => th.textContent.trim().replace(/\s*↕.*$/, "")).join("|");
    const first = [...t.querySelectorAll("tbody tr > :first-child")].map((td) => td.textContent.trim()).sort((a, b) => b.length - a.length)[0] ?? "";
    return { heads, visible: t.offsetWidth > 0, table: t.scrollWidth, container: c?.clientWidth, cls: c?.className, longest: first.slice(0, 90) };
  }).filter((t) => /Relationship/.test(t.heads) && t.visible));
  const pageOver = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  for (const t of tables) console.log(`${name} ${width}px: removal table ${t.table}px in .${t.cls} ${t.container}px → internal scroll +${Math.max(0, t.table - t.container)}px; page overflow +${pageOver}; longest first cell "${t.longest}"`);
  await ctx.close();
}
await browser.close();
