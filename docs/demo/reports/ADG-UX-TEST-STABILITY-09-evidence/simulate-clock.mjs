#!/usr/bin/env node
/**
 * ADG-UX-TEST-STABILITY-09 future-date proof. Sits between Next.js (ADG_INTERNAL_API_URL) and
 * TARGET_URL (the titles gate's proxy, or the API for the negative control). It moves each
 * Changes window read by SIMULATED_NOW − now, so what goes on is the window the Changes page
 * would ask for on SIMULATED_NOW. Only that window moves. The session and the API keep the real
 * clock. A whole-stack clock change would not work: the web tier compares the session's expiry
 * with its own clock, so it would sign the user out.
 *
 *   SIMULATED_NOW=2028-01-01T00:00:00Z LISTEN_PORT=8291 TARGET_URL=http://127.0.0.1:8190 \
 *   node simulate-clock.mjs
 *
 * Prints each window it moved, as "path  received → sent". Test-only; binds loopback.
 */
import http from "node:http";

const TARGET = process.env.TARGET_URL;
const PORT = Number(process.env.LISTEN_PORT);
const SIMULATED = Date.parse(process.env.SIMULATED_NOW ?? "");
if (!TARGET || !PORT || Number.isNaN(SIMULATED)) {
  console.error("Set TARGET_URL, LISTEN_PORT and SIMULATED_NOW.");
  process.exit(2);
}
// The same two reads the titles gate anchors: the only ones whose window is computed from "now".
const WINDOW_READS = new Set(["/api/v1/changes", "/api/v1/changes/summary"]);

http
  .createServer((req, res) => {
    const url = new URL(req.url, TARGET);
    if (WINDOW_READS.has(url.pathname)) {
      const offset = SIMULATED - Date.now();
      const before = `${url.searchParams.get("from")}..${url.searchParams.get("to")}`;
      for (const name of ["from", "to"]) {
        const value = Date.parse(url.searchParams.get(name));
        if (!Number.isNaN(value)) url.searchParams.set(name, new Date(value + offset).toISOString());
      }
      console.log(`${url.pathname}  ${before} → ${url.searchParams.get("from")}..${url.searchParams.get("to")}`);
    }
    const upstream = http.request(url, { method: req.method, headers: { ...req.headers, host: url.host } }, (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    });
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.pipe(upstream);
  })
  .listen(PORT, "127.0.0.1", () => console.log(`simulating ${new Date(SIMULATED).toISOString()} on :${PORT} → ${TARGET}`));
