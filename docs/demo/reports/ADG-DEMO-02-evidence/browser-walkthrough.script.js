// One-off browser check for ADG-DEMO-02 (not committed): drives the real app in Chromium.
const { chromium } = require("playwright");
const fs = require("fs");
const OUT = process.argv[2];
const BASE = "http://localhost:3000";
const results = [];
function check(name, ok, detail = "") { results.push({ name, ok: !!ok, detail }); }

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(`${m.text()} @ ${m.location().url}`); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  const failed = [];
  page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });

  const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  check("sign in as the development auditor", login.ok(), String(login.status()));

  // Step 1: the seeded investigation, from the demo guide.
  await page.goto(`${BASE}/demo`);
  const card = page.getByRole("region", { name: "Start here: a guided access investigation" });
  check("demo guide offers the guided investigation", await card.isVisible());
  const question = await card.locator("strong").first().innerText();
  check("the card states the question", /^Why does Fatima Gordon have access to \\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo\?$/.test(question), question);
  const steps = await page.getByRole("list", { name: "The five-minute walkthrough" }).getByRole("listitem").count();
  check("the walkthrough has eight steps", steps === 8, String(steps));
  await page.screenshot({ path: `${OUT}/01-demo-guide-entry.png`, fullPage: false });
  await card.getByRole("link", { name: "Investigate access" }).click();
  await page.waitForURL(/\/access\/investigate\?/);

  // Step 2: the question.
  const h1 = await page.getByRole("heading", { level: 1 }).innerText();
  check("the page heading is the investigation question", h1 === question, h1);
  check("the page says the data is synthetic", await page.getByText(/^Synthetic scenario\./).isVisible());

  // Step 3: observed access.
  const observed = page.getByRole("region", { name: /What ADG evaluated/ });
  const observedText = await observed.innerText();
  check("observed: access present, Modify, at_least", /Access present/.test(observedText) && /Modify/.test(observedText) && /at_least/.test(observedText), observedText.slice(0, 300));

  // Step 4: the route.
  const ntfs = page.getByRole("article", { name: "Folder permissions (NTFS ACL)" });
  const share = page.getByRole("article", { name: "Share permissions (SMB share ACL)" });
  const ntfsText = await ntfs.innerText();
  const shareText = await share.innerText();
  check("NTFS route: direct, explicit, removal changes nothing", /direct entry/i.test(ntfsText) && /set explicitly on this folder/i.test(ntfsText) && /Removing it changes nothing/.test(ntfsText), ntfsText.slice(0, 400));
  check("share route: through ENG-Mobile", /through 1 group membership/i.test(shareText) && /Fatima Gordon is a member of ENG-Mobile/.test(shareText), shareText.slice(0, 400));

  // Step 5: provenance, by keyboard only.
  await page.keyboard.press("Home");
  await page.locator("body").focus();
  let reached = null;
  for (let i = 0; i < 80; i++) {
    await page.keyboard.press("Tab");
    const tag = await page.evaluate(() => document.activeElement?.tagName);
    const text = await page.evaluate(() => document.activeElement?.textContent ?? "");
    if (tag === "SUMMARY" && /Evidence for hop 1/.test(text)) { reached = { i, text }; break; }
  }
  check("Tab reaches the first hop's evidence disclosure", reached !== null, JSON.stringify(reached));
  await page.keyboard.press("Enter");
  const opened = await page.evaluate(() => document.activeElement?.parentElement?.hasAttribute("open"));
  check("Enter opens the disclosure", opened === true);
  const evidenceText = await page.evaluate(() => document.activeElement?.parentElement?.innerText ?? "");
  check("the evidence names the collector and scans", /NTFS collector/.test(evidenceText) && /2026-09-03/.test(evidenceText) && /2026-10-01/.test(evidenceText) && /succeeded/.test(evidenceText), evidenceText.slice(0, 400));
  await page.keyboard.press("Enter");
  const closed = await page.evaluate(() => !document.activeElement?.parentElement?.hasAttribute("open"));
  check("Enter closes it again", closed === true);
  const focusOutline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  check("the focused disclosure shows a focus outline", focusOutline !== "none", focusOutline);

  // Step 6: what could not be confirmed.
  const gaps = page.getByRole("region", { name: /What ADG could not confirm/ });
  const gapsText = await gaps.innerText();
  check("two missing-evidence items, neither affecting the conclusion", (gapsText.match(/Missing evidence/g) || []).length === 2 && (gapsText.match(/Does not affect the conclusion\./g) || []).length === 2, gapsText.slice(0, 600));
  check("the orphaned SID stays visibly unresolved", /NULLMERIDIAN\\lhansen/.test(gapsText) && /unresolved \(deleted\)/.test(gapsText));
  check("Proj-Zephyr-RW is named", /Proj-Zephyr-RW/.test(gapsText));
  const warnColor = await gaps.locator(".principal-notes.status-warn").first().evaluate((el) => getComputedStyle(el).color);
  check("the unresolved note is styled as a warning (and worded)", !!warnColor, warnColor);

  // Step 7: the summary.
  const summary = page.getByRole("region", { name: /Investigation summary/ });
  const dts = await summary.locator("dt").allInnerTexts();
  check("seven questions answered", dts.length === 7, dts.join(" | "));
  check("the summary says the uncertainty does not take access away", /none of them can take that access away/.test(await summary.innerText()));
  await page.screenshot({ path: `${OUT}/02-investigation-desktop.png`, fullPage: true });

  // Step 8: the next step, followed.
  const next = page.getByRole("region", { name: /What to inspect next/ });
  const nextItems = await next.getByRole("listitem").allInnerTexts();
  check("three next steps", nextItems.length === 3, nextItems.join(" | "));
  await next.getByRole("link", { name: "Inspect the SID" }).click();
  await page.waitForURL(/\/identities\/principal\?/);
  const principalText = await page.locator("main").innerText();
  check("the SID's page shows it unresolved (ADG-DEMO-01 banner)", /could not resolve it/i.test(principalText), principalText.slice(0, 300));
  await page.goBack();
  await page.getByRole("region", { name: /What to inspect next/ }).getByRole("link", { name: "Open the removal analysis" }).click();
  await page.waitForURL(/\/access\/explain\?/);
  check("the removal analysis is the anchor target", (await page.url()).endsWith("#removals") && await page.locator("#removals").isVisible());
  check("the explanation links back to the investigation", await page.getByRole("link", { name: "Read this answer as a guided investigation" }).isVisible());

  // SYSTEM through the investigation (ADG-DEMO-01).
  await page.goto(`${BASE}/access/investigate?principal=S-1-5-18&resource=${encodeURIComponent("\\\\NM-FS-01\\Shared")}`);
  const systemText = await page.locator("main").innerText();
  const noAccessAt = systemText.search(/\bNo access\b/);
  check("SYSTEM over a share: not determined, ambiguous, never 'No access'", /Not determined/.test(systemText) && /Ambiguous/.test(systemText) && noAccessAt < 0, noAccessAt < 0 ? "" : systemText.slice(Math.max(0, noAccessAt - 300), noAccessAt + 100));
  await page.screenshot({ path: `${OUT}/04-system-over-share.png`, fullPage: true });
  await page.goto(`${BASE}/access/investigate?principal=S-1-5-18&resource=${encodeURIComponent("\\\\NM-FS-01\\Shared")}&access_path=local`);
  const localText = await page.locator("main").innerText();
  check("SYSTEM locally: present, confirmed mapping", /Access present/.test(localText) && /Confirmed/.test(localText), localText.slice(0, 300));

  // Phone width: no sideways scroll.
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, storageState: await context.storageState() });
  const phone = await mobile.newPage();
  await phone.goto(page.url().replace(/access\/investigate.*/, "") + `access/investigate?principal=S-1-5-21-3949361974-1583827165-633756269-1323&resource=${encodeURIComponent("\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo")}`);
  const overflow = await phone.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  check("no horizontal scroll at 390px", overflow.scroll <= overflow.client, JSON.stringify(overflow));
  await phone.screenshot({ path: `${OUT}/03-investigation-phone.png`, fullPage: false });

  // The application ships no favicon (true before this change too), so the browser's own
  // /favicon.ico request 404s. Recorded, and not counted against the page.
  const known = consoleErrors.filter((e) => /\/favicon\.ico$/.test(e));
  const other = consoleErrors.filter((e) => !known.includes(e));
  check("no console errors from the application", other.length === 0, other.join(" | ").slice(0, 500) + " || " + failed.join(" | "));
  results.push({ name: "known, pre-existing: /favicon.ico 404", ok: true, detail: known.join(" | ") });
  fs.writeFileSync(`${OUT}/failed-responses.txt`, failed.join("\n"));
  await browser.close();
  fs.writeFileSync(`${OUT}/browser-walkthrough.json`, JSON.stringify({ results, passed: results.filter((r) => r.ok).length, total: results.length }, null, 2));
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.ok ? "" : "  -- " + r.detail}`);
})().catch((e) => { console.error(e); process.exit(1); });
