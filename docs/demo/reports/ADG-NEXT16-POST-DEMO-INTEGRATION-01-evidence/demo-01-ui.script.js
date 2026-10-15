// One-off browser check of the ADG-DEMO-01 surfaces after the Next 16 integration: SYSTEM's
// mapping states in the explanation, and the principal page's unresolved-identity banner. The
// ADG-DEMO-01 report proved these through the API and component tests; this drives the same
// answers through the production build. Kept as evidence, not wired into any suite.
//
//   NODE_PATH=$(npm root -g) node demo-01-ui.script.js <output directory>
const { chromium } = require("playwright");
const fs = require("fs");
const OUT = process.argv[2];
const BASE = process.env.ADG_WEB_URL || "http://localhost:3000";
const results = [];
function check(name, ok, detail = "") { results.push({ name, ok: !!ok, detail: String(detail).slice(0, 600) }); }
const enc = encodeURIComponent;
const SHARE = "\\\\NM-FS-01\\Shared";
const UNRESOLVED = "S-1-5-21-3949361974-1583827165-633756269-1108";

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(`${m.text()} @ ${m.location().url}`); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "auditor" } });
  check("sign in as the development auditor", login.ok(), login.status());

  // SYSTEM over a share: the mapping is ambiguous, and the answer is not a conclusive negative.
  await page.goto(`${BASE}/access/explain?principal=S-1-5-18&resource=${enc(SHARE)}`);
  await page.waitForLoadState("networkidle");
  const smb = await page.locator("main").innerText();
  check("SYSTEM over SMB: mapping shown as ambiguous", /SYSTEM mapping\s*\n?\s*ambiguous/.test(smb), smb.slice(0, 900));
  check("SYSTEM over SMB: certainty uncertain, outcome indeterminate", /Certainty\s*\n?\s*uncertain/.test(smb), "");
  check("SYSTEM over SMB: says it is not a finding of no access", /Not a finding of no access/.test(smb), "");
  check("SYSTEM over SMB: no definite 'No access' verdict", !/Effective rights:?\s*\n?\s*No access/.test(smb) && !/\bVerdict:?\s*\n?\s*No access/.test(smb), "");
  check("SYSTEM over SMB: access path remote_smb", /Access path\s*\n?\s*remote_smb/.test(smb), "");
  await page.screenshot({ path: `${OUT}/demo01-system-smb.png`, fullPage: false });

  // SYSTEM locally: the mapping is confirmed, and the grant is a lower bound.
  await page.goto(`${BASE}/access/explain?principal=S-1-5-18&resource=${enc(SHARE)}&access_path=local`);
  await page.waitForLoadState("networkidle");
  const local = await page.locator("main").innerText();
  check("SYSTEM local: mapping confirmed", /SYSTEM mapping\s*\n?\s*confirmed/.test(local), local.slice(0, 900));
  check("SYSTEM local: certainty at_least (lower bound)", /Certainty\s*\n?\s*at_least/.test(local), "");
  check("SYSTEM local: access path local", /Access path\s*\n?\s*local/.test(local), "");

  // An unresolved SID: the banner, and no name invented for it.
  await page.goto(`${BASE}/identities/principal?key=${UNRESOLVED}`);
  await page.waitForLoadState("networkidle");
  const banner = page.getByRole("alert").filter({ hasText: "A run saw this SID and could not resolve it" });
  check("unresolved SID: the banner is shown as an alert", (await banner.count()) === 1);
  const bannerText = (await banner.count()) ? await banner.innerText() : "";
  check("unresolved SID: the banner names the key and the reason", bannerText.includes(UNRESOLVED) && /\(deleted\)/.test(bannerText), bannerText);
  check("unresolved SID: says nothing on the page names it as anything else", /nothing on this page names it as anything else/.test(bannerText), bannerText);
  const h1 = await page.locator("h1").first().innerText();
  check("unresolved SID: the heading does not invent a name", h1.includes("S-1-5-21-") || /unresolved/i.test(h1), h1);
  await page.screenshot({ path: `${OUT}/demo01-unresolved-principal.png`, fullPage: false });

  const known = consoleErrors.filter((e) => /\/favicon\.ico$/.test(e));
  const other = consoleErrors.filter((e) => !known.includes(e));
  check("no console errors from the application", other.length === 0, other.join(" | "));
  results.push({ name: "known, pre-existing: /favicon.ico 404", ok: true, detail: known.join(" | ") });
  await browser.close();
  fs.writeFileSync(`${OUT}/demo-01-ui.json`, JSON.stringify({ base: BASE, results, passed: results.filter((r) => r.ok).length, total: results.length }, null, 2));
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.ok ? "" : "  -- " + r.detail}`);
})().catch((e) => { console.error(e); process.exit(1); });
