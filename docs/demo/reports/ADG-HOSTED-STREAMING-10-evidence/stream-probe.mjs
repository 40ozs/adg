#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-10: raw HTTP arrival timing of one signed-in page. It signs in
 * (POST SIGN_IN_URL/auth/dev/login), builds the web tier's development session cookie in memory,
 * GETs PAGE_URL, and records when headers, each body chunk and each marker arrived. It decodes
 * gzip/br incrementally, so a marker is timed when its bytes land, not when the body completes.
 *
 *   PAGE_URL=https://…/ SIGN_IN_URL=https://… SIGN_IN_AS=planner ENCODING=gzip RUNS=3 \
 *   PAGE_LABEL=hosted [ORIGIN_KEY_FILE=…] node stream-probe.mjs > out.json
 *
 * Read-only apart from the development sign-in. Prints no cookie, token or key: the output holds
 * an allow-list of response headers, timings in ms from request start, and byte counts.
 */
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { performance } from "node:perf_hooks";
import zlib from "node:zlib";

const PAGE = new URL(process.env.PAGE_URL);
const SIGN_IN = (process.env.SIGN_IN_URL ?? PAGE.origin).replace(/\/+$/, "");
const AS = process.env.SIGN_IN_AS ?? "planner";
const ENCODING = process.env.ENCODING ?? "identity";
const RUNS = Number(process.env.RUNS ?? 3);
const KEY = process.env.ORIGIN_KEY_FILE ? readFileSync(process.env.ORIGIN_KEY_FILE, "utf8").trim() : null;
const UA = "adg-hosted-streaming-10-probe/1"; // Cloudflare refuses some default library agents
const HEADERS = ["content-type", "content-encoding", "transfer-encoding", "content-length", "cache-control", "cf-cache-status", "vary", "server", "x-nextjs-cache", "x-nextjs-prerender", "x-adg-runtime-mode", "age"];
// Ordered: what a reader sees first to last. `id="overview-…"` (closing quote) is the resolved
// section, never its placeholder (`overview-…-pending`).
const MARKERS = [
  ["title", "<title>"],
  ["h1", "<h1"],
  ["placeholder", 'aria-busy="true"'],
  ["attention", 'id="overview-attention"'],
  ["changes", 'id="overview-changes"'],
  ["health", 'id="overview-health"'],
  ["reviews", 'id="overview-reviews"'],
  ["html_end", "</html>"],
];

const extra = KEY ? { "x-adg-origin-key": KEY } : {};
const client = (url) => (url.protocol === "https:" ? https : http);

function send(url, { method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = client(url).request(url, { method, headers: { "user-agent": UA, ...extra, ...headers } }, resolve);
    req.on("error", reject);
    req.end(body);
  });
}

async function signIn() {
  const url = new URL(`${SIGN_IN}/auth/dev/login`);
  const res = await send(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: AS }) });
  const chunks = [];
  for await (const chunk of res) chunks.push(chunk);
  if (res.statusCode !== 200) throw new Error(`sign-in HTTP ${res.statusCode}`);
  const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const session = { accessToken: body.access_token, expiresAt: body.expires_at, mode: "development" };
  return `adg_session=${Buffer.from(JSON.stringify(session)).toString("base64url")}`;
}

async function probe(cookie) {
  const start = performance.now();
  const at = () => Math.round(performance.now() - start);
  const req = client(PAGE).request(PAGE, { headers: { "user-agent": UA, ...extra, cookie, "accept-encoding": ENCODING, accept: "text/html" } });
  const out = { headers_ms: null, status: null, alpn: null, headers: {}, chunks: [], markers: {}, complete_ms: null, wire_bytes: 0, decoded_bytes: 0 };
  req.on("socket", (socket) => socket.on("secureConnect", () => (out.alpn = socket.alpnProtocol || null)));
  await new Promise((resolve, reject) => {
    req.on("error", reject);
    req.on("response", (res) => {
      out.headers_ms = at();
      out.status = res.statusCode;
      for (const name of HEADERS) if (res.headers[name] !== undefined) out.headers[name] = res.headers[name];
      const enc = res.headers["content-encoding"];
      const decoder = enc === "gzip" ? zlib.createGunzip({ finishFlush: zlib.constants.Z_SYNC_FLUSH }) : enc === "br" ? zlib.createBrotliDecompress({ finishFlush: zlib.constants.BROTLI_OPERATION_FLUSH }) : null;
      let text = "";
      const look = (piece, t) => {
        out.decoded_bytes += piece.length;
        text += piece.toString("utf8");
        for (const [name, needle] of MARKERS) if (out.markers[name] === undefined && text.includes(needle)) out.markers[name] = t;
      };
      decoder?.on("data", (piece) => look(piece, at()));
      res.on("data", (chunk) => {
        out.wire_bytes += chunk.length;
        out.chunks.push([at(), chunk.length]);
        if (decoder) decoder.write(chunk);
        else look(chunk, at());
      });
      res.on("end", () => {
        out.complete_ms = at();
        // The decoder can still hold the last piece: markers in it count at its flush.
        if (decoder) decoder.end(), decoder.on("end", resolve);
        else resolve();
      });
    });
    req.end();
  });
  out.first_byte_ms = out.chunks[0]?.[0] ?? null;
  return out;
}

const cookie = await signIn();
const runs = [];
for (let i = 0; i < RUNS; i++) runs.push(await probe(cookie));
console.log(JSON.stringify({ page: `${process.env.PAGE_LABEL ?? "page"} ${PAGE.pathname}`, signed_in_as: AS, encoding: ENCODING, at: new Date().toISOString(), runs }, null, 1));
