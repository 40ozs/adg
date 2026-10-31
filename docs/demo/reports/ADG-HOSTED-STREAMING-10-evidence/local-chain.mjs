#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-10, local hops. Two listeners on loopback:
 *
 *   probe/browser ──► GATE_PORT: the real render/gate.ts `createGate` (the code the Render
 *                     service runs) ─┬─ API paths ─► API_PORT
 *                                    └─ the rest ──► WEB_PORT (Next.js standalone, whose
 *                                                    ADG_INTERNAL_API_URL is DELAY_PORT)
 *   DELAY_PORT ──► API_PORT, holding DELAY_PATH (the Overview's review-queue read) DELAY_MS
 *
 * so the same Next.js server can be timed directly (WEB_PORT) and through the gate (GATE_PORT)
 * with one Overview section slow and the rest fast. Test-only.
 *
 *   GATE_PORT=10090 DELAY_PORT=8190 API_PORT=8090 WEB_PORT=3090 ORIGIN_KEY_FILE=… \
 *   GATE_TS=file:///…/frontend/render/gate.ts node local-chain.mjs   (Node ≥ 22.18 strips the types)
 */
import { readFileSync } from "node:fs";
import http from "node:http";

const { createGate } = await import(process.env.GATE_TS);
const env = (name, fallback) => Number(process.env[name] ?? fallback);
const API_PORT = env("API_PORT", 8090);
const DELAY_PATH = process.env.DELAY_PATH ?? "/api/v1/governance/queue";
const DELAY_MS = env("DELAY_MS", 4000);
const key = readFileSync(process.env.ORIGIN_KEY_FILE, "utf8").trim();

http
  .createServer((req, res) => {
    const forward = () => {
      const upstream = http.request({ host: "127.0.0.1", port: API_PORT, path: req.url, method: req.method, headers: req.headers }, (up) => {
        res.writeHead(up.statusCode, up.headers);
        up.pipe(res);
      });
      upstream.on("error", () => {
        if (!res.headersSent) res.writeHead(502);
        res.end();
      });
      req.pipe(upstream);
    };
    if (new URL(req.url, "http://x").pathname === DELAY_PATH) setTimeout(forward, DELAY_MS);
    else forward();
  })
  .listen(env("DELAY_PORT", 8190), "127.0.0.1");

http
  .createServer(createGate({ key, apiPort: API_PORT, webPort: env("WEB_PORT", 3090), log: () => {} }))
  .listen(env("GATE_PORT", 10090), "127.0.0.1", () => console.log(`gate :${env("GATE_PORT", 10090)} delay ${DELAY_PATH} ${DELAY_MS} ms`));
