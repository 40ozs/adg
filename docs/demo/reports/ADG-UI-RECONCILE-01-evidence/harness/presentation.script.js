// ADG-UI-RECONCILE-01 presentation evidence: the two current-main fixes and the compact
// identity rows, measured in the browser (computed styles, geometry), with screenshots.
//   NODE_PATH=… ADG_WEB_URL=… node presentation.js <out dir>
const { chromium } = require("playwright");
const fs = require("fs");

const OUT = process.argv[2];
const BASE = process.env.ADG_WEB_URL || "http://localhost:3000";
const CHROME = process.env.CHROMIUM_PATH;
const UNRESOLVED = "S-1-5-21-3949361974-1583827165-633756269-1108";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + String(detail).slice(0, 160) : ""}`);
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  const errors = [];
  for (const [label, viewport] of [["desktop", { width: 1360, height: 900 }], ["phone", { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    page.on("console", (m) => { if (m.type() === "error" && !/favicon\.ico/.test(m.location().url)) errors.push(`${label}: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`${label}: ${e.message}`));
    const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "viewer" } });
    check(`${label}: sign in as viewer`, login.ok(), login.status());

    // /risks: the severity strip has no list markers, and no item overlaps the next.
    await page.goto(`${BASE}/risks`, { waitUntil: "networkidle" });
    const strip = await page.locator('ul.filter-strip[aria-label="Severity counts"]').evaluate((ul) => {
      const items = [...ul.children].map((li) => li.getBoundingClientRect());
      const overlaps = items.slice(1).filter((r, i) => r.top === items[i].top && r.left < items[i].right).length;
      return { listStyle: getComputedStyle(ul).listStyleType, marker: getComputedStyle(ul.children[0], "::marker").content, role: ul.getAttribute("role"), items: items.length, overlaps, text: ul.innerText.replace(/\s+/g, " ") };
    });
    check(`${label}: /risks severity strip has no markers`, strip.listStyle === "none", JSON.stringify(strip));
    check(`${label}: /risks severity items do not overlap`, strip.overlaps === 0 && strip.items === 5, strip.text);
    check(`${label}: /risks counts unchanged (3/8/1957/423/6995)`, /critical 3 high 8 medium 1957 low 423 informational 6995/i.test(strip.text), strip.text);
    await page.locator("section[aria-label=Summary]").screenshot({ path: `${OUT}/risks-severity-strip-${label}.png` });

    // Deleted state: the banner and Identity information agree.
    await page.goto(`${BASE}/identities/principal/${UNRESOLVED}`, { waitUntil: "networkidle" });
    const banner = await page.getByRole("alert").filter({ hasText: "could not resolve it" }).innerText();
    const deleted = await page.locator("dt", { hasText: /^Deleted$/ }).locator("xpath=following-sibling::dd[1]").innerText();
    check(`${label}: unresolved principal banner says (deleted)`, /\(deleted\)/.test(banner), banner.replace(/\s+/g, " "));
    check(`${label}: Identity information agrees: Deleted yes`, /^yes\b/.test(deleted.trim()), deleted.trim());
    await page.screenshot({ path: `${OUT}/unresolved-principal-${label}.png`, fullPage: false });

    // Compact identity: Groups directory rows are one line; name cells hold no SID.
    await page.goto(`${BASE}/identities/groups?exclude_local=1`, { waitUntil: "networkidle" });
    const rows = await page.locator("table tbody tr").evaluateAll((trs) => trs.map((tr) => {
      const name = tr.querySelector("th, td");
      return { h: tr.getBoundingClientRect().height, nameText: name ? name.innerText : "" };
    }));
    const heights = rows.map((r) => r.h);
    check(`${label}: Groups rows rendered`, rows.length > 0, rows.length);
    check(`${label}: no SID under a name in the Name column`, rows.every((r) => !/S-1-5-/.test(r.nameText)), rows.find((r) => /S-1-5-/.test(r.nameText))?.nameText);
    check(`${label}: Groups rows are single-line (max height)`, Math.max(...heights) < 48, `max ${Math.max(...heights).toFixed(1)}px, min ${Math.min(...heights).toFixed(1)}px`);
    const sid = await page.locator("table tbody code.identifier.sid").first().evaluate((el) => ({ title: el.title, text: el.textContent, clipped: el.scrollWidth > el.clientWidth, dir: getComputedStyle(el).direction, ws: getComputedStyle(el).whiteSpace }));
    check(`${label}: SID cell keeps the full value (text and title), one line, cut at the start`, sid.title === sid.text && sid.dir === "rtl" && sid.ws === "nowrap", JSON.stringify(sid));
    const overflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    check(`${label}: no page-level horizontal scroll on Groups`, overflow.scroll <= overflow.client, JSON.stringify(overflow));
    await page.screenshot({ path: `${OUT}/groups-exclude-local-${label}.png`, fullPage: false });
    await context.close();
  }
  check("no application console or page errors", errors.length === 0, errors.join(" | "));
  await browser.close();
  fs.writeFileSync(`${OUT}/presentation.json`, JSON.stringify({ base: BASE, results }, null, 2));
  process.exit(results.every((r) => r.ok) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
