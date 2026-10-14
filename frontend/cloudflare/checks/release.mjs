#!/usr/bin/env node
/**
 * Release gate for the combined UI, Directory Access and edge build
 * (ADG-UI-EDGE-DIRECTORY-INTEGRATION-01). It covers what smoke.mjs, browser.mjs,
 * navigation.mjs and directory-access.mjs leave unmeasured: row density and SID presentation,
 * every relationship view of a group with Back and Forward, request counts per view switch,
 * the Exclude local groups semantics against the API, the export contract, the Identity
 * information state on a newly selected object, and a fresh document load of every route
 * family through the edge.
 *
 *   STAGING_URL=… node cloudflare/checks/release.mjs
 *
 * Runs against the hosted demo or a local Worker → gate chain; needs `playwright` as
 * browser.mjs does. Reads the Null Meridian dataset as SIGN_IN_AS (default `viewer`) and
 * writes nothing. A reload is detected as navigation.mjs detects it.
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

// Null Meridian (docs/demo/null-meridian.md).
const BIANCA = "S-1-5-21-3949361974-1583827165-633756269-1133";
const AARON = "S-1-5-21-3949361974-1583827165-633756269-1885";
const GROUP = "GG_Sales";
// A table row holding one line of text at the app's type scale (39.8 px measured); two lines
// would be about 60 px.
const ONE_LINE_ROW_PX = 44;

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
  if (request.headers()["rsc"] === "1" && request.headers()["next-router-prefetch"] !== "1") {
    flights.push(request.url());
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
async function mark() {
  documents.length = 0;
  flights.length = 0;
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
const path = () => new URL(page.url()).pathname + new URL(page.url()).search;
const h1 = () => page.locator("main h1, h1").first().innerText();
const disclosure = () => page.locator("details:has(summary h2:text-is('Identity information'))");
const tabs = () => page.getByRole("navigation", { name: "Identity sections" });
/** JSON from the web tier's API proxy, with the browser's session. */
async function api(apiPath) {
  return page.evaluate(async (url) => {
    const response = await fetch(url);
    return { status: response.status, body: response.ok ? await response.json() : null };
  }, `/api/adg${apiPath}`);
}
/** Every page of a listing, following the cursor. */
async function allPrincipals(query) {
  const items = [];
  let cursor = null;
  do {
    const answer = await api(`/api/v1/principals?${query}&limit=500${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    expect(answer.status === 200, `GET principals?${query}: ${answer.status}`);
    items.push(...answer.body.items);
    cursor = answer.body.page.next_cursor;
  } while (cursor);
  return items;
}
/** Row heights and SID cell presentation of the first table on the page. */
function measureDirectory() {
  return page.locator("table").first().evaluate((table) => {
    const rows = [...table.querySelectorAll("tbody tr")];
    const sids = [...table.querySelectorAll("tbody .identifier.sid")];
    return {
      rows: rows.length,
      maxHeight: Math.max(...rows.map((row) => row.getBoundingClientRect().height)),
      namesWithSid: rows.filter((row) => /S-1-\d/.test(row.querySelector("th")?.innerText ?? "")).length,
      sids: sids.length,
      sidsBad: sids.filter((cell) => {
        const style = getComputedStyle(cell);
        return (
          style.whiteSpace !== "nowrap" ||
          style.direction !== "rtl" ||
          cell.getAttribute("title") !== cell.textContent ||
          !cell.textContent.startsWith("S-1-")
        );
      }).length,
      sidsCut: sids.filter((cell) => cell.scrollWidth > cell.clientWidth).length,
    };
  });
}

await check(`sign in as ${SIGN_IN_AS}`, async () => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: new RegExp(`Sign in as ${SIGN_IN_AS}\\b`, "i") }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
});

for (const directory of ["users", "groups"]) {
  await check(`${directory} directory: one-line rows, names without SIDs, SIDs in one bounded line`, async () => {
    await page.goto(`${BASE}/identities/${directory}`, { waitUntil: "networkidle" });
    const m = await measureDirectory();
    expect(m.rows > 0, "no rows");
    expect(m.maxHeight <= ONE_LINE_ROW_PX, `tallest row ${m.maxHeight.toFixed(1)} px`);
    expect(m.namesWithSid === 0, `${m.namesWithSid} name cells carry a SID`);
    expect(m.sids === m.rows, `${m.sids} SID cells for ${m.rows} rows`);
    expect(m.sidsBad === 0, `${m.sidsBad} SID cells not one rtl line with the full value as text and title`);
    return `${m.rows} rows ≤ ${m.maxHeight.toFixed(1)} px; ${m.sids} SIDs, ${m.sidsCut} cut at the start`;
  });
}

await check("relationship tables: names without SIDs; chains of names; paths on one line", async () => {
  await page.goto(`${BASE}/identities/principal/${BIANCA}/effective-groups`, { waitUntil: "networkidle" });
  // The view's own table, not the aliases table inside Identity information.
  const groups = await page.locator("main table").evaluateAll((tables) => {
    const table = tables.find((candidate) => !candidate.closest("details"));
    const rows = [...(table?.querySelectorAll("tbody tr") ?? [])];
    return {
      rows: rows.length,
      namesWithSid: rows.filter((row) => /S-1-\d/.test(row.querySelector("th")?.innerText ?? "")).length,
      rawSids: rows.filter((row) => /S-1-\d/.test(row.innerText)).length,
      links: table?.querySelectorAll("tbody a[href^='/identities/principal/']").length ?? 0,
      // A long chain may wrap; names never stack a SID beneath them.
      wrapped: rows.filter((row) => row.getBoundingClientRect().height > 44).length,
    };
  });
  expect(groups.rows > 0, "no effective groups");
  expect(groups.links > groups.rows, `${groups.links} principal links for ${groups.rows} rows`);
  expect(groups.namesWithSid === 0 && groups.rawSids === 0, `SID text in ${groups.rawSids} rows`);
  await page.goto(`${BASE}/identities/principal/${BIANCA}/shares`, { waitUntil: "networkidle" });
  const paths = await page.locator("main table .identifier").evaluateAll((all) => {
    const cells = all.filter((cell) => !cell.closest("details"));
    return {
      count: cells.length,
      wrapping: cells.filter((cell) => getComputedStyle(cell).whiteSpace !== "nowrap").length,
      untitled: cells.filter((cell) => cell.getAttribute("title") !== cell.textContent).length,
    };
  });
  expect(paths.count > 0, "no share paths");
  expect(paths.wrapping === 0 && paths.untitled === 0, `${paths.wrapping} wrapping, ${paths.untitled} without the full value`);
  return (
    `${groups.rows} effective groups, ${groups.links} principal links, 0 SIDs, ${groups.wrapped} rows with a wrapped chain; ` +
    `${paths.count} share paths, one line each`
  );
});

await check("Exclude local groups removes machine-local groups only; Domain Local groups stay", async () => {
  await page.goto(`${BASE}/identities/groups`, { waitUntil: "networkidle" });
  const all = await allPrincipals("kind=groups");
  const kept = await allPrincipals("kind=groups&exclude_local=true");
  const local = all.filter((item) => item.kind === "local_group");
  const domainLocal = (items) => items.filter((item) => item.kind === "domain_group" && item.group_scope === "domain_local").length;
  const keptKeys = new Set(kept.map((item) => item.key));
  expect(local.length > 0, "the dataset has no local group to exclude");
  expect(kept.every((item) => item.kind !== "local_group"), "a local group survived the filter");
  expect(kept.length === all.length - local.length, `${all.length} − ${local.length} ≠ ${kept.length}`);
  expect(all.filter((item) => item.kind !== "local_group").every((item) => keptKeys.has(item.key)), "a non-local group was dropped");
  expect(domainLocal(all) > 0 && domainLocal(kept) === domainLocal(all), `Domain Local ${domainLocal(all)} → ${domainLocal(kept)}`);
  return `${all.length} → ${kept.length} (${local.length} local removed); Domain Local ${domainLocal(all)} → ${domainLocal(kept)}`;
});

let groupPath = "";
const groupVisits = [];
await check(`every relationship view of a group, without a reload, one request each`, async () => {
  await page.goto(`${BASE}/identities/groups?q=${GROUP}`, { waitUntil: "networkidle" });
  await navigate(page.locator("table tbody th").getByRole("link", { name: GROUP, exact: true }), "/identities/principal/");
  groupPath = new URL(page.url()).pathname;
  const heading = await h1();
  const labels = await tabs().getByRole("link").allInnerTexts();
  for (const view of ["Effective members", "Parent groups", "Effective groups", "Reachable shares", "Directory access", "Named on share ACLs"]) {
    expect(labels.includes(view), `no ${view} tab (${labels.join(", ")})`);
  }
  groupVisits.push(path());
  const start = await tabs().locator("[aria-current=page]").innerText();
  const counts = [];
  for (const view of labels.filter((label) => label !== start).concat(start)) {
    await mark();
    await navigate(tabs().getByRole("link", { name: view, exact: true }), "/");
    await noReload(view);
    expect((await h1()) === heading, `${view}: heading changed to ${await h1()}`);
    expect(new URL(page.url()).pathname.startsWith(groupPath), `${view}: left the group (${path()})`);
    expect((await tabs().locator("[aria-current=page]").innerText()) === view, `${view}: not marked current`);
    counts.push(flights.length);
    groupVisits.push(path());
  }
  expect(counts.every((n) => n <= 1), `requests per switch: ${counts.join(",")}`);
  return `${labels.join(", ")}; requests per switch ${counts.join(",")}`;
});

/**
 * One history step. `page.goBack()` returns before a same-document navigation lands, so the
 * step is taken in the page and the URL change awaited.
 */
async function step(direction) {
  const before = page.url();
  await page.evaluate((back) => (back ? history.back() : history.forward()), direction === "back");
  await page.waitForURL((url) => url.href !== before);
  await page.waitForLoadState("networkidle");
}

await check("Back walks the views in reverse and Forward replays them, in one document", async () => {
  expect(groupVisits.length > 2, "no view history to walk");
  await mark();
  for (let i = groupVisits.length - 2; i >= 0; i -= 1) {
    await step("back");
    expect(path() === groupVisits[i], `Back ${groupVisits.length - 1 - i}: ${path()} ≠ ${groupVisits[i]}`);
  }
  for (let i = 1; i < groupVisits.length; i += 1) {
    await step("forward");
    expect(path() === groupVisits[i], `Forward ${i}: ${path()} ≠ ${groupVisits[i]}`);
  }
  await noReload("Back/Forward");
  return `${groupVisits.length - 1} steps back and forward`;
});

await check("switching back and forth does not accumulate requests", async () => {
  const views = ["Effective groups", "Reachable shares"];
  const counts = [];
  for (let round = 0; round < 3; round += 1) {
    for (const view of views) {
      await mark();
      await navigate(tabs().getByRole("link", { name: view, exact: true }), "/");
      counts.push(flights.length);
    }
  }
  expect(counts.every((n) => n <= 1), `requests per switch: ${counts.join(",")}`);
  return `requests per switch ${counts.join(",")}`;
});

await check("Identity information: a newly selected object starts open; the summary is a keyboard control", async () => {
  await page.goto(`${BASE}/identities/principal/${BIANCA}/effective-groups`, { waitUntil: "networkidle" });
  await disclosure().locator("summary").click();
  expect(!(await disclosure().evaluate((node) => node.open)), "did not collapse");
  await mark();
  await navigate(page.locator("table tbody").getByRole("link", { name: GROUP, exact: true }).first(), "/identities/principal/");
  await noReload("chain link");
  expect(await disclosure().evaluate((node) => node.open), "the new object's Identity information is collapsed");
  const summary = disclosure().locator("summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  expect(!(await disclosure().evaluate((node) => node.open)), "Enter did not collapse");
  await page.keyboard.press(" ");
  expect(await disclosure().evaluate((node) => node.open), "Space did not expand");
  return `${await h1()}: open, Enter/Space toggle`;
});

const deepLinks = [
  "/",
  "/resources",
  "/identities/users",
  "/identities/groups?exclude_local=1",
  `/identities/principal/${BIANCA}`,
  `/identities/principal/${BIANCA}/effective-groups`,
  `/identities/principal/${AARON}/directories`,
  `/identities/principal/${AARON}/directories?mode=tree`,
  `/identities/principal/${AARON}/directories?mode=paths`,
  `/identities/principal/${AARON}/shares`,
  `/identities/principal/${AARON}/acl-references`,
  "/search?q=Finance",
];
await check("every route family answers a fresh document load through the edge", async () => {
  if (groupPath) {
    deepLinks.push(`${groupPath}/effective-members`, `${groupPath}/members`, `${groupPath}/groups`);
  }
  const failures = [];
  for (const link of deepLinks) {
    const response = await page.goto(`${BASE}${link}`, { waitUntil: "networkidle" });
    const text = await page.locator("main").innerText().catch(() => "");
    if (response?.status() !== 200 || (await page.locator("h1").count()) === 0 || /Application error|API unavailable/i.test(text)) {
      failures.push(`${link} → ${response?.status()}`);
    }
  }
  expect(failures.length === 0, failures.join("; "));
  return `${deepLinks.length} routes, 200 with a heading`;
});

await check("export: the links name content=summary|paths, never mode; both CSVs complete", async () => {
  await page.goto(`${BASE}/identities/principal/${AARON}/directories`, { waitUntil: "networkidle" });
  const hrefs = await page.locator("a[href*='/api/directory-access/export']").evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  const contents = hrefs.map((href) => new URL(href, "http://x").searchParams);
  expect(hrefs.length === 2, `${hrefs.length} export links`);
  expect(contents.every((params) => !params.has("mode")), `an export link names mode: ${hrefs}`);
  expect(contents.map((params) => params.get("content")).sort().join() === "paths,summary", `contents ${hrefs}`);
  const rows = {};
  for (const href of hrefs) {
    const response = await page.request.get(`${BASE}${href}`);
    expect(response.status() === 200, `${href}: ${response.status()}`);
    expect((response.headers()["content-type"] ?? "").startsWith("text/csv"), `${href}: ${response.headers()["content-type"]}`);
    expect(response.headers()["x-adg-complete"] === "true", `${href}: complete=${response.headers()["x-adg-complete"]}`);
    rows[new URL(href, "http://x").searchParams.get("content")] = (await response.text()).trim().split("\n").length - 1;
  }
  return `summary ${rows.summary} rows, paths ${rows.paths} rows`;
});

await check("Directory access: the first summary and a repeated one (timing)", async () => {
  const times = [];
  for (let i = 0; i < 3; i += 1) {
    const started = Date.now();
    const response = await page.goto(`${BASE}/identities/principal/${AARON}/directories`, { waitUntil: "load" });
    expect(response?.status() === 200, `status ${response?.status()}`);
    times.push(Date.now() - started);
  }
  return `document loads ${times.join(" / ")} ms`;
});

await check("no page errors", async () => {
  expect(pageErrors.length === 0, pageErrors.join(" | "));
  return `${consoleErrors.length} console error(s)`;
});

await browser.close();
const failed = results.filter((result) => !result.ok);
console.log(
  JSON.stringify(
    { base: BASE, passed: results.length - failed.length, failed: failed.length, consoleErrors, pageErrors, results },
    null,
    2,
  ),
);
process.exit(failed.length === 0 ? 0 : 1);
