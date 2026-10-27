// ADG-UI-STAGING-PROMOTE-01: hosted Users/Groups directory and Exclude-local interaction,
// beyond what navigation.mjs covers. Read-only, signed in as viewer.
//   NODE_PATH=… CHROMIUM_PATH=… STAGING_URL=… node directory-ui.mjs out.json
import fs from "node:fs";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.STAGING_URL.replace(/\/+$/, "");
const OUT = process.argv[2];
const BIANCA = "S-1-5-21-3949361974-1583827165-633756269-1133";
const UNRESOLVED = "S-1-5-21-3949361974-1583827165-633756269-1108";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${String(detail).slice(0, 180)}` : ""}`);
};

// Expected totals from the API itself, not hard-coded.
const tokenResponse = await fetch(`${BASE}/auth/dev/login`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "viewer" }),
});
const token = (await tokenResponse.json()).access_token;
const total = async (query) => {
  const r = await fetch(`${BASE}/api/v1/principals?${new URLSearchParams({ limit: "1", ...query })}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).page.total;
};
const expected = {
  users: await total({ kind: "users" }),
  groups: await total({ kind: "groups" }),
  groupsDomain: await total({ kind: "groups", exclude_local: "true" }),
};

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const errors = [];
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(90_000);
const page = await context.newPage();
const documents = [];
page.on("console", (m) => { if (m.type() === "error" && !/favicon\.ico/.test(m.location().url)) errors.push(`${m.text()} @ ${m.location().url}`); });
page.on("pageerror", (e) => errors.push(e.message));
page.on("request", (r) => { if (r.frame() === page.mainFrame() && r.resourceType() === "document") documents.push(r.url()); });
const login = await page.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "viewer" } });
check("sign in as viewer", login.ok(), login.status());

const pagerText = () => page.locator("nav.pager p.muted").first().innerText();
const mark = () => page.evaluate(() => { window.__noReload = true; });
const marked = () => page.evaluate(() => window.__noReload === true);
const settled = (pattern) => page.waitForURL(pattern).then(() => page.waitForLoadState("networkidle"));
const firstRowKey = () => page.locator("table tbody tr").first().innerText();
const checkbox = () => page.getByLabel("Exclude local groups");

// ---- Users directory: total, pagination, the three search forms
await page.goto(`${BASE}/identities/users`, { waitUntil: "networkidle" });
let text = await pagerText();
check("Users: renders with the exact total", text.includes(`${expected.users} users in total`), text);
const page1 = await firstRowKey();
await mark();
let docs = documents.length;
await page.getByRole("link", { name: "Next", exact: true }).click();
await settled(/[?&]p=/);
text = await pagerText();
check("Users: Next is a client-side page 2", (await marked()) && documents.length === docs && text.startsWith("Page 2.") && (await firstRowKey()) !== page1, text);
await page.getByRole("link", { name: "Previous", exact: true }).click();
await page.waitForFunction(() => !/[?&]p=/.test(location.search));
await page.waitForLoadState("networkidle");
check("Users: Previous returns to page 1 without a reload", (await marked()) && documents.length === docs && (await pagerText()).startsWith("Page 1.") && (await firstRowKey()) === page1);

async function search(term) {
  await page.locator("#directory-search").fill(term);
  await page.locator("#directory-search-form").getByRole("button", { name: "Search" }).click();
  await settled((url) => url.searchParams.get("q") === term);
  return page.locator("table tbody").innerText();
}
docs = documents.length;
let rows = await search("Bianca");
check("Users: name prefix search (client-side)", rows.includes("Bianca Rossi") && documents.length === docs && (await marked()), (await pagerText()));
rows = await search(BIANCA.slice(0, -2));
check("Users: SID prefix search", rows.includes("Bianca Rossi"), await pagerText());
const sam = await page.locator("table tbody tr", { hasText: "Bianca Rossi" }).locator(":scope > :nth-child(2)").innerText();
rows = await search(`NULLMERIDIAN\\${sam.trim()}`);
check("Users: DOMAIN\\name search", rows.includes("Bianca Rossi"), `NULLMERIDIAN\\${sam.trim()} — ${await pagerText()}`);
const href = await page.locator("table tbody tr", { hasText: "Bianca Rossi" }).getByRole("link").first().getAttribute("href");
check("Users: the name links to the canonical storage-key route", href === `/identities/principal/${BIANCA}`, href);

// ---- Groups: Exclude local groups
await page.goto(`${BASE}/identities/groups`, { waitUntil: "networkidle" });
await mark();
docs = documents.length;
check("Groups: exact total before filtering", (await pagerText()).includes(`${expected.groups} groups in total`), await pagerText());
check("Groups: Exclude local groups is off by default", !(await checkbox().isChecked()));
// Page 2 first, so the filter's page reset is visible.
await page.getByRole("link", { name: "Next", exact: true }).click();
await settled(/[?&]p=/);
await checkbox().check();
await settled((url) => url.searchParams.get("exclude_local") === "1");
let url = new URL(page.url());
text = await pagerText();
const kinds = await page.locator("table tbody tr td:nth-child(3)").allInnerTexts();
check("Groups: turning it on is a client-side navigation", (await marked()) && documents.length === docs);
check("Groups: the URL records the state, and the page resets to 1", url.searchParams.get("exclude_local") === "1" && !url.searchParams.has("p") && text.startsWith("Page 1."), url.search);
check("Groups: local groups are gone (exact filtered total, no local kind on the page)", text.includes(`${expected.groupsDomain} groups in total`) && kinds.every((k) => !/local group/i.test(k)), text);

// Domain Local groups stay: search one by name with the filter on.
await page.goto(`${BASE}/identities/groups?q=DL_&exclude_local=1`, { waitUntil: "networkidle" });
let scopes = await page.locator("table tbody tr td:nth-child(4)").allInnerTexts();
if (!scopes.some((s) => /domain local/i.test(s))) {
  await page.goto(`${BASE}/identities/groups?exclude_local=1`, { waitUntil: "networkidle" });
  scopes = await page.locator("table tbody tr td:nth-child(4)").allInnerTexts();
}
check("Groups: Domain Local groups remain with the filter on", scopes.some((s) => /domain local/i.test(s)), [...new Set(scopes)].join(", "));

// Search survives the toggle; Back and Forward restore both states.
await page.goto(`${BASE}/identities/groups?q=A`, { waitUntil: "networkidle" });
await mark();
docs = documents.length;
const before = await pagerText();
await checkbox().check();
await settled((u) => u.searchParams.get("exclude_local") === "1");
url = new URL(page.url());
const after = await pagerText();
check("Groups: the search term survives (URL and box)", url.searchParams.get("q") === "A" && (await page.locator("#directory-search").inputValue()) === "A" && before !== after, `${before} → ${after}`);
await page.goBack();
await page.waitForFunction(() => !location.search.includes("exclude_local"));
await page.waitForLoadState("networkidle");
check("Groups: Back restores the unfiltered state (URL, checkbox, total)", !(await checkbox().isChecked()) && (await pagerText()) === before && (await marked()), await pagerText());
await page.goForward();
await page.waitForFunction(() => location.search.includes("exclude_local=1"));
await page.waitForLoadState("networkidle");
check("Groups: Forward restores the filtered state", (await checkbox().isChecked()) && (await pagerText()) === after && (await marked()), await pagerText());

// Sorting still works on the filtered page, without navigating.
const at = page.url();
docs = documents.length;
const header = page.getByRole("columnheader", { name: /Direct members/ });
await header.getByRole("button").click();
const sort1 = await header.getAttribute("aria-sort");
const col = (await page.locator("table tbody tr td:nth-child(6)").allInnerTexts()).map((s) => Number(s.replace(/\D/g, "")));
const sortedUp = col.every((v, i) => i === 0 || col[i - 1] <= v);
const sortedDown = col.every((v, i) => i === 0 || col[i - 1] >= v);
check("Groups: client-side sorting on the filtered page", page.url() === at && documents.length === docs && (sortedUp || sortedDown) && /ascending|descending/.test(sort1 ?? ""), `${sort1}: ${col.slice(0, 8).join(",")}`);
check("Groups: the whole sequence made no full document reload", await marked());

// ---- 390 px: no horizontal overflow on the main surfaces; DOMAIN\name hint unbroken
const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
const p2 = await phone.newPage();
p2.on("console", (m) => { if (m.type() === "error" && !/favicon\.ico/.test(m.location().url)) errors.push(`phone: ${m.text()} @ ${m.location().url}`); });
p2.on("pageerror", (e) => errors.push(`phone: ${e.message}`));
await p2.request.post(`${BASE}/api/auth/dev-login`, { data: { username: "viewer" } });
const overflow = {};
for (const path of ["/identities/users", "/identities/groups?exclude_local=1", `/identities/principal/${BIANCA}`,
  `/identities/principal/${BIANCA}/effective-groups`, `/identities/principal/${UNRESOLVED}`, "/risks",
  `/access/explain?principal=S-1-5-21-3949361974-1583827165-633756269-1323&resource=${encodeURIComponent("\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo")}`]) {
  await p2.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  overflow[path] = await p2.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}
check("390 px: no horizontal page overflow on 7 surfaces", Object.values(overflow).every((v) => v <= 0), JSON.stringify(overflow));
await p2.goto(`${BASE}/identities/users`, { waitUntil: "networkidle" });
const hint = await p2.locator("p.muted code.nowrap", { hasText: "DOMAIN\\name" }).evaluate((el) => ({
  ws: getComputedStyle(el).whiteSpace, rects: el.getClientRects().length,
}));
check("390 px: DOMAIN\\name stays on one line", hint.ws === "nowrap" && hint.rects === 1, JSON.stringify(hint));
await p2.goto(`${BASE}/identities/principal/${UNRESOLVED}`, { waitUntil: "networkidle" });
const unresolvedText = await p2.locator("main").innerText();
check("390 px: the unresolved identity still exposes its SID", unresolvedText.includes(UNRESOLVED));
await p2.screenshot({ path: OUT.replace(/\.json$/, "-unresolved-phone.png") });

check("no application console or page errors", errors.length === 0, errors.join(" | "));
await browser.close();
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} passed`);
fs.writeFileSync(OUT, JSON.stringify({ base: BASE, at: new Date().toISOString(), expected, passed, total: results.length, results }, null, 1));
process.exit(passed === results.length ? 0 : 1);
