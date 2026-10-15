// One-off browser check for ADG-DEMO-03, kept as evidence and not wired into any suite (the
// repository has no browser framework, and ADG-DEMO-02 recorded the same choice). Drives the
// real application in the pre-installed Chromium: the API in demo mode over a reset Null
// Meridian database, and `next start` of the production build.
//
//   NODE_PATH=$(npm root -g) node browser-walkthrough.script.js <output directory>
const { chromium } = require("playwright");
const fs = require("fs");
const OUT = process.argv[2];
const BASE = "http://localhost:3000";
const results = [];
const timings = [];
function check(name, ok, detail = "") { results.push({ name, ok: !!ok, detail: String(detail).slice(0, 600) }); }

async function tabTo(page, predicate, limit = 120) {
  await page.locator("body").focus();
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press("Tab");
    const found = await page.evaluate(predicate);
    if (found) return i + 1;
  }
  return null;
}

async function timedClick(page, locator, urlPattern, label) {
  const started = Date.now();
  await Promise.all([page.waitForURL(urlPattern), locator.click()]);
  await page.getByRole("region", { name: "How this comparison ended" }).waitFor();
  timings.push({ step: label, browser_ms: Date.now() - started });
}

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
  check("sign in as the development auditor", login.ok(), login.status());

  // The guide: the second walkthrough follows the investigation.
  await page.goto(`${BASE}/demo`);
  const card = page.getByRole("region", { name: "Then: how complete is the evidence, and what if a comparison cannot finish?" });
  check("demo guide offers the coverage and recovery walkthrough", await card.isVisible());
  const steps = await page.getByRole("list", { name: "Coverage, then recovery" }).getByRole("listitem").count();
  check("the walkthrough has ten steps", steps === 10, steps);
  await page.screenshot({ path: `${OUT}/01-demo-guide-coverage-recovery.png`, fullPage: true });

  // ---- Coverage. Step 1: the risk totals and how they may be read.
  await card.getByRole("link", { name: /View the risk totals/ }).click();
  await page.waitForURL(/\/risks/);
  const readingHeading = page.getByRole("heading", { name: "These counts are not exhaustive" });
  check("1. the totals are not called totals while a source is unavailable", await readingHeading.isVisible());
  check("1. the reading is an announced status region", await readingHeading.evaluate((el) => el.closest("[role=status]") !== null));
  const severity = await page.getByRole("list", { name: "Severity counts" }).innerText();
  check("1. the counts themselves are still shown (3 critical, 8 high)", /critical\s+3/.test(severity) && /high\s+8/.test(severity), severity);
  const held = await page.getByRole("list", { name: "Findings held from older evidence" }).innerText();
  check("1. the unavailable server's findings are held, not zeroed", /ntfs \(NM-FS-03\): 189 open finding\(s\)/.test(held) && /2026-09-17/.test(held), held);

  // Step 2: the coverage beside the counts.
  const line = page.getByRole("region", { name: "Collection behind these counts" });
  const lineText = await line.innerText();
  check("2. coverage line beside the totals", lineText.includes("Coverage: 98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence"), lineText);
  check("2. verdict in a word and a symbol, not color alone", /⚠\s*Incomplete/i.test(lineText), lineText);
  check("2. says the totals are not exhaustive", /Not exhaustive:/.test(lineText));
  check("2. names the snapshot", /Collected state [0-9a-f]{12} · 300 collector runs/.test(lineText), lineText);
  const tabs = await tabTo(page, () => document.activeElement?.tagName === "SUMMARY" && /Which scopes are not complete/.test(document.activeElement.textContent ?? ""));
  check("2. Tab reaches the incomplete-scopes disclosure", tabs !== null, tabs);
  await page.keyboard.press("Enter");
  const opened = await page.evaluate(() => document.activeElement?.parentElement?.hasAttribute("open"));
  check("2. Enter opens it", opened === true);
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  check("2. the focused disclosure shows a focus outline", outline !== "none", outline);
  await page.screenshot({ path: `${OUT}/02-risks-coverage-desktop.png`, fullPage: true });

  // Step 3: the unavailable source.
  const detail = await line.innerText();
  check("3. the unavailable source is named, with the age of its evidence", /ntfs \(NM-FS-03\): the latest scan could not reach its target\. ADG still holds what the run of 2026-09-17 collected/.test(detail), detail);
  await line.getByRole("link", { name: /collector status page/ }).click();
  await page.waitForURL(/\/collectors/);
  check("3. the collector status page shows NM-FS-03", /NM-FS-03/.test(await page.locator("main").innerText()));

  // Step 4: the investigation's answer, and the collection behind it.
  await page.goto(`${BASE}/demo`);
  await page.getByRole("region", { name: /Then: how complete is the evidence/ }).getByRole("link", { name: /Return to the access investigation/ }).click();
  await page.waitForURL(/\/access\/investigate\?.*#collection-coverage$/);
  const answer = page.getByRole("region", { name: /How complete is the evidence behind this answer/ });
  const answerText = await answer.innerText();
  check("4. the answer reads only complete scopes, and says the rest cannot change it", /Missing collection elsewhere cannot change this answer\./.test(answerText), answerText.slice(0, 500));
  const scopeItems = await answer.getByRole("list", { name: "Collection scopes this answer reads" }).getByRole("listitem").allInnerTexts();
  check("4. the four scopes the answer reads, each collected", scopeItems.length === 4 && scopeItems.every((t) => /✓\s*Collected/i.test(t)), scopeItems.join(" | "));
  const observed = await page.getByRole("region", { name: /What ADG evaluated/ }).innerText();
  check("4. the investigation's own answer is unchanged: Modify, at_least", /Modify/.test(observed) && /at_least/.test(observed), observed.slice(0, 300));
  await answer.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/03-investigation-collection-coverage.png`, fullPage: false });

  // ---- Recovery. Step 5: launch the drill from the guide.
  await page.goto(`${BASE}/demo`);
  const launch = page.getByRole("link", { name: "Run the comparison drill" });
  await timedClick(page, launch, /\/changes\/compare\?.*time_budget_ms=1/, "drill: 1 ms window, estate-wide");
  const outcome = page.getByRole("region", { name: "How this comparison ended" });
  // Step 6: the timed-out state.
  const t1 = await outcome.innerText();
  check("6. timed out, stated in words and a symbol", /⏱\s*Timed out/i.test(t1) && /Timed out — no comparison is presented/.test(t1), t1.slice(0, 400));
  check("6. the state is announced (role=status, aria-live)", await outcome.locator(".banner[role=status][aria-live=polite]").count() === 1);
  check("6. each recovery action carries its own live 'comparing…' status", await outcome.getByRole("navigation", { name: "Recovery actions" }).locator("a [role=status][aria-live=polite]").count() >= 2);
  const main1 = await page.locator("main").innerText();
  check("6. no partial list is presented, and nothing says 'nothing is different'", !/Nothing is different/.test(main1) && (await page.getByRole("region", { name: /Differences/ }).count()) === 0, "");
  await page.screenshot({ path: `${OUT}/04-comparison-timed-out.png`, fullPage: true });
  // Step 7: the reason, by keyboard.
  const toDiag = await tabTo(page, () => document.activeElement?.tagName === "SUMMARY" && /Diagnostics/.test(document.activeElement.textContent ?? ""));
  check("7. Tab reaches Diagnostics", toDiag !== null, toDiag);
  await page.keyboard.press("Enter");
  const diag = await page.evaluate(() => document.activeElement?.parentElement?.innerText ?? "");
  check("7. Diagnostics name the attempt, the window and what was read", /Comparison [0-9a-f-]{36}/.test(diag) && /Window 1 ms/.test(diag) && /Objects read: 4,898 at the earlier instant, 5,000 at the later/.test(diag), diag);
  const firstId = (diag.match(/Comparison ([0-9a-f-]{36})/) || [])[1];
  // Step 8: retry inside the standard window.
  const actions = outcome.getByRole("navigation", { name: "Recovery actions" });
  check("8. recovery actions offered", await actions.isVisible());
  await timedClick(page, actions.getByRole("link", { name: /Retry inside the standard 4-second window/ }), /retry_of=/, "retry: standard window, estate-wide");
  const t2 = await outcome.innerText();
  check("8. the retry stops as incomplete, at a named boundary, and says so", /◐\s*Incomplete/i.test(t2) && /Incomplete — this is not the whole comparison/.test(t2) && /group memberships up to S-1-5-21-/.test(t2), t2.slice(0, 500));
  check("8. the retry names the first attempt", new URL(page.url()).searchParams.get("retry_of") === firstId, page.url());
  check("8. its list is labelled partial", await page.getByRole("heading", { name: "What was compared before the comparison stopped (partial)" }).isVisible());
  await page.screenshot({ path: `${OUT}/05-comparison-incomplete.png`, fullPage: false });
  // Step 9: narrow to folder permission entries.
  await timedClick(page, outcome.getByRole("navigation", { name: "Recovery actions" }).getByRole("link", { name: /Compare only folder permission entries \(3,947 → 4,124 objects\)/ }), /kind=ntfs_ace/, "recovery: folder permission entries only");
  // Step 10: complete, and recovered.
  const t3 = await outcome.innerText();
  check("10. the final comparison is marked complete and recovered", /✓\s*Complete/i.test(t3) && /Complete — this comparison recovered the earlier attempt/.test(t3), t3.slice(0, 400));
  check("10. no recovery is offered for a complete comparison", (await outcome.getByRole("navigation", { name: "Recovery actions" }).count()) === 0);
  const diffs = page.getByRole("region", { name: "Differences" });
  check("10. the complete comparison presents its differences", await diffs.isVisible());
  await page.screenshot({ path: `${OUT}/06-comparison-recovered.png`, fullPage: false });

  // Phone width: no sideways scroll on any new surface.
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, storageState: await context.storageState() });
  const phone = await mobile.newPage();
  const drillUrl = await page.evaluate(() => location.href);
  for (const [label, url] of [
    ["risks", `${BASE}/risks`],
    ["investigation", `${BASE}/access/investigate?principal=S-1-5-21-3949361974-1583827165-633756269-1323&resource=${encodeURIComponent("\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo")}`],
    ["comparison (timed out)", `${BASE}/changes/compare?from=${encodeURIComponent("2026-09-03T06:00:00Z")}&to=${encodeURIComponent("2026-10-01T06:00:00Z")}&time_budget_ms=1`],
    ["comparison (incomplete)", `${BASE}/changes/compare?from=${encodeURIComponent("2026-09-03T06:00:00Z")}&to=${encodeURIComponent("2026-10-01T06:00:00Z")}`],
    ["comparison (recovered)", drillUrl],
  ]) {
    await phone.goto(url);
    const overflow = await phone.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    check(`no horizontal scroll at 390px: ${label}`, overflow.scroll <= overflow.client, JSON.stringify(overflow));
  }
  await phone.goto(`${BASE}/risks`);
  await phone.getByRole("region", { name: "Collection behind these counts" }).scrollIntoViewIfNeeded();
  await phone.screenshot({ path: `${OUT}/07-risks-coverage-phone.png`, fullPage: false });
  await phone.goto(`${BASE}/changes/compare?from=${encodeURIComponent("2026-09-03T06:00:00Z")}&to=${encodeURIComponent("2026-10-01T06:00:00Z")}`);
  await phone.screenshot({ path: `${OUT}/08-comparison-incomplete-phone.png`, fullPage: false });

  const known = consoleErrors.filter((e) => /\/favicon\.ico$/.test(e));
  const other = consoleErrors.filter((e) => !known.includes(e));
  check("no console errors from the application", other.length === 0, other.join(" | ") + " || " + failed.join(" | "));
  results.push({ name: "known, pre-existing: /favicon.ico 404", ok: true, detail: known.join(" | ") });
  await browser.close();
  fs.writeFileSync(`${OUT}/browser-walkthrough.json`, JSON.stringify({ results, passed: results.filter((r) => r.ok).length, total: results.length, timings }, null, 2));
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.ok ? "" : "  -- " + r.detail}`);
  console.log(JSON.stringify(timings));
})().catch((e) => { console.error(e); process.exit(1); });
