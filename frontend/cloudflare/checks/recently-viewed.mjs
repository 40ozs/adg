#!/usr/bin/env node
/**
 * Local browser gate for Recently viewed (ADG-UX-RECENT-05B): the real pages, rendered by the
 * real Next.js server against the real API and the Genesee Press demo dataset, in headless
 * Chromium. The list lives in each tab's sessionStorage (ADG-UX-RECENT-05A), so most checks read
 * that storage directly and compare it with what the Overview renders.
 *
 *   FastAPI (python -m app, ADG_RUNTIME_MODE=demo) $API_URL
 *     ◄── this script's recording proxy 127.0.0.1:$PROXY_PORT  (Next.js ADG_INTERNAL_API_URL)
 *           ◄── Next.js standalone server.js $WEB_URL  ◄── Chromium
 *
 *   WEB_URL=http://127.0.0.1:3050 API_URL=http://127.0.0.1:8050 PROXY_PORT=8150 \
 *   CHROMIUM_PATH=… SCREENSHOT_DIR=… REPORT_JSON=… node cloudflare/checks/recently-viewed.mjs
 *
 * The proxy records every request the web tier sends the API (to prove the list adds none and
 * never travels), and can fail a path or rewrite `/auth/me` to take a capability away from the
 * same account (a role revoked between two page loads). Every substituted state is named
 * "fixture" in its check. The browser's own requests are recorded too.
 *
 * Writes nothing to the database: development sign-ins only. Refuses to run against anything
 * but loopback. Needs the `playwright` package (NODE_PATH works) and the default development
 * accounts admin and planner. Exits non-zero on any failed check.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import http from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

// An ESM import ignores NODE_PATH; require() honors it, so a global install also works.
const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROXY_PORT = Number(process.env.PROXY_PORT ?? 8100);
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

const KEY = "adg.recentlyViewed.v1";
const HOUR = 3_600_000;
const owner = (subject) => createHash("sha256").update(subject, "utf8").digest("hex");
const ADMIN = owner("dev:admin");
const PLANNER = owner("dev:planner");

/* ------------------------------------------------------------------ recording proxy */

/** Every API request the web tier made, in order, with what it carried. */
const apiRequests = [];
/** pathname → { status } | { transform(json) → json } */
const faults = new Map();
const proxy = http.createServer((req, res) => {
  const url = new URL(req.url, API);
  const fault = faults.get(url.pathname) ?? null;
  const entry = { method: req.method, path: url.pathname, query: url.search, headers: JSON.stringify(req.headers), body: "", at: performance.now(), status: null };
  apiRequests.push(entry);
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    entry.body = body.toString("utf8");
    if (fault?.status) {
      res.writeHead(fault.status, { "content-type": "application/json" });
      res.end(JSON.stringify({ detail: "Injected by the recently-viewed gate." }));
      entry.status = fault.status;
      return;
    }
    const headers = { ...req.headers, host: new URL(API).host };
    delete headers["accept-encoding"];
    const upstream = http.request(url, { method: req.method, headers }, (up) => {
      if (!fault?.transform || up.statusCode !== 200) {
        res.writeHead(up.statusCode, up.headers);
        up.pipe(res);
        entry.status = up.statusCode;
        return;
      }
      const parts = [];
      up.on("data", (part) => parts.push(part));
      up.on("end", () => {
        const out = Buffer.from(JSON.stringify(fault.transform(JSON.parse(Buffer.concat(parts).toString("utf8")))));
        const outHeaders = { ...up.headers, "content-length": String(out.length) };
        delete outHeaders.etag;
        res.writeHead(200, outHeaders);
        res.end(out);
        entry.status = 200;
      });
    });
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
      entry.status = 502;
    });
    upstream.end(body);
  });
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

/* ------------------------------------------------------------------ harness */

const results = [];
const pageErrors = [];
const consoleErrors = [];
/** Every request any page sent, from the browser. */
const browserRequests = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    results.push({ name, ok: false, detail: String(error?.message ?? error).split("\n")[0] });
    console.log(`FAIL  ${name} — ${String(error?.message ?? error).split("\n")[0]}`);
  } finally {
    faults.clear();
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}
const squash = (value) => value.replace(/\s+/g, " ").trim();
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function apiToken(username) {
  const response = await fetch(`${API}/auth/dev/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username }),
  });
  expect(response.ok, `API sign-in as ${username}: HTTP ${response.status}`);
  return (await response.json()).access_token;
}
async function api(token, path) {
  const response = await fetch(`${API}${path}`, { headers: { authorization: `Bearer ${token}` } });
  expect(response.ok, `${path}: HTTP ${response.status}`);
  return response.json();
}

for (let attempt = 0; ; attempt += 1) {
  const ready = await fetch(`${WEB}/login`).then((r) => r.ok, () => false);
  const apiReady = await fetch(`${API}/health/ready`).then((r) => r.ok, () => false);
  if (ready && apiReady) break;
  expect(attempt < 120, "the web tier or the API never became ready");
  await new Promise((resolve) => setTimeout(resolve, 1000));
}

/* ------------------------------------------------------------------ the dataset */

const adminToken = await apiToken("admin");
const servers = (await api(adminToken, "/api/v1/servers?limit=20")).items;
expect(servers.length >= 12, `need 12 servers, the dataset has ${servers.length}`);
const people = (await api(adminToken, "/api/v1/search?q=chen")).identities.filter((p) => p.user_principal_name && p.display_name);
expect(people.length >= 1, "need a named identity with a UPN");
const [JANE] = people;
const SERVER = servers.find((s) => s.key === "gp-fin-fs01") ?? servers[0];
const SHARE = (await api(adminToken, `/api/v1/servers/${encodeURIComponent(SERVER.key)}/shares`)).items.find((s) => s.name === "Finance");
const DIRECTORY_PATH = "\\\\GP-FIN-FS01\\Finance\\Reports\\Revenue";
const DIRECTORY = await api(adminToken, `/api/v1/resources/${encodeURIComponent(DIRECTORY_PATH)}`);
const FINDING_TITLES = (await api(adminToken, "/api/v1/risks/rules")).rules.map((r) => r.title).filter(Boolean);

const href = {
  identity: (key) => `/identities/principal/${encodeURIComponent(key)}`,
  server: (key) => `/resources/server?key=${encodeURIComponent(key)}`,
  share: (key) => `/resources/share?key=${encodeURIComponent(key)}`,
  directory: (key) => `/resources/directory?key=${encodeURIComponent(key)}`,
};

/* ------------------------------------------------------------------ browser */

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});

/**
 * Records, in the page, every value the Recently viewed list ever rendered and what storage
 * held when the logout request started. Installed before any document script runs.
 */
function instrument() {
  window.__rendered = [];
  window.__logoutSaw = undefined;
  const watch = () => {
    for (const li of document.querySelectorAll('[aria-label="Recently viewed items"] li')) {
      window.__rendered.push(li.textContent);
    }
  };
  new MutationObserver(watch).observe(document, { subtree: true, childList: true, characterData: true });
  const original = window.fetch.bind(window);
  window.fetch = (input, init) => {
    // Next passes URL objects as well as strings and Requests; a wrapper that throws on one
    // turns every client navigation into a full document load.
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes("/api/auth/logout")) window.__logoutSaw = sessionStorage.getItem("adg.recentlyViewed.v1");
    return original(input, init);
  };
}

async function newContext(viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(60_000);
  await context.addInitScript(instrument);
  context.on("page", (page) => watchPage(page, "page"));
  return context;
}
function watchPage(page, name) {
  page.on("pageerror", (error) => pageErrors.push(`${name}: ${error}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource/.test(message.text())) consoleErrors.push(`${name}: ${message.text()}`);
  });
  page.on("request", (request) => {
    browserRequests.push({ url: request.url(), headers: JSON.stringify(request.headers()), body: request.postData() ?? "", at: performance.now() });
  });
}
async function signIn(context, username) {
  const login = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username } });
  expect(login.ok(), `sign-in as ${username}: HTTP ${login.status()}`);
}
/** Sign in through the login page, in this tab, as a person would. */
async function signInHere(page, username) {
  await page.goto(`${WEB}/login`, { waitUntil: "load" });
  await page.getByRole("button", { name: `Sign in as ${username}` }).click();
  await page.waitForURL(`${WEB}/`);
  await settled(page);
}

async function settled(page) {
  await page.waitForFunction(() => !document.querySelector('#main [aria-busy="true"]'), null, { timeout: 20_000 });
}
async function stored(page) {
  return page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) ?? "null"), KEY);
}
async function storedRaw(page) {
  return page.evaluate((key) => sessionStorage.getItem(key), KEY);
}
const keysOf = (value) => (value?.entries ?? []).map((e) => `${e.kind}:${e.key}`);

/** Open a detail page and wait until its successful read has been recorded. */
async function visit(page, kind, key) {
  const response = await page.goto(`${WEB}${href[kind](key)}`, { waitUntil: "load" });
  expect(response?.status() === 200, `${kind} ${key}: HTTP ${response?.status()}`);
  await page.waitForFunction(
    ([storageKey, k, kk]) => (JSON.parse(sessionStorage.getItem(storageKey) ?? "null")?.entries ?? [])[0]?.key === kk && JSON.parse(sessionStorage.getItem(storageKey)).entries[0].kind === k,
    [KEY, kind, key],
    { timeout: 15_000 },
  );
}
/** Open a page that must not record, and give a writer every chance to run first. */
async function visitQuietly(page, path) {
  const response = await page.goto(`${WEB}${path}`, { waitUntil: "load" });
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(750);
  return response?.status();
}
async function overview(page) {
  const response = await page.goto(`${WEB}/`, { waitUntil: "load" });
  expect(response?.status() === 200, `/: HTTP ${response?.status()}`);
  await settled(page);
  // The section fills in after hydration: its list, or a second paragraph (empty state or notice).
  await page.waitForFunction(() => {
    const section = document.querySelector('[aria-labelledby="overview-recent"]');
    return section?.querySelector("ul, p + p") != null;
  }, null, { timeout: 15_000 });
  return response;
}
/** The rows as rendered: "Kind label". */
async function rows(page) {
  return page.$$eval('[aria-label="Recently viewed items"] li', (items) => items.map((li) => li.textContent.replace(/\s+/g, " ").trim()));
}
async function sectionText(page) {
  return squash(await page.getByRole("region", { name: "Recently viewed", exact: true }).innerText());
}
async function mark(page) {
  return page.evaluate(() => (window.__recent05b = Math.random()));
}
async function marked(page, value) {
  return page.evaluate((expected) => window.__recent05b === expected, value);
}
async function shot(page, name, fullPage = true) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage });
}

const label = {
  identity: JANE.display_name,
  server: SERVER.name,
  share: SHARE.name,
  directory: "Revenue",
};

/* ------------------------------------------------------------------ checks */

const context = await newContext();
await signIn(context, "admin");
const tab = await context.newPage();

await check("§34 basic recording: empty, then identity, server, share and directory, newest first", async () => {
  await overview(tab);
  expect(/^Recently viewed .*Nothing yet\./.test(await sectionText(tab)), `start: ${await sectionText(tab)}`);
  expect((await stored(tab)) === null, "storage not empty at start");

  await visit(tab, "identity", JANE.principal_key);
  await overview(tab);
  expect(same(await rows(tab), [`Identity ${label.identity}`]), `after identity: ${await rows(tab)}`);

  await visit(tab, "server", SERVER.key);
  await overview(tab);
  expect(same(await rows(tab), [`Server ${label.server}`, `Identity ${label.identity}`]), `after server: ${await rows(tab)}`);

  await visit(tab, "share", SHARE.key);
  await visit(tab, "directory", DIRECTORY.key);
  await overview(tab);
  const expected = [`Directory ${label.directory}`, `Share ${label.share}`, `Server ${label.server}`, `Identity ${label.identity}`];
  expect(same(await rows(tab), expected), `four: ${await rows(tab)}`);
  await shot(tab, "populated-1440");
  return (await rows(tab)).join(" | ");
});

await check("§34/§28 each entry links to its canonical route", async () => {
  const links = await tab.$$eval('[aria-label="Recently viewed items"] a', (as) => as.map((a) => a.getAttribute("href")));
  const expected = [href.directory(DIRECTORY.key), href.share(SHARE.key), href.server(SERVER.key), href.identity(JANE.principal_key)];
  expect(same(links, expected), `hrefs: ${links}`);
  for (const [i, path] of expected.entries()) {
    await overview(tab);
    await tab.locator('[aria-label="Recently viewed items"] a').nth(i).click();
    await tab.waitForURL(`${WEB}${path}`);
    expect((await tab.locator("#main h1").first().innerText()).length > 0, `${path}: no heading`);
  }
  return `${expected.length} links followed`;
});

await check("§35 refresh and same-tab navigation keep the list", async () => {
  await overview(tab);
  const before = await rows(tab);
  await tab.reload({ waitUntil: "load" });
  await settled(tab);
  await tab.waitForSelector('[aria-label="Recently viewed items"]');
  expect(same(await rows(tab), before), "lost on reload");
  await visitQuietly(tab, "/changes");
  await overview(tab);
  expect(same(await rows(tab), before), "lost after navigating away and back");
  return `${before.length} rows kept`;
});

await check("§43/§29 storage holds only v, owner digest and kind/key/label/at — no UPN, email, GUID, finding title or UNC label", async () => {
  const raw = await storedRaw(tab);
  const value = JSON.parse(raw);
  expect(same(Object.keys(value).sort(), ["entries", "owner", "v"]), `top-level fields: ${Object.keys(value)}`);
  expect(value.v === 1, "version");
  expect(value.owner === ADMIN, "owner is not sha256(dev:admin)");
  expect(!raw.includes("dev:admin") && !raw.includes("Development Administrator"), "raw account stored");
  for (const entry of value.entries) {
    expect(same(Object.keys(entry).sort(), ["at", "key", "kind", "label"]), `entry fields: ${Object.keys(entry)}`);
    expect(!entry.label.includes("\\") && !entry.label.includes("/"), `label carries a path: ${entry.label}`);
  }
  expect(!raw.includes(JANE.user_principal_name), "UPN stored");
  expect(!/[^\s"@]+@[^\s"@]+\.[a-z]{2,}/i.test(raw), "an email-like value is stored");
  expect(!/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(raw), "a GUID is stored");
  for (const title of FINDING_TITLES) expect(!raw.includes(title), `finding title stored: ${title}`);
  const directory = value.entries.find((e) => e.kind === "directory");
  expect(directory.label === "Revenue", `directory label: ${directory.label}`);
  expect(directory.key === DIRECTORY.key, "directory key is not the canonical key");
  expect(value.entries.filter((e) => e.label.includes(DIRECTORY_PATH) || e.label.toLowerCase().includes(DIRECTORY.key)).length === 0, "full UNC path as a label");
  const identity = value.entries.find((e) => e.kind === "identity");
  expect(identity.label === JANE.display_name, "identity label is not the display name");
  if (SCREENSHOTS) writeFileSync(join(SCREENSHOTS, "..", "storage-shape.json"), `${JSON.stringify(value, null, 2)}\n`);
  return `${raw.length} bytes, ${value.entries.length} entries; identity label = display name (05A-allowed)`;
});

await check("§36 a new independent tab has its own empty list, and recording there leaves tab A alone", async () => {
  const before = await storedRaw(tab);
  const tabB = await context.newPage();
  await overview(tabB);
  expect((await stored(tabB)) === null, "new tab B sees storage");
  expect(/Nothing yet\./.test(await sectionText(tabB)), "new tab B shows entries");
  await visit(tabB, "server", servers[5].key);
  expect((await storedRaw(tab)) === before, "tab A changed");
  // window.open with noopener: an independent browsing context, no storage copy.
  const [noopener] = await Promise.all([context.waitForEvent("page"), tab.evaluate((url) => window.open(url, "_blank", "noopener"), `${WEB}/`)]);
  await noopener.waitForLoadState("load");
  const isolated = (await stored(noopener)) === null;
  // window.open with an opener: HTML copies the opener's sessionStorage into the new context.
  const [opened] = await Promise.all([context.waitForEvent("page"), tab.evaluate((url) => window.open(url, "_blank"), `${WEB}/`)]);
  await opened.waitForLoadState("load");
  const copied = (await storedRaw(opened)) === before;
  await visit(opened, "server", servers[6].key);
  const aAfter = (await storedRaw(tab)) === before;
  for (const p of [tabB, noopener, opened]) await p.close();
  expect(isolated, "a noopener tab saw tab A's storage");
  expect(aAfter, "recording in an opened tab changed tab A");
  return `newPage: empty; window.open noopener: empty; window.open with opener: ${copied ? "starts with a COPY of A's list (browser semantics), then diverges" : "empty"}`;
});

await check("§41 not-found, forbidden (fixture), API error (fixture) and malformed routes record nothing", async () => {
  const before = keysOf(await stored(tab));
  const statuses = [];
  statuses.push(await visitQuietly(tab, href.server("gp-nope-fs99")));
  faults.set(`/api/v1/servers/${servers[7].key}`, { status: 403 });
  statuses.push(await visitQuietly(tab, href.server(servers[7].key)));
  faults.clear();
  faults.set(`/api/v1/servers/${servers[8].key}`, { status: 500 });
  statuses.push(await visitQuietly(tab, href.server(servers[8].key)));
  faults.clear();
  statuses.push(await visitQuietly(tab, href.directory("not-a-unc-path")));
  statuses.push(await visitQuietly(tab, "/identities/principal/%E0%A4%A"));
  statuses.push(await visitQuietly(tab, href.identity("S-1-5-21-0-0-0-999999")));
  const after = keysOf(await stored(tab));
  expect(same(after, before), `recorded on a failed load: ${after.filter((k) => !before.includes(k))}`);
  const text = squash(await tab.locator("#main").innerText());
  expect(text.length > 0, "error page rendered nothing");
  return `statuses ${statuses.join(",")}; list unchanged (${after.length})`;
});

await check("§41 a prefetched but never visited route records nothing", async () => {
  const before = keysOf(await stored(tab));
  await visitQuietly(tab, "/resources");
  const links = tab.locator('#main a[href^="/resources/server?key="]');
  const count = Math.min(await links.count(), 5);
  for (let i = 0; i < count; i += 1) await links.nth(i).hover();
  await tab.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await tab.waitForTimeout(1500);
  const prefetches = browserRequests.filter((r) => /_rsc=|next-router-prefetch/i.test(r.url + r.headers)).length;
  const after = keysOf(await stored(tab));
  expect(same(after, before), `a prefetch recorded: ${after.filter((k) => !before.includes(k))}`);
  return `${count} server links hovered and in view; ${prefetches} RSC/prefetch requests so far; list unchanged`;
});

await check("§7/§59 findings, reviews, what-if, investigate, compare, search and list pages are not tracked", async () => {
  const before = await storedRaw(tab);
  const paths = ["/risks", "/governance", "/simulations", "/access", "/changes", "/search?q=chen", "/resources", "/identities/principal", "/demo"];
  for (const path of paths) await visitQuietly(tab, path);
  expect((await storedRaw(tab)) === before, "a non-entity page changed the list");
  return `${paths.length} pages`;
});

await check("§41/§20 following an entry whose object is gone shows not-found and removes it", async () => {
  const value = await stored(tab);
  value.entries.unshift({ kind: "server", key: "gp-gone-fs99", label: "GP-GONE-FS99", at: Date.now() - 1000 });
  await tab.evaluate(([k, v]) => sessionStorage.setItem(k, v), [KEY, JSON.stringify(value)]);
  await overview(tab);
  expect((await rows(tab))[0] === "Server GP-GONE-FS99", "seeded entry not shown");
  await tab.getByRole("link", { name: "GP-GONE-FS99" }).click();
  await tab.waitForURL(`${WEB}${href.server("gp-gone-fs99")}`);
  await tab.waitForFunction((k) => !(sessionStorage.getItem(k) ?? "").includes("gp-gone-fs99"), KEY, { timeout: 15_000 });
  const text = squash(await tab.locator("#main").innerText());
  await overview(tab);
  expect(!(await rows(tab)).some((r) => r.includes("GP-GONE-FS99")), "still listed");
  return `detail page said: "${text.slice(0, 90)}…"`;
});

await check("§46/§28 a Recent link navigates client-side, Back returns, titles and grouped nav hold, revisit moves to top", async () => {
  await overview(tab);
  const token = await mark(tab);
  const lastRow = (await rows(tab)).at(-1);
  await tab.locator('[aria-label="Recently viewed items"] a').last().click();
  await tab.waitForURL((url) => url.pathname.startsWith("/identities/principal/") || url.pathname.startsWith("/resources/"));
  await tab.waitForFunction(() => document.querySelector("#main h1") !== null);
  expect(await marked(tab, token), "full document reload");
  const title = await tab.title();
  expect(/^(Identity|Server|Share|Directory) — ADG$/.test(title), `detail title: ${title}`);
  const groups = await tab.$$eval('nav[aria-label="Primary"] ul[aria-labelledby^="nav-group-"]', (uls) => uls.length);
  expect(groups >= 4, `grouped nav: ${groups} groups`);
  await tab.waitForFunction(([k, row]) => {
    const first = JSON.parse(sessionStorage.getItem(k)).entries[0];
    return row.endsWith(first.label);
  }, [KEY, lastRow], { timeout: 15_000 });
  await tab.goBack();
  await tab.waitForURL(`${WEB}/`);
  await settled(tab);
  await tab.waitForSelector('[aria-label="Recently viewed items"]');
  expect(await marked(tab, token), "Back reloaded the document");
  expect((await tab.title()) === "Overview — ADG", `Overview title: ${await tab.title()}`);
  const now = await rows(tab);
  expect(now[0] === lastRow, `revisited entry not on top: ${now[0]} vs ${lastRow}`);
  expect(new Set(now).size === now.length, "duplicate rows");
  return `${lastRow} → top; ${now.length} rows, no duplicates; title ${title}`;
});

await check("§40 eleven unique views keep ten, evicting the oldest; a repeat moves to top with a new time", async () => {
  await tab.evaluate((k) => sessionStorage.removeItem(k), KEY);
  const eleven = servers.slice(0, 11);
  for (const server of eleven) await visit(tab, "server", server.key);
  let value = await stored(tab);
  expect(value.entries.length === 10, `length ${value.entries.length}`);
  expect(!keysOf(value).includes(`server:${eleven[0].key}`), "oldest not evicted");
  expect(value.entries[0].key === eleven[10].key, "newest not first");
  const repeat = eleven[4];
  const oldAt = value.entries.find((e) => e.key === repeat.key).at;
  await visit(tab, "server", repeat.key);
  value = await stored(tab);
  expect(value.entries.length === 10, "repeat changed the length");
  expect(value.entries.filter((e) => e.key === repeat.key).length === 1, "duplicate entry");
  expect(value.entries[0].key === repeat.key && value.entries[0].at > oldAt, "repeat not moved to top with a newer time");
  await overview(tab);
  expect((await rows(tab)).length === 10, "Overview does not show ten");
  return `evicted ${eleven[0].key}; repeat ${repeat.key} at +${value.entries[0].at - oldAt} ms`;
});

await check("§40 an entry older than eight hours is removed from storage before display", async () => {
  const value = await stored(tab);
  value.entries.push({ kind: "server", key: "gp-old-fs00", label: "GP-OLD-FS00", at: Date.now() - 8 * HOUR - 60_000 });
  value.entries.splice(9, 1); // stay at ten, so the cap is not what removes it
  await tab.evaluate(([k, v]) => sessionStorage.setItem(k, v), [KEY, JSON.stringify(value)]);
  await overview(tab);
  expect(!(await rows(tab)).some((r) => r.includes("GP-OLD-FS00")), "expired entry displayed");
  expect(!(await storedRaw(tab)).includes("gp-old-fs00"), "expired entry left in storage");
  expect(!(await tab.evaluate(() => window.__rendered)).some((t) => t.includes("GP-OLD-FS00")), "expired entry rendered transiently");
  return `${(await rows(tab)).length} rows after expiry`;
});

await check("§42 permission loss (fixture: /auth/me without resources:read) removes resource entries with no per-entry request", async () => {
  await tab.evaluate((k) => sessionStorage.removeItem(k), KEY);
  await visit(tab, "identity", JANE.principal_key);
  await visit(tab, "server", SERVER.key);
  await visit(tab, "directory", DIRECTORY.key);
  faults.set("/auth/me", { transform: (me) => ({ ...me, capabilities: me.capabilities.filter((c) => c !== "resources:read") }) });
  const from = performance.now();
  await overview(tab);
  const during = apiRequests.filter((r) => r.at >= from).map((r) => r.path);
  const shown = await rows(tab);
  const text = await sectionText(tab);
  const left = keysOf(await stored(tab));
  faults.clear();
  expect(same(shown, [`Identity ${label.identity}`]), `shown: ${shown}`);
  expect(same(left, [`identity:${JANE.principal_key}`]), `storage: ${left}`);
  expect(text.includes("Some items were removed because this account can no longer open them."), "no removal notice");
  const perEntry = during.filter((p) => /^\/api\/v1\/(servers|shares|resources|principals|access)\//.test(p));
  expect(perEntry.length === 0, `per-entry reads: ${perEntry}`);
  return `API paths during that render: ${[...new Set(during)].join(", ")}`;
});

await check("§51/§44 the list adds no API read or write, and no request ever carries it", async () => {
  // Same Overview, empty list vs ten entries: the web tier's API requests must match.
  const paths = async () => {
    const from = performance.now();
    await overview(tab);
    await tab.waitForTimeout(500);
    return apiRequests.filter((r) => r.at >= from).map((r) => `${r.method} ${r.path}`).sort();
  };
  const saved = await storedRaw(tab);
  await tab.evaluate((k) => sessionStorage.removeItem(k), KEY);
  const empty = await paths();
  await tab.evaluate(([k, v]) => sessionStorage.setItem(k, v), [KEY, saved]);
  const browserFrom = performance.now();
  const full = await paths();
  expect(same(empty, full), `API requests differ: empty [${empty}] vs populated [${full}]`);
  const browserApi = browserRequests.filter((r) => r.at >= browserFrom && new URL(r.url).pathname.startsWith("/api/"));
  expect(browserApi.length === 0, `browser API calls on Overview: ${browserApi.map((r) => r.url)}`);
  return `${full.length} API requests either way (${[...new Set(full)].join(", ")})`;
});

await check("§45 Clear empties the list and storage at once, sends nothing, and keeps focus in the section", async () => {
  await visit(tab, "server", SERVER.key);
  await overview(tab);
  expect((await rows(tab)).length > 0, "nothing to clear");
  await tab.getByRole("button", { name: "Clear", exact: true }).scrollIntoViewIfNeeded();
  const scrollBefore = await tab.evaluate(() => window.scrollY);
  const apiBefore = apiRequests.length;
  const browserBefore = browserRequests.length;
  await tab.getByRole("button", { name: "Clear", exact: true }).focus();
  await tab.keyboard.press("Enter");
  await tab.waitForFunction(() => document.querySelector('[aria-label="Recently viewed items"]') === null);
  await tab.waitForTimeout(500);
  expect((await storedRaw(tab)) === null, "storage not cleared");
  expect(/Nothing yet\./.test(await sectionText(tab)), "no empty state");
  expect(apiRequests.length === apiBefore, `${apiRequests.length - apiBefore} API requests`);
  const sent = browserRequests.slice(browserBefore).map((r) => r.url);
  expect(sent.every((u) => !new URL(u).pathname.startsWith("/api/")), `Clear sent an API request: ${sent}`);
  expect(sent.every((u) => !u.includes(KEY) && !u.includes("recent")), `Clear sent: ${sent}`);
  const focus = await tab.evaluate(() => document.activeElement?.id);
  expect(focus === "overview-recent", `focus moved to ${focus}`);
  const scrollAfter = await tab.evaluate(() => window.scrollY);
  expect(scrollBefore === 0 || scrollAfter > 0, `scrolled to top (${scrollBefore} → ${scrollAfter})`);
  await shot(tab, "empty-1440");
  return `focus on the section heading; scrollY ${scrollBefore} → ${scrollAfter}; browser requests after Clear: ${sent.length ? sent.map((u) => new URL(u).pathname + new URL(u).search.replace(/&?_rsc=[^&]*/, "")).join(", ") : "none"}`;
});

await check("§38 sign-out clears storage before the logout request; a hanging and a failing logout leave nothing", async () => {
  const outcomes = [];
  for (const mode of ["hang", "fail", "real"]) {
    await visit(tab, "server", SERVER.key);
    await overview(tab);
    expect((await storedRaw(tab)) !== null, "nothing stored before sign-out");
    let release;
    if (mode !== "real") {
      await tab.route("**/api/auth/logout", async (route) => {
        if (mode === "fail") return route.fulfill({ status: 500, body: "{}" });
        await new Promise((resolve) => (release = resolve));
        return route.fulfill({ status: 500, body: "{}" }).catch(() => {});
      });
    }
    await tab.getByRole("button", { name: "Sign out" }).click();
    await tab.waitForFunction(() => window.__logoutSaw !== undefined, null, { timeout: 10_000 });
    const saw = await tab.evaluate(() => window.__logoutSaw);
    const now = await storedRaw(tab).catch(() => "unavailable");
    expect(saw === null, `${mode}: storage held the list when the request started`);
    expect(now === null, `${mode}: storage not empty after sign-out (${now})`);
    outcomes.push(`${mode}: cleared before request`);
    if (mode !== "real") {
      release?.();
      await tab.waitForURL(`${WEB}/login`);
      await tab.unroute("**/api/auth/logout");
      await tab.evaluate(() => (window.__logoutSaw = undefined));
    } else {
      await tab.waitForURL(`${WEB}/login`);
    }
  }
  await signInHere(tab, "admin");
  return outcomes.join("; ");
});

await check("§37 account switch (admin → planner in the same tab, no sign-out): admin's entries never render for planner", async () => {
  await tab.evaluate((k) => sessionStorage.removeItem(k), KEY);
  await visit(tab, "identity", JANE.principal_key);
  await visit(tab, "directory", DIRECTORY.key);
  await overview(tab);
  // 05A §14 item 20: identity and directory listed, newest first.
  expect(same(await rows(tab), [`Directory ${label.directory}`, `Identity ${label.identity}`]), `admin rows: ${await rows(tab)}`);
  const adminLabels = [label.directory, label.identity];
  await tab.evaluate(() => (window.__rendered = []));
  let html = "";
  const onResponse = async (response) => {
    if (new URL(response.url()).pathname === "/" && response.request().resourceType() === "document") html = await response.text().catch(() => "");
  };
  tab.on("response", onResponse);
  // The login page signs planner in without signing admin out: only the owner check can clear.
  await signInHere(tab, "planner");
  await tab.waitForFunction(() => {
    const section = document.querySelector('[aria-labelledby="overview-recent"]');
    return section?.querySelector("ul, p + p") != null;
  });
  const rendered = await tab.evaluate(() => window.__rendered);
  const value = await storedRaw(tab);
  tab.off("response", onResponse);
  expect(!rendered.some((t) => adminLabels.some((l) => t.includes(l))), `flash: ${rendered}`);
  expect((await rows(tab)).length === 0, "planner sees rows");
  expect(/Nothing yet\./.test(await sectionText(tab)), "no empty state for planner");
  expect(value === null || !value.includes(ADMIN), "admin's list still in storage");
  expect(!adminLabels.some((l) => html.includes(l)), "server HTML carries admin's labels");
  await shot(tab, "account-switch-planner");
  // And the other way, through the writer: planner records, then admin signs in and opens a page.
  await visit(tab, "server", SERVER.key);
  expect((await stored(tab)).owner === PLANNER, "planner's list not owned by planner");
  await signInHere(tab, "admin");
  await visit(tab, "share", SHARE.key);
  const back = await stored(tab);
  expect(back.owner === ADMIN && same(keysOf(back), [`share:${SHARE.key}`]), `admin inherited planner's list: ${keysOf(back)}`);
  return `mutation log saw ${rendered.length} rows for planner; storage after switch: ${value === null ? "deleted" : "planner-owned"}`;
});

await check("§39 same-account reauthentication after session expiry keeps the list", async () => {
  await visit(tab, "identity", JANE.principal_key);
  await overview(tab);
  const before = await rows(tab);
  const raw = await storedRaw(tab);
  // Session expiry: the httpOnly cookie is gone; nothing in the page is told.
  await context.clearCookies();
  await tab.goto(`${WEB}/`, { waitUntil: "load" });
  const signedOut = await tab.getByRole("region", { name: "Recently viewed", exact: true }).count();
  expect(signedOut === 0, "list rendered while signed out");
  expect((await storedRaw(tab)) === raw, "storage changed while signed out");
  await signInHere(tab, "admin");
  await tab.waitForSelector('[aria-label="Recently viewed items"]');
  expect(same(await rows(tab), before), `lost after reauth: ${await rows(tab)}`);
  return `${before.length} rows survived`;
});

await check("§48 accessibility: region, heading, named links with kind text, one Clear button, no nested controls, keyboard", async () => {
  await visit(tab, "server", servers[2].key);
  await overview(tab);
  const region = tab.getByRole("region", { name: "Recently viewed", exact: true });
  expect((await region.count()) === 1, "no Recently viewed region");
  expect((await region.getByRole("heading", { level: 2, name: "Recently viewed" }).count()) === 1, "no h2");
  const links = await region.getByRole("link").allInnerTexts();
  expect(links.every((t) => t.trim().length > 0), "unnamed link");
  const kinds = (await rows(tab)).every((r) => /^(Identity|Server|Share|Directory) /.test(r));
  expect(kinds, "a row without its kind");
  expect((await region.getByRole("button").count()) === 1, "not exactly one button");
  expect((await region.getByRole("button", { name: "Clear", exact: true }).count()) === 1, "Clear not named Clear");
  const nested = await region.evaluate((el) => el.querySelectorAll("a a, a button, button a, button button").length);
  expect(nested === 0, `${nested} nested controls`);
  const active = await tab.evaluate(() => document.activeElement?.tagName);
  expect(active === "BODY", `hydration moved focus to ${active}`);
  await region.getByRole("link").first().focus();
  const target = await region.getByRole("link").first().getAttribute("href");
  await tab.keyboard.press("Enter");
  await tab.waitForURL(`${WEB}${target}`);
  return `${links.length} links; Enter on the first opened ${target}`;
});

for (const width of [1440, 768, 375, 320]) {
  await check(`§47 responsive ${width}px: no horizontal overflow, compact rows, usable Clear, long label wraps`, async () => {
    const ctx = await newContext({ width, height: 900 });
    await signIn(ctx, "admin");
    const page = await ctx.newPage();
    await page.goto(`${WEB}/`, { waitUntil: "load" });
    const now = Date.now();
    const entries = [
      { kind: "identity", key: "S-1-5-21-3949361974-1583827165-633756269-99999", label: "S-1-5-21-3949361974-1583827165-633756269-99999", at: now - 1 },
      { kind: "directory", key: "\\\\gp-fin-fs01\\finance\\reports\\revenue", label: "A".repeat(200), at: now - 2 },
      ...servers.slice(0, 8).map((s, i) => ({ kind: "server", key: s.key, label: s.name, at: now - 10 - i })),
    ];
    await page.evaluate(([k, v]) => sessionStorage.setItem(k, v), [KEY, JSON.stringify({ v: 1, owner: ADMIN, entries })]);
    await overview(page);
    const metrics = await page.evaluate(() => {
      const items = [...document.querySelectorAll('[aria-label="Recently viewed items"] li')];
      const clear = [...document.querySelectorAll('[aria-labelledby="overview-recent"] button')][0].getBoundingClientRect();
      const line = parseFloat(getComputedStyle(items[2]).lineHeight) || 24;
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        rows: items.length,
        tallestShort: Math.max(...items.slice(2).map((li) => li.getBoundingClientRect().height)),
        line,
        clear: { left: clear.left, right: clear.right, height: clear.height, width: clear.width },
        viewport: window.innerWidth,
      };
    });
    expect(metrics.overflow <= 0, `horizontal overflow ${metrics.overflow}px`);
    expect(metrics.rows === 10, `${metrics.rows} rows`);
    expect(metrics.tallestShort <= metrics.line * 2 + 8, `a short row is ${metrics.tallestShort}px tall (line ${metrics.line}px)`);
    expect(metrics.clear.left >= 0 && metrics.clear.right <= metrics.viewport && metrics.clear.height >= 24, `Clear ${JSON.stringify(metrics.clear)}`);
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    expect((await storedRaw(page)) === null, "Clear did not clear");
    await page.evaluate(([k, v]) => sessionStorage.setItem(k, v), [KEY, JSON.stringify({ v: 1, owner: ADMIN, entries })]);
    await overview(page);
    if (width === 375) await shot(page, "populated-375");
    if (width === 320) await shot(page, "populated-320");
    await ctx.close();
    return `overflow ${metrics.overflow}px; short rows ≤ ${metrics.tallestShort}px; Clear ${Math.round(metrics.clear.width)}×${Math.round(metrics.clear.height)}`;
  });
}

await check("§21/§44 over the whole run, no request (browser or web tier) carried the list", async () => {
  const markers = [KEY, "recentlyViewed", ADMIN, PLANNER, "\"entries\""];
  const all = [
    ...browserRequests.map((r) => ({ where: "browser", text: `${r.url}\n${r.headers}\n${r.body}` })),
    ...apiRequests.map((r) => ({ where: "api", text: `${r.path}${r.query}\n${r.headers}\n${r.body}` })),
  ];
  const leaks = all.filter((r) => markers.some((m) => r.text.includes(m) || r.text.includes(encodeURIComponent(m))));
  expect(leaks.length === 0, `${leaks.length} requests carry a marker: ${leaks.slice(0, 2).map((l) => l.text.slice(0, 120))}`);
  // A list would carry several entries' keys at once. A client navigation carries two routes
  // (its target in the URL, the current page in Next-Router-State-Tree), so three distinct keys
  // that were in the list together is the signal. The server key is left out: it is a prefix of
  // the share and directory keys, so it would match inside them.
  const entryKeys = [JANE.principal_key, SHARE.key, DIRECTORY.key].map((k) => [k, encodeURIComponent(k), JSON.stringify(k).slice(1, -1)]);
  const multi = all.filter((r) => entryKeys.filter((forms) => forms.some((f) => r.text.includes(f))).length >= 3);
  expect(multi.length === 0, `${multi.length} requests carry the identity, share and directory keys together: ${multi.slice(0, 1).map((l) => l.text.slice(0, 400))}`);
  const label = all.filter((r) => r.text.includes(JANE.display_name) || r.text.includes(encodeURIComponent(JANE.display_name)));
  expect(label.length === 0, `${label.length} requests carry the identity's stored label`);
  return `${browserRequests.length} browser + ${apiRequests.length} web-tier requests scanned`;
});

await check("no page errors and no hydration errors", async () => {
  expect(pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "));
  const hydration = consoleErrors.filter((e) => /hydrat|#418|#423|did not match/i.test(e));
  expect(hydration.length === 0, hydration.slice(0, 3).join(" | "));
  return `${consoleErrors.length} other console errors (expected 4xx pages: ${consoleErrors.slice(0, 2).join(" | ")})`;
});

await browser.close();
proxy.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (REPORT) writeFileSync(REPORT, `${JSON.stringify({ web: WEB, results, consoleErrors }, null, 2)}\n`);
process.exit(failed.length === 0 ? 0 : 1);
