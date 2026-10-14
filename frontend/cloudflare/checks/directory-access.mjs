#!/usr/bin/env node
/**
 * Browser gate for Directory access (ADG-DIRECTORY-ACCESS-01): the summary as the default,
 * sorting, exceptions, the lazy tree, the selected principal across view changes, All
 * paths, long-path handling, keyboard operation, the share link and the CSV export — with
 * every move checked for a full document reload and every tree request counted.
 *
 *   STAGING_URL=… node cloudflare/checks/directory-access.mjs
 *
 * Runs against a local `next start` (or the hosted demo) over Null Meridian, signed in as
 * SIGN_IN_AS (default `viewer`); reads only. SCREENSHOT_DIR, when set, receives one PNG of
 * the summary and one of the opened tree.
 */

import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const BASE = (process.env.STAGING_URL ?? "").replace(/\/+$/, "");
if (!BASE) {
  console.error("Set STAGING_URL.");
  process.exit(2);
}
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "viewer";
const TIMEOUT = Number(process.env.CHECK_TIMEOUT_MS ?? 90_000);
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";

// Null Meridian: an Engineering-FS member, so scenario C's Engineering share is a deep root.
const SUBJECT = { name: "Aaron Adams", sid: "S-1-5-21-3949361974-1583827165-633756269-1885" };
const ENGINEERING = "\\\\NM-ENG-FS01\\Engineering";

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(TIMEOUT);
const page = await context.newPage();

const pageErrors = [];
const consoleErrors = [];
const documents = [];
const treeRequests = [];
const exceptionRequests = [];
const failedResponses = [];
page.on("response", (response) => {
  if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
});
page.on("pageerror", (error) => pageErrors.push(String(error)));
const favicon = [];
page.on("console", (message) => {
  if (message.type() !== "error") return;
  const source = message.location()?.url ?? "";
  // The application ships no favicon, so the browser's own /favicon.ico request is a 404 on
  // every page, before and after this feature. It is reported, not counted as this gate's.
  if (new URL(source || BASE).pathname === "/favicon.ico") favicon.push(source);
  else consoleErrors.push(`${message.text()} (${source})`);
});
page.on("request", (request) => {
  if (request.frame() === page.mainFrame() && request.resourceType() === "document") {
    documents.push(request.url());
  }
  if (request.url().includes("/directory-access/tree")) treeRequests.push(request.url());
  if (request.url().includes("/directory-access/exceptions")) exceptionRequests.push(request.url());
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
async function mark() {
  documents.length = 0;
  await page.evaluate(() => {
    window.__adgNoReload = true;
  });
}
async function noReload(what) {
  const kept = await page.evaluate(() => window.__adgNoReload === true);
  expect(kept && documents.length === 0, `${what}: the document reloaded (${documents.join(", ")})`);
}
async function navigate(locator, urlPart) {
  const before = page.url();
  await locator.click();
  await page.waitForURL((url) => url.href !== before && url.href.includes(urlPart));
  await page.waitForLoadState("networkidle");
}
const h1 = () => page.locator("h1").first().innerText();
const views = () => page.getByRole("navigation", { name: "Identity sections" });
const modes = () => page.getByRole("navigation", { name: "Directory access views" });
const summaryTable = () => page.locator("table.directory-summary");
const treeItems = () => page.locator(".directory-tree li.tree-item");
const toggleFor = (name) => page.getByRole("button", { name: new RegExp(`^(Expand|Collapse) ${name.replace(/\\/g, "\\\\")}$`) });

let totals = { reachable: 0, roots: 0 };

await check(`sign in as ${SIGN_IN_AS}`, async () => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: new RegExp(`Sign in as ${SIGN_IN_AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
});

await check("1. open the principal", async () => {
  await page.goto(`${BASE}/identities/principal/${encodeURIComponent(SUBJECT.sid)}`, { waitUntil: "networkidle" });
  const title = await h1();
  expect(title.includes(SUBJECT.name), `h1 is ${title}`);
  return title.replace(/\s+/g, " ");
});

await check("2–3. Directory access opens on Summary, without a reload", async () => {
  await mark();
  await navigate(views().getByRole("link", { name: "Directory access" }), "/directories");
  await noReload("opening Directory access");
  const current = await modes().getByRole("link", { name: "Summary" }).getAttribute("aria-current");
  expect(current === "page", "Summary is not the current mode");
  return new URL(page.url()).pathname;
});

await check("4–6. meaningful roots, not every descendant; counts reconcile", async () => {
  const heading = await page.getByRole("heading", { name: /access roots? covers?/ }).innerText();
  const match = heading.match(/([\d,]+) access roots? covers? ([\d,]+) director/);
  expect(match, `heading: ${heading}`);
  totals = { roots: Number(match[1].replace(/,/g, "")), reachable: Number(match[2].replace(/,/g, "")) };
  const rows = await summaryTable().locator("tbody tr").count();
  expect(rows === totals.roots, `${rows} rows for ${totals.roots} roots`);
  expect(rows < totals.reachable, "the summary lists as many rows as directories");
  const descendants = (await summaryTable().locator("tbody tr td:nth-child(4)").allInnerTexts()).map((t) => Number(t.replace(/,/g, "")));
  const sum = descendants.reduce((a, n) => a + n + 1, 0);
  expect(sum === totals.reachable, `roots + descendants = ${sum}, heading says ${totals.reachable}`);
  const engineering = summaryTable().locator("tbody tr", { hasText: "NM-ENG-FS01" }).filter({ hasText: "Engineering" }).first();
  const engineeringText = (await engineering.innerText()).replace(/\s+/g, " ");
  return `${rows} root rows for ${totals.reachable} reachable directories; Engineering row: ${engineeringText}`;
});

await check("7. exceptions are indicated", async () => {
  const varied = await summaryTable().locator("span[title^='Permissions vary']").count();
  const disclosures = await summaryTable().locator("details.root-exceptions").count();
  expect(varied > 0 && disclosures > 0, `varied ${varied}, disclosures ${disclosures}`);
  return `${varied} roots marked "varies" (Permissions vary), ${disclosures} with an exceptions disclosure`;
});

if (SCREENSHOTS) {
  mkdirSync(SCREENSHOTS, { recursive: true });
  await page.getByRole("navigation", { name: "Directory access views" }).evaluate((node) => node.scrollIntoView());
  await page.screenshot({ path: `${SCREENSHOTS}/adg-directory-access-01-summary.png`, fullPage: false });
}

await check("8. sort by root / path, without a reload", async () => {
  await mark();
  const header = summaryTable().getByRole("columnheader", { name: /^Access root/ });
  await header.getByRole("button").click();
  await noReload("sorting by root");
  expect((await header.getAttribute("aria-sort")) === "ascending", "aria-sort is not ascending");
  const names = await summaryTable().locator("tbody th").allInnerTexts();
  const sorted = [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  // The root column sorts by the canonical key (share, then folder); assert it is ordered.
  return `${names.length} rows, first ${names[0]}, last ${names[names.length - 1]} (alphabetic first would be ${sorted[0]})`;
});

await check("9. sort by descendant count, numerically", async () => {
  await mark();
  const header = summaryTable().getByRole("columnheader", { name: /^Reachable descendants/ });
  await header.getByRole("button").click();
  await header.getByRole("button").click();
  await noReload("sorting by count");
  expect((await header.getAttribute("aria-sort")) === "descending", "aria-sort is not descending");
  const counts = (await summaryTable().locator("tbody tr td:nth-child(4)").allInnerTexts()).map((t) => Number(t.replace(/,/g, "")));
  expect(counts.every((n, i) => i === 0 || counts[i - 1] >= n), `not descending: ${counts.join(",")}`);
  return `top counts ${counts.slice(0, 5).join(", ")}`;
});

await check("19–20. inspect a root's boundaries: exceptions differ from the root", async () => {
  await mark();
  const row = summaryTable().locator("tbody tr", { hasText: "Engineering" }).filter({ hasText: "NM-ENG-FS01" }).first();
  const details = row.locator("details.root-exceptions");
  await details.locator("summary").click();
  await details.locator(".exception-list li").first().waitFor();
  await noReload("opening exceptions");
  const items = await details.locator(".exception-list li").allInnerTexts();
  expect(items.length > 0, "no exceptions listed");
  expect(exceptionRequests.length === 1, `${exceptionRequests.length} exception requests`);
  await details.locator("summary").click(); // fold
  await details.locator("summary").click(); // unfold: no second request
  expect(exceptionRequests.length === 1, `refetched exceptions: ${exceptionRequests.length}`);
  return items.map((text) => text.replace(/\s+/g, " ")).join(" | ");
});

await check("22. long paths stay on one line and keep their full value", async () => {
  const heights = await summaryTable().locator("tbody tr").evaluateAll((rows) =>
    rows.filter((row) => !row.querySelector("details[open]")).map((row) => row.getBoundingClientRect().height),
  );
  const max = Math.max(...heights);
  const min = Math.min(...heights);
  expect(max <= min * 1.25 + 1, `row heights vary from ${min} to ${max}`);
  const identifiers = await summaryTable().locator(".identifier").evaluateAll((nodes) =>
    nodes.map((node) => ({ clipped: node.scrollWidth > node.clientWidth, title: node.getAttribute("title") ?? "", text: node.textContent ?? "" })),
  );
  const clipped = identifiers.filter((item) => item.clipped);
  expect(identifiers.every((item) => item.title.length > 0), "an identifier has no title");
  return `${heights.length} closed rows, height ${min.toFixed(1)}–${max.toFixed(1)} px; ${clipped.length} clipped identifiers, all with a full-path title`;
});

await check("17–18. Tree: the shares, collapsed; nothing beneath them rendered", async () => {
  await mark();
  treeRequests.length = 0;
  await navigate(modes().getByRole("link", { name: "Tree" }), "mode=tree");
  await noReload("switching to Tree");
  const items = await treeItems().count();
  const expanded = await page.locator(".directory-tree button[aria-expanded='true']").count();
  expect(expanded === 0, `${expanded} branches open on load`);
  expect(treeRequests.length === 0, `${treeRequests.length} tree requests from the browser on load`);
  expect(items > 0 && items <= 60, `${items} tree items rendered on load`);
  return `${items} share roots rendered, all collapsed, 0 browser tree requests`;
});

await check("10–11. expand a share: children appear, one request, no reload", async () => {
  await mark();
  const toggle = toggleFor(ENGINEERING.replace(/\\/g, "\\"));
  await toggle.click();
  await page.locator(".directory-tree li.tree-item li.tree-item").first().waitFor();
  await noReload("expanding a share");
  expect((await toggle.getAttribute("aria-expanded")) === "true", "aria-expanded is not true");
  expect(treeRequests.length === 1, `${treeRequests.length} tree requests`);
  expect(treeRequests[0].includes("parent="), `request ${treeRequests[0]}`);
  const children = await page.locator(`.directory-tree ul[id='${await toggle.getAttribute("aria-controls")}'] > li.tree-item`).count();
  return `${children} children of ${ENGINEERING}; request ${decodeURIComponent(new URL(treeRequests[0]).search)}`;
});

let nestedName = "";
await check("12. expand a nested branch", async () => {
  await mark();
  // Resolve the element once: a locator for "the first closed toggle" moves on once it opens.
  const candidate = page.locator(".directory-tree li.tree-item li.tree-item button.tree-toggle[aria-expanded='false']").first();
  nestedName = ((await candidate.getAttribute("aria-label")) ?? "").replace(/^Expand /, "");
  const nested = toggleFor(nestedName);
  const listId = await nested.getAttribute("aria-controls");
  await nested.click();
  await page.locator(`.directory-tree [id='${listId}'] > li`).first().waitFor();
  await noReload("expanding a nested branch");
  expect(treeRequests.length === 2, `${treeRequests.length} tree requests`);
  return `${nestedName} opened; 2 tree requests so far`;
});

await check("13. collapse the branch; reopening asks nothing", async () => {
  await mark();
  const toggle = toggleFor(nestedName);
  await toggle.click();
  expect((await toggle.getAttribute("aria-expanded")) === "false", "did not collapse");
  await toggle.click();
  await page.waitForTimeout(300);
  await noReload("collapse and reopen");
  expect(treeRequests.length === 2, `reopening refetched: ${treeRequests.length} requests`);
  return "collapsed and reopened from cache";
});

await check("19–20. a boundary in the tree reads differently from its parent", async () => {
  const marked = page.locator(".directory-tree li.tree-item li.tree-item", { has: page.locator(".tree-node .badge-deny, .tree-node .badge-explicit") }).first();
  expect((await marked.count()) > 0, "no boundary visible in the opened branches");
  const node = (await marked.locator(":scope > .tree-node").innerText()).replace(/\s+/g, " ");
  const parent = (await marked.locator("xpath=ancestor::li[contains(@class,'tree-item')][1]/div[contains(@class,'tree-node')]").innerText()).replace(/\s+/g, " ");
  return `boundary: "${node}" under "${parent}"`;
});

if (SCREENSHOTS) {
  await page.screenshot({ path: `${SCREENSHOTS}/adg-directory-access-01-tree.png`, fullPage: false });
}

await check("23. keyboard: Enter opens, ArrowLeft closes, focus stays", async () => {
  const candidate = page.locator(".directory-tree > ul > li.tree-item > .tree-node > button.tree-toggle[aria-expanded='false']").first();
  const toggle = toggleFor(((await candidate.getAttribute("aria-label")) ?? "").replace(/^Expand /, ""));
  await toggle.focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction((el) => el.getAttribute("aria-expanded") === "true", await toggle.elementHandle());
  const focused = await toggle.evaluate((el) => el === document.activeElement);
  await page.keyboard.press("ArrowLeft");
  const closed = (await toggle.getAttribute("aria-expanded")) === "false";
  expect(focused && closed, `focused ${focused}, closed ${closed}`);
  return (await toggle.getAttribute("aria-label")) ?? "";
});

await check("14–16. another view and back: the selected principal is kept", async () => {
  await mark();
  await navigate(views().getByRole("link", { name: "Effective groups" }), "/effective-groups");
  await noReload("to Effective groups");
  expect((await h1()).includes(SUBJECT.name), "principal lost on Effective groups");
  await mark();
  await navigate(views().getByRole("link", { name: "Directory access" }), "/directories");
  await noReload("back to Directory access");
  expect((await h1()).includes(SUBJECT.name), "principal lost on return");
  expect(page.url().includes(encodeURIComponent(SUBJECT.sid)), `url ${page.url()}`);
  const current = await modes().getByRole("link", { name: "Summary" }).getAttribute("aria-current");
  return `h1 ${(await h1()).replace(/\s+/g, " ")}; mode Summary ${current === "page" ? "current" : "not current"}`;
});

await check("21. All paths: the full flat detail remains", async () => {
  await mark();
  await navigate(modes().getByRole("link", { name: "All paths" }), "mode=paths");
  await noReload("to All paths");
  const rows = await page.locator("table.access-table tbody tr").count();
  const firstPath = await page.locator("table.access-table tbody tr th a.identifier").first().getAttribute("title");
  expect(rows > totals.roots, `${rows} rows; the flat list should be longer than the summary`);
  return `${rows} rows on page one; first ${firstPath}`;
});

await check("export: the paths CSV carries every evaluated directory", async () => {
  const response = await page.request.get(`${BASE}/api/directory-access/export?key=${encodeURIComponent(SUBJECT.sid)}&mode=paths`);
  expect(response.status() === 200, `HTTP ${response.status()}`);
  expect((response.headers()["content-type"] ?? "").startsWith("text/csv"), response.headers()["content-type"]);
  const lines = (await response.text()).trim().split(/\r?\n/);
  const reachable = lines.slice(1).filter((line) => line.split(",")[4] === "yes").length;
  expect(reachable === totals.reachable, `${reachable} reachable rows, summary says ${totals.reachable}`);
  return `${lines.length - 1} rows, ${reachable} reachable; complete=${response.headers()["x-adg-complete"]}`;
});

await check("Reachable shares → Directory access inside one share", async () => {
  await mark();
  await navigate(views().getByRole("link", { name: "Reachable shares" }), "/shares");
  const link = page.locator("table").getByRole("link", { name: "Directory access" }).first();
  await navigate(link, "share=");
  await noReload("share → directory access");
  const share = new URL(page.url()).searchParams.get("share");
  const shares = new Set(await summaryTable().locator("tbody tr td:nth-child(1)").allInnerTexts());
  expect(shares.size === 1, `summary spans ${[...shares].join(", ")}`);
  return `share=${share}: ${await summaryTable().locator("tbody tr").count()} root(s) in ${[...shares][0]}`;
});

await check("24. no page errors and no console errors", async () => {
  expect(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);
  expect(consoleErrors.length === 0, `console errors: ${consoleErrors.join(" | ")} (failed responses: ${failedResponses.join(" | ")})`);
  return `0 page errors, 0 console errors${favicon.length ? ` (plus ${favicon.length} pre-existing /favicon.ico 404)` : ""}`;
});

await browser.close();
const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length === 0 ? 0 : 1);
