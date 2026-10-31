#!/usr/bin/env node
/**
 * ADG-UX-ENTITY-02: identity terminology and entity context, on the real local demo stack.
 *
 * Signs in as `admin` (every capability), finds representative entities from /api/v1/demo
 * and the UI's own lists, and on each page checks:
 *
 * - HTTP 200, no page errors, an <h1> naming the object with its kind;
 * - rendered text and accessible names (aria-label, title, table headers, form labels) carry
 *   no generic "principal" (Windows terms of art allowed, see ALLOWED). Known backend engine
 *   prose deferred by this lane is reported, not failed (see DEFERRED);
 * - internal links on the page answer below 400;
 * - at 375 px and 320 px, no sideways page scroll.
 *
 * Read-only apart from development sign-ins.
 *
 *   WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 CHROMIUM_PATH=… \
 *   SCREENSHOT_DIR=… REPORT_JSON=… node entity-check.mjs
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

/** Windows / directory terms of art and field names that legitimately contain "principal". */
// `principal:<sid>` is an engine graph node id (contract value), printed as <code> in the
// removal-targets table: an internal identifier, preserved (report §5).
const ALLOWED = [/foreign security principals?/gi, /user principal name/gi, /\bprincipal:\S+/g];
/** Backend engine prose this lane classifies as deferred (report §5); reported, not failed. */
const DEFERRED = [
  /This principal holds rights[^.]*\./gi,
  /No entry on either ACL names this principal[^.]*\./gi,
  /No run has described this principal[^.]*\./gi,
  /this principal's access depends on the order of the entries\.?/gi,
  /Windows grants every principal full access[^.]*\./gi,
];

const results = [];
const deferredSeen = new Set();
const pageErrors = [];
async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    const detail = String(error?.message ?? error).split("\n")[0];
    results.push({ name, ok: false, detail });
    console.log(`FAIL  ${name} — ${detail}`);
  }
}
function expect(condition, message) {
  if (!condition) throw new Error(message);
}

/** Generic "principal" left in `text`, after removing allowed and deferred phrases. */
function generic(text, where) {
  let rest = text;
  for (const pattern of ALLOWED) rest = rest.replace(pattern, "");
  for (const pattern of DEFERRED) {
    for (const match of rest.match(pattern) ?? []) deferredSeen.add(`${where}: ${match.trim()}`);
    rest = rest.replace(pattern, "");
  }
  return [...rest.matchAll(/[^.\n]{0,40}\bprincipals?\b[^.\n]{0,40}/gi)].map((m) => m[0].trim());
}

// --- representative entities, from the demo API ------------------------------------------
const login = await fetch(`${API}/auth/dev/login`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ username: "admin" }),
});
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
const demo = await (await fetch(`${API}/api/v1/demo`, { headers: { Authorization: `Bearer ${token}` } })).json();
const scenarios = demo.scenarios ?? [];
const subjects = scenarios.flatMap((s) => (s.subjects ?? []).map((subject) => ({ ...subject, scenario: s })));
const person = subjects.find((s) => s.kind === "person" && s.scenario.key === "F") ?? subjects.find((s) => s.kind === "person");
const group = subjects.find((s) => s.kind === "group");
expect(person && group, `demo subjects: ${subjects.map((s) => s.kind).join()}`);

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
async function pageFor(viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(60_000);
  const signIn = await context.request.post(`${WEB}/api/auth/dev-login`, { data: { username: "admin" } });
  expect(signIn.ok(), `web sign-in: HTTP ${signIn.status()}`);
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error}`));
  return page;
}

const page = await pageFor();
const linkCache = new Map();
async function visit(path) {
  const response = await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
  expect(response?.status() === 200, `${path}: HTTP ${response?.status()}`);
  return page;
}
/** Text, accessible names and headers on the page, and every generic "principal" in them. */
async function terminology(where) {
  const text = await page.locator("main").innerText();
  const names = await page.evaluate(() =>
    [...document.querySelectorAll("main [aria-label], main [title], main th, main label, main summary, main option, main caption")]
      .map((node) => [node.getAttribute("aria-label"), node.getAttribute("title"), node.textContent].filter(Boolean).join(" | "))
      .join("\n"),
  );
  return [...generic(text, where), ...generic(names, `${where} (accessible names)`)];
}
async function internalLinksOk(limit = 30) {
  const hrefs = await page.locator("main a[href^='/']").evaluateAll((as) => [...new Set(as.map((a) => a.getAttribute("href")))]);
  const sample = hrefs.slice(0, limit);
  const broken = [];
  for (const href of sample) {
    if (!linkCache.has(href)) {
      const response = await page.request.get(`${WEB}${href}`, { maxRedirects: 5 });
      linkCache.set(href, response.status());
    }
    if (linkCache.get(href) >= 400) broken.push(`${href} → ${linkCache.get(href)}`);
  }
  expect(broken.length === 0, `broken: ${broken.join(", ")}`);
  return `${sample.length}/${hrefs.length} links checked`;
}
async function shot(name) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage: false });
}

const identity = (key, view) => `/identities/principal/${encodeURIComponent(key)}${view ? `/${view}` : ""}`;
const PAGES = [
  { name: "User: direct groups", path: identity(person.sid, "groups"), heading: person.name, kind: /user/, section: "Groups this identity is directly inside", column: "Group" },
  { name: "User: effective groups", path: identity(person.sid, "effective-groups"), heading: person.name, section: "Every group this identity ends up inside", column: "Group" },
  { name: "User: reachable shares", path: identity(person.sid, "shares"), heading: person.name, section: "Shares this identity can reach" },
  { name: "User: reachable directories", path: identity(person.sid, "directories"), heading: person.name, section: "Directories this identity can reach" },
  { name: "User: named on share ACLs", path: identity(person.sid, "acl-references"), heading: person.name, section: "Share ACLs that name this identity" },
  { name: "Group: direct members", path: identity(group.sid, "members"), heading: group.name, kind: /group/, section: "Direct members", column: "Member" },
  { name: "Group: effective members", path: identity(group.sid, "effective-members"), heading: group.name, section: "Everything that ends up inside this group", column: "Member" },
  { name: "Group: parent groups", path: identity(group.sid, "groups"), heading: group.name, section: "Groups this identity is directly inside", column: "Group" },
];

for (const target of PAGES) {
  await check(`${target.name}: header names the object, identity copy, links work`, async () => {
    await visit(target.path);
    const h1 = (await page.locator("h1").innerText()).trim();
    expect(h1.includes(target.heading), `h1 "${h1}"`);
    if (target.kind) expect(target.kind.test(await page.locator("h1 .title-kind").innerText()), `kind chip in "${h1}"`);
    expect(await page.getByRole("heading", { name: target.section }).count() > 0, `no section heading "${target.section}"`);
    expect((await page.locator("nav[aria-label='Identity sections'] [aria-current='page']").count()) === 1, "no current tab");
    // Only the open panel's table, and only when it has rows: an empty answer has no table.
    const headers = target.column ? await page.locator("main section:has(h2[id^='panel-']) th").allInnerTexts() : [];
    if (target.column && headers.length > 0) {
      expect(headers.some((h) => h.trim().startsWith(target.column)), `headers ${headers.join("|")}`);
    }
    const found = await terminology(target.name);
    expect(found.length === 0, `generic principal: ${found.join(" ‖ ")}`);
    const links = await internalLinksOk();
    await shot(target.name.replace(/\W+/g, "-").toLowerCase());
    return `h1 "${h1}"; ${links}`;
  });
}

// Resources: a server, a share and a directory, reached from the UI's own lists.
await check("Server, share and directory: header names the object with its kind, no generic principal", async () => {
  await visit("/resources");
  const serverHref = await page.locator("main a[href^='/resources/server']").first().getAttribute("href");
  await visit(serverHref); // shares are listed on their server's page
  const shareHref = await page.locator("main a[href^='/resources/share']").first().getAttribute("href");
  const seen = [];
  for (const [label, href] of [["server", serverHref], ["share", shareHref], ["directory", `/resources/directory?key=${encodeURIComponent(person.scenario.resource)}`]]) {
    expect(href, `no ${label} link`);
    await visit(href);
    const kind = (await page.locator("h1 .title-kind").innerText()).trim();
    expect(kind.length > 0, `${label}: no kind chip`);
    const found = await terminology(label);
    expect(found.length === 0, `${label}: ${found.join(" ‖ ")}`);
    await internalLinksOk();
    await shot(`resource-${label}`);
    seen.push(`${label} "${(await page.locator("h1").innerText()).trim()}"`);
  }
  return seen.join("; ");
});

await check("Findings: redundant-routes rule title says identity, filter key unchanged", async () => {
  await visit("/risks?rule=redundant_access_paths");
  const text = await page.locator("main").innerText();
  expect(text.includes("One identity reaches a resource by several independent routes"), "new title not rendered");
  expect(!text.includes("One principal reaches"), "old title still rendered");
  const found = await terminology("findings");
  expect(found.length === 0, found.join(" ‖ "));
  await shot("findings-redundant-routes");
  return "rule filter ?rule=redundant_access_paths still selects the rule";
});

await check("Access path: investigate and explain for the scenario person use Identity", async () => {
  const q = new URLSearchParams({ principal: person.sid, resource: person.scenario.resource }).toString();
  const out = [];
  for (const path of [`/access/investigate?${q}`, `/access/explain?${q}`]) {
    await visit(path);
    const found = await terminology(path.split("?")[0]);
    expect(found.length === 0, `${path}: ${found.join(" ‖ ")}`);
    await internalLinksOk(20);
    out.push(path.split("?")[0]);
  }
  await shot("access-explain");
  return out.join(", ");
});

for (const width of [375, 320]) {
  await check(`${width}px: identity and findings pages do not scroll sideways`, async () => {
    const narrow = await pageFor({ width, height: 800 });
    const over = [];
    for (const path of [identity(person.sid, "groups"), identity(group.sid, "members"), "/risks?rule=redundant_access_paths"]) {
      const response = await narrow.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
      expect(response?.status() === 200, `${path}: HTTP ${response?.status()}`);
      const [sw, iw] = await narrow.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      if (sw > iw) over.push(`${path} ${sw}>${iw}`);
    }
    if (SCREENSHOTS) await narrow.screenshot({ path: join(SCREENSHOTS, `narrow-${width}-findings.png`) });
    expect(over.length === 0, over.join(", "));
    await narrow.context().close();
  });
}

await check("no page errors", async () => {
  expect(pageErrors.length === 0, pageErrors.join(" ‖ "));
});

await browser.close();
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} passed`);
if (deferredSeen.size) {
  console.log("\nDeferred backend engine prose seen (classified in report §5, not failed):");
  for (const line of deferredSeen) console.log(`  - ${line}`);
}
if (REPORT) writeFileSync(REPORT, JSON.stringify({ results, deferred: [...deferredSeen], person: person.sid, group: group.sid }, null, 2));
process.exit(passed === results.length ? 0 : 1);
