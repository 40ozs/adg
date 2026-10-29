#!/usr/bin/env node
/**
 * Hosted HTTP smoke checks for the ADG public demo staging deployment.
 *
 *   STAGING_URL=https://adg-demo-staging.<subdomain>.workers.dev \
 *   RENDER_ORIGIN=https://<service>.onrender.com \
 *   node cloudflare/checks/smoke.mjs
 *
 * The deployment is public, so every request is made as an anonymous visitor would make it,
 * then as the public demo account (SIGN_IN_AS, default `planner`). PUBLIC_ACCOUNTS (default
 * `viewer,planner`) is the whole list of accounts development sign-in may offer; every other
 * account must be refused. With RENDER_ORIGIN (the Render service) set, it also checks that the
 * service refuses anyone who reaches it directly, around the Worker: its pages and its API. Writes nothing but
 * development sign-ins: every request is a read, a write the demo must refuse, or a sign-in.
 * Prints one line per check and a JSON summary; exits non-zero on any failure. Never prints a
 * credential.
 */

const BASE = (process.env.STAGING_URL ?? "").replace(/\/+$/, "");
if (!BASE) {
  console.error("Set STAGING_URL.");
  process.exit(2);
}

const list = (value) => value.split(",").map((v) => v.trim()).filter(Boolean);
/** The only accounts a visitor may sign in as (the Render service's ADG_DEV_AUTH_USERS). */
const PUBLIC_ACCOUNTS = list(process.env.PUBLIC_ACCOUNTS ?? "viewer,planner");
const SIGN_IN_AS = process.env.SIGN_IN_AS ?? "planner";
/** The default development accounts that must not be public: each holds a write a visitor must not. */
const PRIVILEGED_ACCOUNTS = ["admin", "auditor", "reviewer", "governance", "approver"].filter(
  (account) => !PUBLIC_ACCOUNTS.includes(account),
);
/** Fragments of a connection string; no answer may contain one. */
const FORBIDDEN = ["postgresql+psycopg", "postgresql://", "postgres://", "adg_demo", ":5432", "supabase", "sslmode", "onrender.com"];
/**
 * Optional, comma-separated values the operator knows must never appear (the database host,
 * say). Reported as "a canary", never by value.
 */
const CANARIES = (process.env.SMOKE_CANARIES ?? "").split(",").map((v) => v.trim().toLowerCase()).filter(Boolean);

const results = [];
let cookie = "";
let apiToken = "";

async function request(path, { method = "GET", body, headers = {}, json } = {}) {
  const started = performance.now();
  const response = await fetch(`${BASE}${path}`, {
    method,
    redirect: "manual",
    headers: {
      ...(cookie ? { Cookie: cookie } : {}),
      ...(json !== undefined || body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : body,
  });
  const text = await response.text();
  return { response, text, ms: Math.round(performance.now() - started) };
}

async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, ok: true, detail });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } catch (error) {
    results.push({ name, ok: false, detail: String(error?.message ?? error) });
    console.log(`FAIL  ${name} — ${error?.message ?? error}`);
  }
}

function expect(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function noSecrets(text, where) {
  const lower = text.toLowerCase();
  for (const needle of FORBIDDEN) {
    expect(!lower.includes(needle), `${where} contains "${needle}"`);
  }
  for (const canary of CANARIES) {
    expect(!lower.includes(canary), `${where} contains a configured canary`);
  }
}

await check("an anonymous visitor reads no ADG data from the API", async () => {
  for (const path of ["/api/v1/demo", "/api/v1/risks/summary", "/api/v1/simulations", "/api/v1/remediation", "/auth/me"]) {
    const { response } = await request(path);
    expect(response.status === 401, `${path}: HTTP ${response.status}`);
  }
  return "5 routes → 401";
});

await check(`development sign-in offers only ${PUBLIC_ACCOUNTS.join(", ")}; every other account is refused`, async () => {
  const { response, text } = await request("/auth/config");
  expect(response.status === 200, `/auth/config: HTTP ${response.status}`);
  const offered = JSON.parse(text).development_accounts ?? [];
  expect(
    [...offered].sort().join(",") === [...PUBLIC_ACCOUNTS].sort().join(","),
    `offered ${offered.join(",") || "none"}`,
  );
  for (const account of PRIVILEGED_ACCOUNTS) {
    const refused = await request("/auth/dev/login", { method: "POST", json: { username: account } });
    expect(refused.response.status === 404, `${account}: HTTP ${refused.response.status}`);
  }
  return `${PRIVILEGED_ACCOUNTS.join(", ")} → 404`;
});

const RENDER_ORIGIN = (process.env.RENDER_ORIGIN ?? "").replace(/\/+$/, "");
if (RENDER_ORIGIN) {
  await check("the Render origin refuses a request that did not come through the Worker", async () => {
    // A guessed key is no key: only the Worker's own reaches the service.
    for (const path of ["/runtime", "/demo"]) {
      const guessed = await fetch(`${RENDER_ORIGIN}${path}`, { headers: { "X-ADG-Origin-Key": "x".repeat(64) } });
      expect(guessed.status === 403, `${path} with a guessed key: HTTP ${guessed.status}`);
    }
    // The API and the pages alike: nothing the application serves is served directly.
    const direct = ["/runtime", "/health/ready", "/auth/config", "/api/v1/demo", "/", "/demo", "/risks", "/api/adg/api/v1/demo"];
    for (const path of direct) {
      const response = await fetch(`${RENDER_ORIGIN}${path}`, { method: "GET", redirect: "manual" });
      const text = await response.text();
      expect(
        response.status === 403 && !text.includes("runtime_mode") && !text.includes("Genesee Press"),
        `${path}: HTTP ${response.status}`,
      );
    }
    const signIn = await fetch(`${RENDER_ORIGIN}/auth/dev/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin" }),
    });
    expect(signIn.status === 403, `sign-in: HTTP ${signIn.status}`);
    const webSignIn = await fetch(`${RENDER_ORIGIN}/api/auth/dev-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "planner" }),
    });
    expect(webSignIn.status === 403, `web sign-in: HTTP ${webSignIn.status}`);
    const live = await fetch(`${RENDER_ORIGIN}/health/live`);
    const liveBody = await live.json();
    expect(live.status === 200 && Object.keys(liveBody).sort().join() === "status,version", `/health/live: HTTP ${live.status}`);
    return `${direct.length + 4} requests → 403 without the origin key or with a guessed one; only /health/live open`;
  });
}

await check("homepage loads with the Demo indicator before sign-in", async () => {
  const { response, text, ms } = await request("/");
  expect(response.status === 200, `HTTP ${response.status}`);
  expect(/Demo environment/i.test(text), "no Demo indicator");
  expect(text.includes("Synthetic enterprise data"), "no synthetic-data label");
  expect(text.includes("Genesee Press"), "no dataset name");
  noSecrets(text, "homepage");
  return `${ms} ms`;
});

// After a page render on purpose: under the earlier Worker-rendered architecture, Next.js
// replaced the isolate's Request class on its first request, which once broke API requests made
// after it (adg-cloudflare-demo-01 handoff §10). Kept as an ordering regression check.
await check("/runtime reports demo / synthetic / genesee-press-v1 and discloses nothing", async () => {
  const { response, text, ms } = await request("/runtime");
  expect(response.status === 200, `HTTP ${response.status}`);
  const body = JSON.parse(text);
  expect(body.runtime_mode === "demo", `runtime_mode ${body.runtime_mode}`);
  expect(body.data_source === "synthetic", `data_source ${body.data_source}`);
  expect(body.dataset?.key === "genesee-press-v1", `dataset ${body.dataset?.key}`);
  expect(response.headers.get("x-adg-runtime-mode") === "demo", "no X-ADG-Runtime-Mode: demo");
  noSecrets(text, "/runtime");
  return `${ms} ms`;
});

await check("/health/ready is ready in demo mode without connection detail", async () => {
  const { response, text } = await request("/health/ready");
  expect(response.status === 200, `HTTP ${response.status}`);
  const body = JSON.parse(text);
  expect(body.status === "ready" && body.runtime_mode === "demo", text.slice(0, 200));
  noSecrets(text, "/health/ready");
  return `postgresql ${body.dependencies?.find((d) => d.name === "postgresql")?.latency_ms} ms`;
});

await check("/version reports demo", async () => {
  const { response, text } = await request("/version");
  const body = JSON.parse(text);
  expect(response.status === 200 && body.runtime_mode === "demo", text.slice(0, 200));
  return `${body.name} ${body.version}`;
});

await check("runtime mode cannot be switched remotely", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const { response } = await request("/runtime", { method, json: { runtime_mode: "production" } });
    expect(response.status === 405, `${method} /runtime answered ${response.status}`);
  }
  const forged = await request("/runtime?runtime_mode=production", {
    headers: { "X-ADG-Runtime-Mode": "production" },
  });
  expect(JSON.parse(forged.text).runtime_mode === "demo", "a query or header changed the mode");
  return "POST/PUT/PATCH/DELETE → 405; query and header ignored";
});

await check("external collector ingestion is refused (anonymous)", async () => {
  const { response, text } = await request("/api/v1/scan-runs", { method: "POST", json: {} });
  expect(response.status === 403 && JSON.parse(text).status === "demo_only", `${response.status} ${text.slice(0, 120)}`);
  return "403 demo_only";
});

await check(`development sign-in through the API (${SIGN_IN_AS})`, async () => {
  const { response, text } = await request("/auth/dev/login", { method: "POST", json: { username: SIGN_IN_AS } });
  expect(response.status === 200, `HTTP ${response.status}`);
  const body = JSON.parse(text);
  apiToken = body.access_token;
  expect(typeof apiToken === "string" && apiToken.length > 20, "no token");
  expect(!body.principal?.roles?.includes("admin"), `roles ${body.principal?.roles}`);
  return `${SIGN_IN_AS}: ${body.principal?.roles?.join(", ")}`;
});

await check(`external collector ingestion is refused (${SIGN_IN_AS})`, async () => {
  for (const path of ["/api/v1/scan-runs", "/api/v1/scan-runs/00000000-0000-0000-0000-000000000000/batches", "/api/v1/scan-runs/00000000-0000-0000-0000-000000000000/completion"]) {
    const { response, text } = await request(path, {
      method: "POST",
      json: {},
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    expect(response.status === 403 && JSON.parse(text).status === "demo_only", `${path}: ${response.status}`);
  }
  return "3 ingestion routes → 403 demo_only";
});

await check(`the public account cannot approve, export, alert or run governance (${SIGN_IN_AS})`, async () => {
  const id = "00000000-0000-0000-0000-000000000000";
  const writes = [
    ["POST", `/api/v1/remediation/${id}/approval`],
    ["POST", `/api/v1/remediation/${id}/exports`],
    ["POST", "/api/v1/alerts/watches"],
    ["POST", "/api/v1/alerts/deliveries/drain"],
    ["POST", "/api/v1/governance/campaigns"],
    ["POST", "/api/v1/governance/owners"],
    ["POST", `/api/v1/governance/items/${id}/decisions`],
    ["POST", "/api/v1/demo/scans"],
  ];
  for (const [method, path] of writes) {
    const { response } = await request(path, { method, json: {}, headers: { Authorization: `Bearer ${apiToken}` } });
    expect(response.status === 403, `${method} ${path}: HTTP ${response.status}`);
  }
  return `${writes.length} privileged writes → 403`;
});

await check("remediation execution does not exist", async () => {
  const id = "00000000-0000-0000-0000-000000000000";
  for (const path of [`/api/v1/remediation/${id}/execution`, `/api/v1/remediation/${id}/execute`, "/api/v1/remediation/execute"]) {
    const { response } = await request(path, { method: "POST", json: {}, headers: { Authorization: `Bearer ${apiToken}` } });
    expect(response.status === 404 || response.status === 405, `${path}: ${response.status}`);
  }
  return "no execution route (404/405)";
});

let scenarios = [];
await check("/api/v1/demo: Genesee Press, three generations applied, scenarios A–H", async () => {
  const { response, text } = await request("/api/v1/demo", { headers: { Authorization: `Bearer ${apiToken}` } });
  expect(response.status === 200, `HTTP ${response.status}`);
  const body = JSON.parse(text);
  scenarios = body.scenarios;
  const keys = scenarios.map((s) => s.key).join("");
  expect(keys === "ABCDEFGH", `scenarios ${keys}`);
  const applied = body.generations.filter((g) => g.applied).length;
  expect(applied === 3 && body.next_generation === null, `applied ${applied}, next ${body.next_generation}`);
  return `${applied} generations applied, next none`;
});

await check("web sign-in sets a session", async () => {
  const { response } = await request("/api/auth/dev-login", { method: "POST", json: { username: SIGN_IN_AS } });
  expect(response.status === 200, `HTTP ${response.status}`);
  const setCookie = response.headers.getSetCookie?.() ?? [response.headers.get("set-cookie")];
  cookie = setCookie.filter(Boolean).map((c) => c.split(";")[0]).join("; ");
  expect(cookie.length > 0, "no session cookie");
  return "session cookie set";
});

await check("web tier forwards no write to ingestion", async () => {
  const { response } = await request("/api/adg/api/v1/scan-runs", { method: "POST", json: {} });
  expect(response.status === 405, `HTTP ${response.status}`);
  return "405";
});

function scenario(key) {
  const found = scenarios.find((s) => s.key === key);
  if (!found) throw new Error(`no scenario ${key}`);
  return found;
}

async function page(path, mustContain) {
  const { response, text, ms } = await request(path);
  expect(response.status === 200, `HTTP ${response.status}`);
  expect(!text.includes("Your session has ended"), "signed out");
  expect(!text.includes("did not respond"), "API unreachable from the web tier");
  for (const needle of mustContain) {
    expect(text.includes(needle), `missing "${needle}"`);
  }
  noSecrets(text, path);
  return `${ms} ms`;
}

const q = (params) => new URLSearchParams(params).toString();

await check("/demo guide lists scenarios A–H", () => page("/demo", ["Demo guide", "Genesee Press", "A six-level chain", "A safe first clean-up"]));
await check("scenario A share loads (directory page)", () =>
  page(`/resources/directory?${q({ key: scenario("A").resource })}`, ["sec-share-design-write"]));
await check("representative principal loads (scenario F person)", () => {
  const person = scenario("F").subjects.find((s) => s.kind === "person");
  return page(`/identities/principal/${encodeURIComponent(person.sid)}`, [person.name]);
});
await check("scenario F access explanation loads", () => {
  const person = scenario("F").subjects.find((s) => s.kind === "person");
  return page(`/access/explain?${q({ principal: person.sid, resource: scenario("F").resource })}`, ["Revenue-Reporting"]);
});
await check("scenario G nested path loads", () => {
  const person = scenario("G").subjects.find((s) => s.kind === "person");
  return page(`/access/explain?${q({ principal: person.sid, resource: scenario("G").resource })}`, ["ENG-Payments-Oncall", "Engineering-Backend"]);
});
await check("risk UI loads", () => page("/risks", ["Risk findings, most consequential first"]));
await check("simulation UI loads", () => page("/simulations", ["What-if proposals"]));

const failed = results.filter((r) => !r.ok);
console.log(JSON.stringify({ base: BASE, passed: results.length - failed.length, failed: failed.length, results }, null, 2));
process.exit(failed.length === 0 ? 0 : 1);
