// One-off check for DEMO-PUBLIC-01, kept as evidence and not wired into any suite (the
// repository has no browser framework; ADG-DEMO-02 and -03 recorded the same choice).
// It follows the reviewer path in README.md and docs/demo/README.md against a running demo and
// checks that every label, route and number those documents cite is what the application shows.
//
//   NODE_PATH=$(npm root -g) node docs-walkthrough.script.js <output directory>
const { chromium } = require("playwright");
const fs = require("fs");
const OUT = process.argv[2];
const BASE = process.env.ADG_WEB_URL || "http://localhost:3000";
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
}
const has = async (page, text) => (await page.locator("body").innerText()).includes(text);
// A section is found by the id ADG gives it or its heading: the innermost <section> holding it.
const sec = (page, id) => page.locator(`section:has(#${id}), section#${id}`).last();

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(`${m.text()} @ ${m.location().url}`); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  // Before you start: /login offers the auditor account; the header and footer say demo.
  await page.goto(`${BASE}/login`);
  check("README: /login offers the auditor account", await has(page, "auditor"));
  check("guide: the header pill says Demo environment before sign-in", await has(page, "Demo environment") || await has(page, "DEMO ENVIRONMENT"));
  const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  check("sign in as auditor", login.ok(), login.status());

  await page.goto(`${BASE}/demo`);
  const body = await page.locator("body").innerText();
  check("guide: pill reads Synthetic enterprise data · Null Meridian", body.includes("Synthetic enterprise data") && body.includes("Null Meridian"));
  check("guide: footer marks the page as fictional", body.includes("the organization and its data are fictional"));
  check("guide: /demo says no directory, file server or collector is connected", /No Active Directory, file server or collector is connected/.test(body));
  check("README step 2: 'Start here: a guided access investigation'", body.includes("Start here: a guided access investigation"));
  check("README step 8: 'Run the comparison drill' is offered", body.includes("Run the comparison drill"));
  check("guide W3.1: the drill says it is a deliberately short 1 ms window", body.includes("deliberately short 1 ms window"));

  // Walkthrough 1.
  await page.getByRole("link", { name: "Investigate access" }).first().click();
  await page.waitForURL(/\/access\/investigate/);
  await page.locator("#observed-access").waitFor();
  const heading = await page.locator("h1").innerText();
  check("W1.1: the heading is the question", /Why does Fatima Gordon have access to/.test(heading) && heading.includes("Apollo"), heading);
  check("W1.1: a note says the scenario is synthetic", await has(page, "Synthetic scenario"));
  const observed = await sec(page, "observed-access").innerText();
  check("W1.2: granted (Access present), Modify, at_least, over the network", /Access present/.test(observed) && /Modify/.test(observed) && /at_least/.test(observed) && /share ACL and NTFS ACL/.test(observed), observed.slice(0, 300));
  const routes = await sec(page, "access-routes").innerText();
  check("W1.3: NTFS route is a direct entry set explicitly on the folder", /direct entry/i.test(routes) && /set explicitly on this folder/i.test(routes) && routes.includes("NTFS entry #1"));
  check("W1.3: share route runs through ENG-Mobile to share entry #3", routes.includes("ENG-Mobile") && routes.includes("Share entry #3"));
  check("W1.3: 'Every other route ADG found' disclosure exists", routes.includes("Every other route ADG found"));
  const other = sec(page, "access-routes").locator("details", { hasText: "Every other route ADG found" }).first();
  await other.locator("summary").first().click();
  const otherText = await other.innerText();
  check("W1.3: the other routes include an inherited one through the engineering groups", /inherited/i.test(otherText) && otherText.includes("Engineering-Frontend") && otherText.includes("Share_Engineering_Modify"));
  check("W1.4: 'Evidence for hop N' disclosures exist", routes.includes("Evidence for hop 1"));
  const hop = sec(page, "access-routes").locator("details", { hasText: "Evidence for hop 1" }).first();
  await hop.locator("summary").first().click();
  const hopText = await hop.innerText();
  check("W1.4: hop evidence names the collector and the scans", /collector/i.test(hopText) && /2026-09-03|2026-10-01/.test(hopText), hopText.slice(0, 300));
  const open = await sec(page, "open-questions").innerText();
  check("W1.5: lhansen and Proj-Zephyr-RW are missing evidence that does not affect the conclusion",
    open.includes("lhansen") && open.includes("Proj-Zephyr-RW") && (open.match(/Missing evidence/g) || []).length >= 2 && (open.match(/Does not affect the conclusion/g) || []).length >= 2);
  const summary = await sec(page, "investigation-summary").innerText();
  check("W1.6: the summary says the rights are a lower bound", /lower bound/.test(summary));
  const coverage = await sec(page, "collection-coverage").innerText();
  check("W1 optional: section 6 reads four collected scopes and says elsewhere cannot change it",
    (coverage.match(/collected/g) || []).length >= 4 && /cannot change this answer/.test(coverage));
  check("W1: 'Investigate a different pair' is offered", await has(page, "Investigate a different pair"));

  // The SYSTEM aside.
  await page.goto(`${BASE}/access/investigate?principal=S-1-5-18&resource=${encodeURIComponent("\\\\NM-FS-01\\Shared")}`);
  await page.locator("#observed-access").waitFor();
  const system = await sec(page, "observed-access").innerText();
  check("W1 aside: SYSTEM over SMB is Not determined, mapping ambiguous, never No access",
    system.includes("Not determined") && system.includes("ambiguous") && !system.includes("No access"), system.slice(0, 300));

  // Walkthrough 2.
  await page.goto(`${BASE}/risks`);
  await page.locator("#risk-reading").waitFor();
  const reading = await page.locator("#risk-reading").innerText();
  check("W2.2: 'These counts are not exhaustive'", reading.includes("These counts are not exhaustive"));
  check("W2.5: 189 open findings on NM-FS-03 stay counted", /NM-FS-03\)?:? 189 open finding/.test(reading) || (reading.includes("NM-FS-03") && reading.includes("189")), reading.slice(0, 400));
  const severity = await page.getByRole("list", { name: "Severity counts" }).innerText();
  check("W2.1: 3 critical, 8 high, 1,957 medium, 423 low, 6,995 informational",
    /critical\s+3/.test(severity) && /high\s+8/.test(severity) && /medium\s+1,?957/.test(severity) && /low\s+423/.test(severity) && /informational\s+6,?995/.test(severity), severity);
  const card = await page.locator("#risk-coverage").innerText();
  check("W2.3: the coverage line is a count of scopes", card.includes("98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence"));
  check("W2.6: 'Not exhaustive:' beside the counts", card.includes("Not exhaustive:"));
  check("W2.4: 'Which scopes are not complete (2)'", card.includes("Which scopes are not complete (2)"));
  await page.locator("#risk-coverage summary", { hasText: "Which scopes are not complete" }).click();
  const scopes = await page.locator("#risk-coverage").innerText();
  check("W2.4: NM-FS-03 could not be reached and holds the 2026-09-17 scan; NM-MIG-FS01 partial",
    scopes.includes("NM-FS-03") && scopes.includes("2026-09-17") && scopes.includes("NM-MIG-FS01") && /partial/.test(scopes));
  await page.goto(`${BASE}/collectors`);
  check("W2.4: /collectors names NM-FS-03", await has(page, "NM-FS-03"));

  // Walkthrough 3.
  await page.goto(`${BASE}/demo`);
  await Promise.all([page.waitForURL(/\/changes\/compare/), page.getByRole("link", { name: "Run the comparison drill" }).click()]);
  const ended = page.getByRole("region", { name: "How this comparison ended" });
  await ended.waitFor();
  let text = await ended.innerText();
  check("W3.2: 'Timed out — no comparison is presented'", text.includes("Timed out — no comparison is presented"));
  check("W3.2: Diagnostics is offered", text.includes("Diagnostics"));
  check("W3.3: 'Retry inside the standard 4-second window' is offered", text.includes("Retry inside the standard 4-second window"));
  check("W3.4: 'Compare only folder permission entries (3,947 → 4,124 objects)' is offered", text.includes("Compare only folder permission entries (3,947 → 4,124 objects)"));
  await Promise.all([page.waitForURL(/retry_of=/), ended.getByRole("link", { name: "Retry inside the standard 4-second window" }).click()]);
  await page.getByRole("region", { name: "How this comparison ended" }).getByText("Incomplete — this is not the whole comparison").waitFor({ timeout: 30000 });
  text = await page.getByRole("region", { name: "How this comparison ended" }).innerText();
  check("W3.3: 'Incomplete — this is not the whole comparison', 5,000 per instant", text.includes("Incomplete — this is not the whole comparison") && text.includes("5,000 per instant"));
  await Promise.all([page.waitForURL(/kind=/), page.getByRole("region", { name: "How this comparison ended" }).getByRole("link", { name: /Compare only folder permission entries/ }).click()]);
  await page.getByRole("region", { name: "How this comparison ended" }).getByText("Complete — this comparison recovered the earlier attempt").waitFor({ timeout: 30000 });
  text = await page.getByRole("region", { name: "How this comparison ended" }).innerText();
  check("W3.5: 'Complete — this comparison recovered the earlier attempt'", text.includes("Complete — this comparison recovered the earlier attempt"));
  await page.getByRole("region", { name: "How this comparison ended" }).locator("summary", { hasText: "Diagnostics" }).click();
  text = await page.getByRole("region", { name: "How this comparison ended" }).innerText();
  check("W3.5: 221 differences", text.includes("Objects that differed: 221"), text.slice(0, 400));

  check("no application console errors (the favicon 404 is pre-existing and excluded)",
    consoleErrors.filter((e) => !e.includes("favicon.ico")).length === 0, consoleErrors.join(" | "));
  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  fs.writeFileSync(`${OUT}/docs-walkthrough.json`, JSON.stringify({ base: BASE, passed, total: results.length, results, console_errors: consoleErrors }, null, 2) + "\n");
  console.log(`${passed} of ${results.length} checks passed`);
  for (const r of results.filter((x) => !x.ok)) console.log("FAILED:", r.name, "—", r.detail);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
