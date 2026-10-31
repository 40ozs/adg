#!/usr/bin/env node
/**
 * ADG-UX-BACKEND-COPY-04: backend identity wording and readable identifiers, on the real
 * local demo stack.
 *
 * Signs in as `admin`, takes the scenario person and group from /api/v1/demo and the two
 * campaigns `campaign-setup.py` created (CAMPAIGNS_JSON), and checks:
 *
 * - explain / investigate / What-if: the backend sentences say "identity"; no generic
 *   "principal" in rendered text or accessible names (Windows terms of art allowed);
 * - explain removal table: no `principal:<sid>` engine id as a label, the id still on the
 *   row (`data-removal-edge`) and in the text export, the API still sends the same ids;
 * - reviews list and campaign page: scope tokens rendered as words, names where the page
 *   holds them, the explicit fallback where it does not;
 * - identity, group, findings and changes pages: no regression; the rule title and version;
 * - internal links < 400, no page errors, no sideways scroll at 375 and 320 px.
 *
 * Writes nothing except development sign-ins.
 *
 *   WEB_URL=… API_URL=… CAMPAIGNS_JSON=campaigns.json CHROMIUM_PATH=… \
 *   SCREENSHOT_DIR=… REPORT_JSON=… node copy-check.mjs
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const WEB = (process.env.WEB_URL ?? "http://127.0.0.1:3000").replace(/\/+$/, "");
const API = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
const REPORT = process.env.REPORT_JSON ?? "";
const CAMPAIGNS = JSON.parse(readFileSync(process.env.CAMPAIGNS_JSON ?? "campaigns.json", "utf8"));
for (const url of [WEB, API]) {
  if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname)) {
    console.error(`Refusing a non-loopback target: ${url}.`);
    process.exit(2);
  }
}
if (SCREENSHOTS) mkdirSync(SCREENSHOTS, { recursive: true });

/** Terms of art kept exact (report §2.2). Everything else that says "principal" fails. */
const ALLOWED = [
  /foreign security principals?/gi,
  /user principal name/gi,
  /well-known SYSTEM principal/gi,
  /Windows grants every principal full access/gi,
];

const results = [];
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
function generic(text) {
  let rest = text;
  for (const pattern of ALLOWED) rest = rest.replace(pattern, "");
  return [...rest.matchAll(/[^.\n]{0,40}\bprincipal(s|'s)?\b[^.\n]{0,40}/gi)].map((m) => m[0].trim());
}

async function apiGet(path, token) {
  const response = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(response.ok, `${path}: HTTP ${response.status}`);
  return response.json();
}
const login = await fetch(`${API}/auth/dev/login`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ username: "admin" }),
});
expect(login.ok, `API sign-in: HTTP ${login.status}`);
const token = (await login.json()).access_token;
const { person, group } = CAMPAIGNS;
const explainQuery = new URLSearchParams({ principal: person.sid, resource: person.resource }).toString();

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
async function pageFor(viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(90_000);
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
  // Streamed sections reveal after load (ADG-UX-PERF-01); read the settled page.
  await page.waitForFunction(() => !document.querySelector("#main [aria-busy='true']"), null, { timeout: 60_000 });
  return page;
}
async function terminology() {
  const text = await page.locator("main").innerText();
  const names = await page.evaluate(() =>
    [...document.querySelectorAll("main [aria-label], main [title], main th, main label, main summary, main option, main caption")]
      .map((node) => [node.getAttribute("aria-label"), node.getAttribute("title"), node.textContent].filter(Boolean).join(" | "))
      .join("\n"),
  );
  return [...generic(text), ...generic(names).map((m) => `(accessible name) ${m}`)];
}
async function noGenericPrincipal() {
  const found = await terminology();
  expect(found.length === 0, `generic principal: ${found.join(" ‖ ")}`);
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
  return `${sample.length}/${hrefs.length} links ok`;
}
async function shot(name) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage: false });
}
const identity = (key, view) => `/identities/principal/${encodeURIComponent(key)}${view ? `/${view}` : ""}`;

// --- contracts, straight from the API ---------------------------------------------------
let explanation;
await check("API: explain keeps principal= and principal:<sid> node ids; reason says identity", async () => {
  explanation = await apiGet(`/api/v1/access/explain?${explainQuery}`, token);
  const ids = explanation.graph.nodes.map((n) => n.id);
  expect(ids.includes(`principal:${person.sid}`), `no principal:<sid> node among ${ids.length}`);
  expect(explanation.removal_targets.some((t) => t.source.startsWith("principal:")), "no removal target from principal:");
  expect(!/\bprincipal\b/i.test(explanation.verdict.reason), `reason "${explanation.verdict.reason}"`);
  return `reason "${explanation.verdict.reason}"; ${explanation.removal_targets.length} removal targets`;
});

await check("API: rule redundant_access_paths keeps its id, version 1.0 and the identity title", async () => {
  const body = await apiGet("/api/v1/risks/rules", token);
  const rules = body.rules ?? body.items ?? body;
  const rule = rules.find((r) => (r.rule_id ?? r.id) === "redundant_access_paths");
  expect(rule, "rule missing");
  expect(rule.version === "1.0", `version ${rule.version}`);
  expect(rule.title === "One identity reaches a resource by several independent routes", `title "${rule.title}"`);
  return `version ${rule.version}`;
});

await check("API: What-if vocabulary codes unchanged, descriptions say identity", async () => {
  const vocabulary = await apiGet("/api/v1/simulations/vocabulary", token);
  const kinds = Object.fromEntries(vocabulary.change_kinds.map((k) => [k.code, k.description]));
  expect(kinds.add_member === "Add an identity to a group.", `add_member "${kinds.add_member}"`);
  expect(kinds.remove_member === "Remove an identity from a group.", `remove_member "${kinds.remove_member}"`);
  const all = JSON.stringify(vocabulary);
  const descriptions = [...all.matchAll(/"description":"([^"]*)"/g)].map((m) => m[1]).join("\n");
  const found = generic(descriptions);
  expect(found.length === 0, found.join(" ‖ "));
  expect(all.includes('"principal_budget"'), "truncation code principal_budget missing");
  return "codes add_member/remove_member/principal_budget kept";
});

// --- explanation and investigation --------------------------------------------------------
await check("Explain: identity wording, removal relationships named, ids kept on the row", async () => {
  await visit(`/access/explain?${explainQuery}`);
  const text = await page.locator("main").innerText();
  expect(text.includes("This identity holds rights on this object."), "verdict reason not rendered");
  expect(!/principal:S-1-/.test(text), "a principal:<sid> id is rendered as text");
  const edges = await page.locator("main [data-removal-edge]").evaluateAll((els) => els.map((el) => [el.dataset.removalEdge, el.textContent]));
  expect(edges.length === explanation.removal_targets.length, `rows ${edges.length} vs ${explanation.removal_targets.length}`);
  expect(edges.some(([, label]) => label.includes(`${person.name} is a member of`)), `labels: ${edges.map((e) => e[1]).join(" | ")}`);
  await noGenericPrincipal();
  const links = await internalLinksOk(20);
  await shot("explain-removals");
  return `${edges.length} rows, first "${edges[0]?.[1]}"; ${links}`;
});

await check("Explain export: human label with the canonical edge id beside it", async () => {
  const exported = await page.locator("main textarea").first().inputValue();
  const first = explanation.removal_targets[0];
  expect(exported.includes(`[${first.edge_id}]`), "edge id missing from export");
  expect(exported.includes(`${person.name} is a member of`), "no named removal in export");
  // Bracketed edge ids (`[membership:principal:S-…]`) are the canonical ids, kept on purpose.
  const prose = generic(exported.replace(/\[[^\]\n]*\]/g, ""));
  expect(prose.length === 0, prose.join(" ‖ "));
  return `export carries [${first.edge_id}]`;
});

await check("Investigate: identity wording, no generic principal", async () => {
  await visit(`/access/investigate?${explainQuery}`);
  await noGenericPrincipal();
  const links = await internalLinksOk(20);
  await shot("investigate");
  return links;
});

// --- What-if --------------------------------------------------------------------------
await check("What-if vocabulary on the new-simulation page says identity", async () => {
  await visit("/simulations/new");
  await page.locator("main summary", { hasText: "What a proposal can say" }).click();
  const text = await page.locator("main").innerText();
  expect(text.includes("Remove an identity from a group."), "remove_member description not rendered");
  expect(text.includes("Add an identity to a group."), "add_member description not rendered");
  await noGenericPrincipal();
  await shot("simulation-vocabulary");
  return await internalLinksOk(20);
});

// --- reviews ------------------------------------------------------------------------------
await check("Reviews list: scope tokens rendered as words, explicit fallback without a name", async () => {
  await visit("/governance");
  const text = await page.locator("main").innerText();
  expect(!/\bprincipal:S-1-/.test(text) && !/\bdirectory_tree:/.test(text) && !/\bshare:/.test(text), "a raw scope token is rendered");
  expect(text.includes(`Identity: ${group.sid} (name not loaded)`), "identity scope fallback missing");
  expect(text.includes(`Share: ${CAMPAIGNS.share}`), "share scope label missing");
  expect(text.includes(`Directory tree: ${CAMPAIGNS.resource}`), "directory tree scope label missing");
  await noGenericPrincipal();
  const links = await internalLinksOk(20);
  await shot("reviews-list");
  return links;
});

await check("Campaign page: reviewer scopes named from the items, links work", async () => {
  await visit(`/governance/campaigns/${CAMPAIGNS.identity_campaign}`);
  const table = page.locator("main section[aria-labelledby='reviewers']");
  const text = await table.innerText();
  expect(text.includes(`Identity: ${group.name}`), `group scope not named: ${text.slice(0, 200)}`);
  expect(text.includes(`Identity: ${person.name}`), "person scope not named");
  expect(!/principal:S-1-/.test(await page.locator("main").innerText()), "a raw scope token is rendered");
  await noGenericPrincipal();
  const links = await internalLinksOk(30);
  await shot("campaign-reviewers");
  return links;
});

await check("Campaign page filtered to one identity: heading names it", async () => {
  await visit(`/governance/campaigns/${CAMPAIGNS.identity_campaign}?principal=${encodeURIComponent(group.sid)}`);
  const heading = (await page.locator("main h2#items").innerText()).trim();
  expect(heading.includes(`for ${group.name}`), `heading "${heading}"`);
  return `heading "${heading}"`;
});

// --- regressions on identity, group, findings and changes ---------------------------------
for (const [name, path] of [
  ["Identity detail (person)", identity(person.sid, "groups")],
  ["Group detail", identity(group.sid, "members")],
  ["Findings: redundant routes", "/risks?rule=redundant_access_paths"],
]) {
  await check(`${name}: no generic principal, links work`, async () => {
    await visit(path);
    if (path.startsWith("/risks")) {
      const text = await page.locator("main").innerText();
      expect(text.includes("One identity reaches a resource by several independent routes"), "rule title missing");
    }
    await noGenericPrincipal();
    const links = await internalLinksOk();
    await shot(name.replace(/\W+/g, "-").toLowerCase());
    return links;
  });
}

await check("Changes scoped to an identity: the scope line says identity, the query stays principal=", async () => {
  await visit(`/changes?principal=${encodeURIComponent(group.sid)}`);
  const text = await page.locator("main").innerText();
  expect(text.includes(`identity: ${group.sid}`), "scope line not relabelled");
  expect(!text.includes(`principal: ${group.sid}`), "old scope line still rendered");
  await noGenericPrincipal();
  const links = await internalLinksOk(20);
  await shot("changes-identity-scope");
  return links;
});

for (const width of [375, 320]) {
  await check(`${width}px: explain, What-if, reviews and campaign pages do not scroll sideways`, async () => {
    const narrow = await pageFor({ width, height: 800 });
    const over = [];
    for (const path of [
      `/access/explain?${explainQuery}`,
      "/simulations/new",
      "/governance",
      `/governance/campaigns/${CAMPAIGNS.identity_campaign}`,
    ]) {
      const response = await narrow.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
      expect(response?.status() === 200, `${path}: HTTP ${response?.status()}`);
      await narrow.waitForFunction(() => !document.querySelector("#main [aria-busy='true']"), null, { timeout: 60_000 });
      const [sw, iw] = await narrow.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      if (sw > iw) over.push(`${path} ${sw}>${iw}`);
      if (SCREENSHOTS && path === "/governance") await narrow.screenshot({ path: join(SCREENSHOTS, `narrow-${width}-reviews.png`) });
    }
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
if (REPORT) writeFileSync(REPORT, JSON.stringify({ results, person: person.sid, group: group.sid }, null, 2));
process.exit(passed === results.length ? 0 : 1);
