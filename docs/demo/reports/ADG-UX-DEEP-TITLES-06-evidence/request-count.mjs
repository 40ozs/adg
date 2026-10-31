#!/usr/bin/env node
/**
 * ADG-UX-DEEP-TITLES-06: do the new titles cost any backend request?
 *
 * Two web servers built from different trees, each with ADG_INTERNAL_API_URL pointed at its own
 * recording proxy in front of the same API:
 *
 *   BEFORE_WEB (baseline build) → proxy :BEFORE_PROXY ┐
 *   AFTER_WEB  (this lane)      → proxy :AFTER_PROXY  ┴→ API_URL
 *
 * For every page it asks both servers for the same thing, once as a document (direct load)
 * and once as an RSC payload (what a client-side move fetches; status 200 after Next's redirect), one request at a time, and
 * records which API calls each server made. Calls are compared by method, path and sorted
 * query keys (values hold the current time). Any difference is an extra request.
 *
 *   API_URL=http://127.0.0.1:8030 BEFORE_WEB=http://127.0.0.1:3031 AFTER_WEB=http://127.0.0.1:3030 \
 *     BEFORE_PROXY=8131 AFTER_PROXY=8130 PATHS_FILE=paths.json node request-count.mjs
 */

import { readFileSync, writeFileSync } from "node:fs";
import http from "node:http";

const API = new URL(process.env.API_URL ?? "http://127.0.0.1:8030");
const SIDES = {
  before: { web: process.env.BEFORE_WEB, port: Number(process.env.BEFORE_PROXY ?? 8131), seen: [] },
  after: { web: process.env.AFTER_WEB, port: Number(process.env.AFTER_PROXY ?? 8130), seen: [] },
};
const PATHS = JSON.parse(readFileSync(process.env.PATHS_FILE, "utf-8"));
const SETTLE_MS = 400;

function proxy(side) {
  return http.createServer((req, res) => {
    const url = new URL(req.url, API);
    const keys = [...new Set(url.searchParams.keys())].sort().join(",");
    side.seen.push(`${req.method} ${url.pathname}${keys ? `?${keys}` : ""}`);
    const upstream = http.request(
      { host: API.hostname, port: API.port, path: req.url, method: req.method, headers: { ...req.headers, host: API.host } },
      (answer) => {
        res.writeHead(answer.statusCode, answer.headers);
        answer.pipe(res);
      },
    );
    upstream.on("error", () => res.destroy());
    req.pipe(upstream);
  });
}

const servers = Object.values(SIDES).map((side) => proxy(side).listen(side.port, "127.0.0.1"));

const login = await fetch(new URL("/auth/dev/login", API), {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ username: process.env.SIGN_IN_AS ?? "admin" }),
}).then((r) => r.json());
const cookie = `adg_session=${Buffer.from(
  JSON.stringify({ accessToken: login.access_token, expiresAt: new Date(Date.now() + 3_600_000).toISOString(), mode: "development" }),
).toString("base64url")}`;

async function measure(side, path, rsc) {
  side.seen.length = 0;
  const headers = rsc ? { cookie, RSC: "1" } : { cookie, accept: "text/html" };
  // Next 16 answers a bare RSC request with a 307 to `?_rsc`; following it fetches the payload.
  const response = await fetch(`${side.web}${path}`, { headers, redirect: rsc ? "follow" : "manual" });
  const body = await response.text();
  await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
  // The document's own title is the first <title>; later ones are inline SVG <title>s.
  const title = rsc
    ? (body.match(/[^"]* — ADG(?=")/) ?? [null])[0]
    : (body.match(/<title>([^<]*)<\/title>/) ?? [])[1];
  return { status: response.status, calls: [...side.seen].sort(), title };
}

const rows = [];
let extra = 0;
for (const path of PATHS) {
  for (const rsc of [false, true]) {
    // Twice each, first warming caches, so a cold first render does not count as a difference.
    await measure(SIDES.before, path, rsc);
    await measure(SIDES.after, path, rsc);
    const before = await measure(SIDES.before, path, rsc);
    const after = await measure(SIDES.after, path, rsc);
    // The after-side calls the before side did not make (a multiset difference).
    const added = [...after.calls];
    for (const call of before.calls) {
      const i = added.indexOf(call);
      if (i >= 0) added.splice(i, 1);
    }
    extra += added.length;
    rows.push({ path, kind: rsc ? "rsc" : "document", before: before.calls.length, after: after.calls.length, added, status: [before.status, after.status], title: [before.title, after.title] });
    const titles = `  ${JSON.stringify(before.title)} → ${JSON.stringify(after.title)}`;
    console.log(`${rsc ? "rsc     " : "document"} ${String(before.calls.length).padStart(2)} → ${String(after.calls.length).padStart(2)}${added.length ? ` +${added.join(" +")}` : ""}  ${path}${titles}`);
  }
}

servers.forEach((server) => server.close());
console.log(`\n${rows.length} measurements, ${PATHS.length} pages; additional backend requests: ${extra}`);
if (process.env.JSON_OUT) {
  writeFileSync(process.env.JSON_OUT, JSON.stringify(rows, null, 2));
}
process.exit(extra === 0 ? 0 : 1);
