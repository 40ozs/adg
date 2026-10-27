#!/usr/bin/env node
/**
 * Hosted browser gate for the ADG public demo staging deployment: the integration's 22-check
 * browser gate (docs/handoffs/adg-demo-integration-01.md §8), driven against the staging URL
 * with headless Chromium as an anonymous visitor who then signs in as the public demo account.
 *
 *   STAGING_URL=… SCREENSHOT_DIR=./screens node cloudflare/checks/browser.mjs
 *
 * Needs the `playwright` package (CI installs it; locally `NODE_PATH` may point at a global
 * install). PUBLIC_ACCOUNTS and SIGN_IN_AS as in smoke.mjs. Writes only development sign-ins
 * and one unsaved simulation preview. Exits non-zero on any failed check or page error.
 */

import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

// An ESM import ignores NODE_PATH; require() honors it, so a global install also works.
const { chromium } = await import("playwright").catch(() => createRequire(import.meta.url)("playwright"));

const BASE = (process.env.STAGING_URL ?? "").replace(/\/+$/, "");
if (!BASE) {
  console.error("Set STAGING_URL.");
  process.exit(2);
}
const PUBLIC_ACCOUNTS = (process.env.PUBLIC_ACCOUNTS ?? "viewer,planner").split(",").map((v) => v.trim()).filter(Boolean);
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "planner";
const SCREENSHOTS = process.env.SCREENSHOT_DIR ?? "";
if (SCREENSHOTS) {
  mkdirSync(SCREENSHOTS, { recursive: true });
}
const TIMEOUT = Number(process.env.CHECK_TIMEOUT_MS ?? 60_000);

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(TIMEOUT);
const page = await context.newPage();

const pageErrors = [];
const consoleErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
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
async function shot(name) {
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `${name}.png`), fullPage: false });
}
async function text(locator) {
  return (await locator.innerText()).replace(/\s+/g, " ");
}
async function go(path) {
  const response = await page.goto(`${BASE}${path}`, { waitUntil: "load" });
  expect(response?.status() === 200, `${path}: HTTP ${response?.status()}`);
  const body = await text(page.locator("body"));
  expect(!body.includes("did not respond"), `${path}: API unreachable from the web tier`);
  expect(!body.includes("Your session has ended"), `${path}: signed out`);
  return body;
}

const indicator = page.locator("details.demo-indicator");

await check("signed out: header pill reads Demo environment · Synthetic enterprise data · Genesee Press", async () => {
  await go("/");
  const summary = await text(indicator.locator("summary"));
  expect(/Demo environment/i.test(summary) && summary.includes("Synthetic enterprise data · Genesee Press"), summary);
  await shot("cloudflare-indicator");
  return summary;
});

await check("explanation: synthetic data, no production AD/file servers, the application is real", async () => {
  await indicator.locator("summary").click();
  const panel = await text(indicator.locator(".demo-indicator-panel"));
  expect(panel.includes("No production Active Directory or file servers are connected"), "notice missing");
  expect(panel.includes("The application is real"), "'application is real' missing");
});

await check("the indicator offers no control (no button, input or select)", async () => {
  const controls = await indicator.locator("button, input, select, textarea, form").count();
  expect(controls === 0, `${controls} controls`);
});

await check("explanation lists the dataset's facts and links /demo", async () => {
  const panel = await text(indicator.locator(".demo-indicator-panel"));
  for (const fact of ["1,800", "640", "14", "205", "33", "91", "824", "4,111", "300", "41,756"]) {
    expect(panel.includes(fact), `fact ${fact} missing`);
  }
  expect((await indicator.locator('a[href="/demo"]').count()) === 1, "no /demo link");
});

await check("footer: organization and data fictional", async () => {
  const footer = await text(page.locator("footer"));
  expect(/fictional/i.test(footer), footer.slice(0, 200));
});

await check(`development sign-in offers only ${PUBLIC_ACCOUNTS.join(", ")}; signs in as ${SIGN_IN_AS}`, async () => {
  await go("/login");
  const offered = (await page.getByRole("button", { name: /^Sign in as / }).allInnerTexts()).map((label) =>
    label.replace(/^Sign in as /, "").trim(),
  );
  expect(offered.sort().join(",") === [...PUBLIC_ACCOUNTS].sort().join(","), `offered ${offered.join(",") || "none"}`);
  await page.getByRole("button", { name: `Sign in as ${SIGN_IN_AS}` }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  const body = await text(page.locator("body"));
  expect(!body.includes(`Sign in as ${SIGN_IN_AS}`), "still on the sign-in page");
  return `offered ${offered.join(", ")}`;
});

let scenarios = [];
await check("/demo guide renders scenarios A–H; three generations applied", async () => {
  const body = await go("/demo");
  const keys = await page.locator("li.demo-scenario .demo-scenario-key").allInnerTexts();
  expect(keys.join("") === "ABCDEFGH", `scenarios ${keys.join("")}`);
  for (const generation of ["g1", "g2", "g3"]) expect(body.includes(generation), `${generation} missing`);
  scenarios = await page.locator("li.demo-scenario").evaluateAll((items) =>
    items.map((item) => ({
      key: item.getAttribute("aria-label")?.replace("Scenario ", ""),
      folder: item.querySelector("p a")?.getAttribute("href"),
      explain: Array.from(item.querySelectorAll("a"))
        .filter((a) => a.textContent?.includes("explain their access"))
        .map((a) => a.getAttribute("href")),
      people: Array.from(item.querySelectorAll(".demo-scenario-subjects li a"))
        .slice(0, 1)
        .map((a) => a.getAttribute("href")),
    })),
  );
  await shot("cloudflare-guide");
  return `${keys.length} scenarios`;
});
const scenario = (key) => scenarios.find((s) => s.key === key) ?? { folder: null, explain: [], people: [] };

await check("scenario A folder opens the real directory page (Design share ACL)", async () => {
  expect(scenario("A").folder, "no folder link");
  const body = await go(scenario("A").folder);
  expect(body.includes("sec-share-design-write"), "sec-share-design-write missing");
});

await check("scenario B folder renders Genesee Press's ACL on the directory page", async () => {
  const body = await go(scenario("B").folder);
  expect(body.includes("Finance-RW"), "Finance-RW missing");
});

await check("a principal page loads (scenario B's person)", async () => {
  const href = scenario("B").people[0];
  expect(href, "no principal link");
  const body = await go(href);
  expect(body.includes("Lucia Hart"), "Lucia Hart missing");
});

await check("scenario F explanation: the transferred employee against Finance\\Reports\\Revenue, both routes", async () => {
  expect(scenario("F").explain[0], "no explain link");
  const body = await go(scenario("F").explain[0]);
  expect(body.includes("Brianna Reddy") && body.includes("Revenue"), "subject or folder missing");
  expect(body.includes("Revenue-Reporting") && body.includes("Sales-Ops"), "a route is missing");
  await shot("cloudflare-access-path");
});

await check("scenario G explanation: the six-level nested chain", async () => {
  expect(scenario("G").explain[0], "no explain link");
  const body = await go(scenario("G").explain[0]);
  for (const group of ["ENG-Payments-Oncall", "ENG-Payments", "Engineering-Backend", "Engineering"]) {
    expect(body.includes(group), `${group} missing`);
  }
});

await check("risk page renders the full pass (3 / 8 / 1957 / 423 / 6995)", async () => {
  const body = await go("/risks");
  for (const [severity, count] of [["critical", "3"], ["high", "8"], ["medium", "1957"], ["low", "423"], ["informational", "6995"]]) {
    expect(new RegExp(`${severity}\\s+${count}\\b`, "i").test(body), `${severity} ${count} missing`);
  }
  await shot("cloudflare-risks");
});

await check("scenario H through the real simulation editor: changes nobody's access", async () => {
  await go(scenario("H").folder);
  const row = page.locator("tr", { hasText: "Proj-Zephyr-RW" }).filter({ has: page.getByRole("link", { name: /Simulate/ }) }).first();
  await row.getByRole("link", { name: /Simulate/ }).click();
  await page.waitForURL(/\/simulations\/new/);
  await page.getByRole("button", { name: "Evaluate this proposal" }).click();
  await page.getByText("This proposal changes nobody's access.").waitFor({ timeout: Math.max(TIMEOUT, 120_000) });
  await shot("cloudflare-simulation");
});

await check("normal navigation: every primary section loads", async () => {
  for (const path of ["/", "/resources", "/identities", "/access", "/risks", "/changes", "/simulations", "/governance", "/collectors", "/settings", "/status"]) {
    await go(path);
  }
  return "11 sections";
});

await check("status page: backend reachable, demo", async () => {
  const body = await go("/status");
  expect(!body.includes("Backend unreachable"), "backend unreachable");
  expect(/demo/i.test(body), "no demo mode shown");
});

await check("no production operation: web tier POST /api/adg/api/v1/scan-runs → 405", async () => {
  const status = await page.evaluate(async () => (await fetch("/api/adg/api/v1/scan-runs", { method: "POST", body: "{}" })).status);
  expect(status === 405, `HTTP ${status}`);
});

await check(`no production operation: API POST /api/v1/scan-runs as ${SIGN_IN_AS} → 403 demo_only`, async () => {
  const login = await page.request.post(`${BASE}/auth/dev/login`, { data: { username: SIGN_IN_AS } });
  const token = (await login.json()).access_token;
  const response = await page.request.post(`${BASE}/api/v1/scan-runs`, {
    data: {},
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status() === 403 && (await response.json()).status === "demo_only", `HTTP ${response.status()}`);
});

await check("/runtime → demo / synthetic / genesee-press-v1, no connection detail", async () => {
  const response = await page.goto(`${BASE}/runtime`);
  const raw = await response.text();
  const body = JSON.parse(raw);
  expect(body.runtime_mode === "demo" && body.data_source === "synthetic" && body.dataset?.key === "genesee-press-v1", raw.slice(0, 120));
  for (const needle of ["postgresql+psycopg", "postgresql://", "adg_demo", ":5432", "sslmode"]) {
    expect(!raw.toLowerCase().includes(needle), `contains ${needle}`);
  }
});

await check("X-ADG-Runtime-Mode: demo on responses", async () => {
  const response = await page.request.get(`${BASE}/version`);
  expect(response.headers()["x-adg-runtime-mode"] === "demo", JSON.stringify(response.headers()["x-adg-runtime-mode"]));
});

await check("sign out", async () => {
  await go("/");
  const button = page.getByRole("button", { name: /Sign out/i });
  await button.click();
  await page.waitForTimeout(1000);
  await go("/");
  expect((await page.getByRole("button", { name: /Sign out/i }).count()) === 0, "still signed in");
});

await check("no fatal browser errors", async () => {
  expect(pageErrors.length === 0, `${pageErrors.length} page errors: ${pageErrors.slice(0, 2).join(" | ")}`);
  return `${consoleErrors.length} console errors`;
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(
  JSON.stringify(
    { base: BASE, passed: results.length - failed.length, failed: failed.length, consoleErrors, pageErrors, results },
    null,
    2,
  ),
);
process.exit(failed.length === 0 ? 0 : 1);
