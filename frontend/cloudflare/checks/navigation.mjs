#!/usr/bin/env node
/**
 * Browser gate for object navigation (ADG-UI-UPDATE-01): the Servers, Users and Groups
 * directories, shortest chains as links, sortable tables, relationship views and the
 * Identity information disclosure, with every move checked for a full document reload.
 *
 *   STAGING_URL=… node cloudflare/checks/navigation.mjs
 *
 * Runs against the hosted demo or a local `next start`; needs `playwright` as browser.mjs
 * does. Reads the Genesee Press dataset as SIGN_IN_AS (default `viewer`) and writes nothing.
 * A reload is detected two ways: a main-frame document request, and a marker set on
 * `window` that only survives a client-side navigation.
 */

import { createRequire } from "node:module";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const BASE = (process.env.STAGING_URL ?? "").replace(/\/+$/, "");
if (!BASE) {
  console.error("Set STAGING_URL.");
  process.exit(2);
}
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "viewer";
const TIMEOUT = Number(process.env.CHECK_TIMEOUT_MS ?? 90_000);

// Genesee Press (docs/demo/genesee-press.md): scenario G's six-level chain ends at Bianca.
const BIANCA = { name: "Bianca Rossi", sid: "S-1-5-21-3949361974-1583827165-633756269-1133" };
const DEEP_GROUP = "sec-okta-slack-user";
const CHAIN_GROUP = "GG_Sales";

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(TIMEOUT);
const page = await context.newPage();

const pageErrors = [];
const consoleErrors = [];
const documents = [];
const flights = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("request", (request) => {
  if (request.frame() === page.mainFrame() && request.resourceType() === "document") {
    documents.push(request.url());
  }
  if (request.headers()["rsc"] === "1") {
    flights.push({ url: request.url(), prefetch: request.headers()["next-router-prefetch"] === "1" });
  }
});

const results = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    results.push({ name, ok: false, detail: String(error?.message ?? error).split("\n")[0] });
    console.log(`FAIL  ${name} — ${String(error?.message ?? error).split("\n")[0]}`);
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}

/** Mark the current document; a reload loses the mark. */
async function mark() {
  documents.length = 0;
  flights.length = 0;
  await page.evaluate(() => {
    window.__adgNoReload = true;
  });
}
/** Assert the move since `mark()` stayed in the same document. */
async function noReload(what) {
  const kept = await page.evaluate(() => window.__adgNoReload === true);
  expect(kept && documents.length === 0, `${what}: the document reloaded (${documents.join(", ")})`);
}
/** Click, then wait for the URL to change and the page to settle. */
async function navigate(locator, urlPart) {
  const before = page.url();
  await locator.click();
  await page.waitForURL((url) => url.href !== before && url.href.includes(urlPart));
  await page.waitForLoadState("networkidle");
}
const path = () => new URL(page.url()).pathname + new URL(page.url()).search;
const h1 = () => page.locator("main h1, h1").first().innerText();
const disclosure = () => page.locator("details:has(summary h2:text-is('Identity information'))");
const bodyRows = (table) => table.locator("tbody tr");
/** The directory's own search form (the shell has a global search box too). */
async function search(term) {
  const form = page.locator("form:has(#directory-search)");
  await form.locator("#directory-search").fill(term);
  await form.getByRole("button", { name: "Search" }).click();
}

await check(`sign in as ${SIGN_IN_AS}`, async () => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: new RegExp(`Sign in as ${SIGN_IN_AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
});

await check("Servers: the directory strip, and a numeric sort on Shares", async () => {
  await page.goto(`${BASE}/resources`, { waitUntil: "networkidle" });
  const strip = page.getByRole("navigation", { name: "Directories" });
  expect((await strip.getByRole("link", { name: "Servers" }).getAttribute("aria-current")) === "page", "Servers not current");
  const table = page.locator("table").first();
  await mark();
  const header = table.getByRole("columnheader", { name: /^Shares/ });
  await header.getByRole("button").click();
  await header.getByRole("button").click();
  await noReload("sorting");
  expect((await header.getAttribute("aria-sort")) === "descending", "aria-sort is not descending");
  const counts = (await table.locator("tbody tr td:nth-child(5)").allInnerTexts()).map(Number);
  expect(counts.every((n, i) => i === 0 || counts[i - 1] >= n), `not descending numerically: ${counts}`);
  return `${counts.length} servers, shares ${counts.join(",")}`;
});

await check("Servers → Users without a reload", async () => {
  await mark();
  await navigate(page.getByRole("navigation", { name: "Directories" }).getByRole("link", { name: "Users" }), "/identities/users");
  await noReload("Servers → Users");
  const rows = await bodyRows(page.locator("table").first()).count();
  expect(rows > 0 && rows <= 50, `${rows} rows: the directory must be one server page`);
  const pager = await page.getByRole("navigation", { name: "users pages" }).innerText();
  return `${rows} rows; ${pager.replace(/\s+/g, " ").trim()}`;
});

await check("Users: search by name", async () => {
  await mark();
  await search("Bianca");
  await page.waitForURL((url) => url.search.includes("q=Bianca"));
  await page.waitForLoadState("networkidle");
  await noReload("name search");
  const names = await page.locator("table tbody th").allInnerTexts();
  expect(names.some((name) => name.includes(BIANCA.name)), `no ${BIANCA.name} in ${names}`);
  return `${names.length} match(es)`;
});

await check("Users: search by SID", async () => {
  await mark();
  await search(BIANCA.sid);
  await page.waitForURL((url) => url.search.includes(encodeURIComponent(BIANCA.sid)) || url.search.includes(BIANCA.sid));
  await page.waitForLoadState("networkidle");
  await noReload("SID search");
  const names = await page.locator("table tbody th").allInnerTexts();
  expect(names.length === 1 && names[0].includes(BIANCA.name), `SID search returned ${names}`);
});

await check("Users: sort by Direct groups, numerically, both ways", async () => {
  await page.goto(`${BASE}/identities/users?q=B`, { waitUntil: "networkidle" });
  const table = page.locator("table").first();
  const header = table.getByRole("columnheader", { name: /^Direct groups/ });
  await mark();
  await header.getByRole("button").click();
  const up = (await table.locator("tbody tr td:nth-child(6)").allInnerTexts()).map(Number);
  await header.getByRole("button").click();
  const down = (await table.locator("tbody tr td:nth-child(6)").allInnerTexts()).map(Number);
  await noReload("sorting");
  expect(up.every((n, i) => i === 0 || up[i - 1] <= n), `ascending wrong: ${up}`);
  expect(down.every((n, i) => i === 0 || down[i - 1] >= n), `descending wrong: ${down}`);
  return `${up.length} rows`;
});

await check("Users: selecting a user opens that user, by SID, without a reload", async () => {
  await page.goto(`${BASE}/identities/users?q=${encodeURIComponent(BIANCA.sid)}`, { waitUntil: "networkidle" });
  await mark();
  await navigate(page.getByRole("link", { name: BIANCA.name }), "/identities/principal/");
  await noReload("select user");
  expect(path() === `/identities/principal/${BIANCA.sid}`, `opened ${path()}`);
  expect((await h1()).includes(BIANCA.name), `heading ${await h1()}`);
});

const VIEWS = ["Effective groups", "Reachable shares", "Reachable directories", "Named on share ACLs", "Direct groups"];

await check("Identity information collapses, stays collapsed across every view, and expands", async () => {
  const details = disclosure();
  expect(await details.evaluate((node) => node.open), "Identity information did not start open");
  await details.locator("summary").click();
  expect(!(await details.evaluate((node) => node.open)), "did not collapse");
  await mark();
  const seen = [];
  for (const view of VIEWS) {
    await navigate(page.getByRole("navigation", { name: "Identity sections" }).getByRole("link", { name: view }), "/");
    await noReload(view);
    expect(!(await disclosure().evaluate((node) => node.open)), `re-opened after ${view}`);
    const current = await page.getByRole("navigation", { name: "Identity sections" }).locator("[aria-current=page]").innerText();
    expect(current === view, `${view}: tab marked ${current}`);
    seen.push(path().split("/").pop());
  }
  await disclosure().locator("summary").click();
  expect(await disclosure().evaluate((node) => node.open), "did not expand again");
  return seen.join(", ");
});

await check("Identity information is keyboard-operable", async () => {
  const summary = disclosure().locator("summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  expect(!(await disclosure().evaluate((node) => node.open)), "Enter did not collapse");
  await page.keyboard.press("Space");
  expect(await disclosure().evaluate((node) => node.open), "Space did not expand");
});

await check("one view switch is one request, and no chain link prefetches", async () => {
  await page.goto(`${BASE}/identities/principal/${BIANCA.sid}/groups`, { waitUntil: "networkidle" });
  await mark();
  await navigate(page.getByRole("link", { name: "Effective groups" }), "/effective-groups");
  await page.waitForTimeout(1500);
  const navigations = flights.filter((flight) => !flight.prefetch);
  // The view tabs may prefetch their own views (an unrendered route tree); nothing else may.
  const tabs = await page
    .getByRole("navigation", { name: "Identity sections" })
    .getByRole("link")
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  const stray = flights.filter((flight) => flight.prefetch && !tabs.includes(new URL(flight.url).pathname));
  expect(navigations.length === 1, `${navigations.length} navigation requests: ${navigations.map((f) => f.url).join(" ")}`);
  expect(stray.length === 0, `${stray.length} prefetches beyond the tabs: ${stray.map((f) => f.url).join(" ")}`);
  const chainLinks = await page.locator(".principal-chain a").count();
  return `1 navigation; ${flights.length - 1} tab prefetch(es); ${chainLinks} chain links, none prefetched`;
});

await check("Shortest chains: names, not SIDs, in order", async () => {
  const row = page.locator("tbody tr", { has: page.locator("th", { hasText: DEEP_GROUP }) });
  const chain = (await row.locator(".principal-chain").innerText()).replace(/\s+/g, " ");
  expect(!/S-1-5-21-/.test(chain), `a SID is the label in: ${chain}`);
  expect(chain.startsWith(BIANCA.name) && chain.endsWith(DEEP_GROUP), `chain ${chain}`);
  expect(chain.split("→").length === 7, `expected 7 hops: ${chain}`);
  return chain;
});

await check("Shortest chains: a group node opens that group; Back and Forward work", async () => {
  const row = page.locator("tbody tr", { has: page.locator("th", { hasText: DEEP_GROUP }) });
  const node = row.locator(".principal-chain a", { hasText: CHAIN_GROUP });
  const href = await node.getAttribute("href");
  expect(/^\/identities\/principal\/S-1-5-21-/.test(href ?? ""), `link is not by SID: ${href}`);
  const from = path();
  await mark();
  await navigate(node, href);
  await noReload("chain group node");
  expect((await h1()).includes(CHAIN_GROUP), `opened ${await h1()}`);
  expect(await disclosure().evaluate((n) => n.open), "a new principal should start open");
  await page.goBack();
  await page.waitForURL((url) => url.pathname + url.search === from);
  await page.waitForLoadState("networkidle");
  await noReload("Back");
  expect((await h1()).includes(BIANCA.name), `Back landed on ${await h1()}`);
  await page.goForward();
  await page.waitForURL((url) => url.pathname === href);
  await noReload("Forward");
  expect((await h1()).includes(CHAIN_GROUP), `Forward landed on ${await h1()}`);
  return href;
});

await check("Shortest chains: a user node opens that user", async () => {
  await mark();
  await navigate(page.getByRole("link", { name: "Effective members" }), "/effective-members");
  const node = page.locator(".principal-chain a", { hasText: BIANCA.name }).first();
  const href = await node.getAttribute("href");
  await navigate(node, href);
  await noReload("chain user node");
  expect(path() === `/identities/principal/${BIANCA.sid}`, `opened ${path()}`);
  await page.goBack();
  await page.waitForURL((url) => url.pathname.endsWith("/effective-members"));
  await noReload("Back");
  return href;
});

await check("Groups: search, sort by members, open a group, switch its views", async () => {
  await mark();
  await navigate(page.getByRole("link", { name: "Identities" }), "/identities");
  await navigate(page.getByRole("link", { name: "Groups" }).first(), "/identities/groups");
  await search("sec-share-design");
  await page.waitForURL((url) => url.search.includes("q=sec-share-design"));
  await page.waitForLoadState("networkidle");
  const table = page.locator("table").first();
  const header = table.getByRole("columnheader", { name: /^Direct members/ });
  await header.getByRole("button").click();
  await header.getByRole("button").click();
  const first = await table.locator("tbody tr th").first().innerText();
  expect(first.includes("sec-share-design-write"), `largest group first? ${first}`);
  await navigate(table.locator("tbody tr th a").first(), "/identities/principal/");
  for (const view of ["Effective members", "Direct members", "Parent groups"]) {
    await navigate(page.getByRole("navigation", { name: "Identity sections" }).getByRole("link", { name: view }), "/");
  }
  await noReload("groups directory and views");
  return path();
});

await check("Groups: Exclude local groups filters on the server, keeps search and sort, no reload", async () => {
  await navigate(page.getByRole("link", { name: "Identities" }), "/identities");
  await navigate(page.getByRole("link", { name: "Groups" }).first(), "/identities/groups");
  await mark();
  const table = page.locator("table").first();
  const column = async (index) => bodyRows(table).locator(`td:nth-of-type(${index})`).allInnerTexts();
  const toggle = page.getByRole("checkbox", { name: "Exclude local groups" });
  const settled = async (test) => {
    await page.waitForURL((url) => test(url.searchParams));
    await page.waitForLoadState("networkidle");
  };
  expect(!(await toggle.isChecked()), "on by default");

  await search("Finance");
  await settled((p) => p.get("q") === "Finance");
  const header = table.getByRole("columnheader", { name: /^Direct members/ });
  await header.getByRole("button").click();
  await header.getByRole("button").click();
  const before = await bodyRows(table).count();

  await toggle.focus();
  await page.keyboard.press("Space");
  await settled((p) => p.get("exclude_local") === "1" && p.get("q") === "Finance" && !p.has("p"));
  expect(await toggle.isChecked(), "not checked after Space");
  const kinds = await column(2);
  expect(!kinds.includes("local group"), `a local group is listed: ${kinds.join(", ")}`);
  expect((await column(3)).includes("domain local"), "the Domain Local groups were dropped");
  expect((await bodyRows(table).count()) === before, `Finance groups ${before} → ${await bodyRows(table).count()}`);
  expect((await header.getAttribute("aria-sort")) === "descending", "the sort was lost");

  await search("Administrators");
  await settled((p) => p.get("q") === "Administrators" && p.get("exclude_local") === "1");
  expect((await page.locator("main").innerText()).includes('matching "Administrators"'), "local groups still listed");

  await toggle.click();
  await settled((p) => p.get("q") === "Administrators" && !p.has("exclude_local"));
  const locals = await column(2);
  expect(locals.length > 0 && locals.every((kind) => kind === "local group"), `kinds ${locals.join(", ")}`);
  await noReload("groups filter");
  return `${locals.length} local Administrators groups; ${before} Finance groups kept`;
});

await check("Groups → Servers without a reload", async () => {
  await mark();
  await navigate(page.getByRole("link", { name: "Resources" }), "/resources");
  await noReload("back to Servers");
});

await check("a deep link survives a fresh load", async () => {
  const response = await page.goto(`${BASE}/identities/principal/${BIANCA.sid}/effective-groups`, { waitUntil: "networkidle" });
  expect(response?.status() === 200, `HTTP ${response?.status()}`);
  const current = await page.getByRole("navigation", { name: "Identity sections" }).locator("[aria-current=page]").innerText();
  expect(current === "Effective groups", `tab ${current}`);
  expect((await page.locator(".principal-chain").count()) > 0, "no chains rendered");
});

await check("the old ?key= address redirects to the key's path", async () => {
  await page.goto(`${BASE}/identities/principal?key=${BIANCA.sid}&tab=shares`, { waitUntil: "networkidle" });
  expect(path() === `/identities/principal/${BIANCA.sid}/shares`, `landed on ${path()}`);
});

await check("no page errors", async () => {
  expect(pageErrors.length === 0, `${pageErrors.length}: ${pageErrors.slice(0, 2).join(" | ")}`);
  return `${consoleErrors.length} console error(s)`;
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(
  JSON.stringify({ base: BASE, passed: results.length - failed.length, failed: failed.length, consoleErrors, pageErrors, results }, null, 2),
);
process.exit(failed.length === 0 ? 0 : 1);
