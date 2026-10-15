// Slow-origin proxy for the React #418 gate (ADG-NEXT16-POST-DEMO-INTEGRATION-01), version 2.
// Sits between the browser and `next start` and recreates the hosted timing ADG-UI-EDGE-01 §5
// names as the cause of #418: the JavaScript arrives fast (the edge) while the streamed HTML and
// RSC rows arrive slowly (the origin). /_next/static/* passes straight through. Every other
// response is requested uncompressed (so its bytes, not gzip framing, set the pace) and then
// delivered in one of two modes:
//
//   MODE=rate   SLICE_BYTES every SLICE_MS, whatever the upstream chunking. Two builds sending
//               the same bytes get the same timing, so a difference in #418 cannot come from
//               one build flushing more chunks than the other.
//   MODE=chunk  each upstream chunk held CHUNK_MS before it is written (a slow origin flush;
//               the condition ADG-NEXT16-POST-DEMO-INTEGRATION-01 on claude/beautiful-knuth
//               used, reimplemented here).
//
//   LISTEN=3200 TARGET=http://127.0.0.1:3000 MODE=rate SLICE_BYTES=8192 SLICE_MS=20 node stream-proxy-v2.js
//   LISTEN=3300 TARGET=http://127.0.0.1:3000 MODE=chunk CHUNK_MS=40 node stream-proxy-v2.js
const http = require("http");

const LISTEN = Number(process.env.LISTEN);
const TARGET = new URL(process.env.TARGET);
const MODE = process.env.MODE || "rate";
const SLICE_BYTES = Number(process.env.SLICE_BYTES || 8192);
const SLICE_MS = Number(process.env.SLICE_MS || 20);
const CHUNK_MS = Number(process.env.CHUNK_MS || 40);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

http
  .createServer((req, res) => {
    const fast = req.url.startsWith("/_next/static/");
    const headers = { ...req.headers, host: TARGET.host };
    if (!fast) headers["accept-encoding"] = "identity";
    const upstream = http.request(
      { hostname: TARGET.hostname, port: TARGET.port, path: req.url, method: req.method, headers },
      (up) => {
        const out = { ...up.headers };
        if (out.location) out.location = out.location.replace(`//${TARGET.host}`, `//localhost:${LISTEN}`);
        res.writeHead(up.statusCode, out);
        if (fast) return up.pipe(res);
        const queue = [];
        let ended = false;
        let wake = null;
        up.on("data", (c) => { queue.push(c); if (wake) wake(); });
        up.on("end", () => { ended = true; if (wake) wake(); });
        (async () => {
          let pending = Buffer.alloc(0);
          while (true) {
            if (queue.length === 0 && pending.length === 0) {
              if (ended) break;
              await new Promise((r) => { wake = r; });
              wake = null;
              continue;
            }
            if (MODE === "chunk") {
              await sleep(CHUNK_MS);
              res.write(queue.shift());
              continue;
            }
            while (queue.length) pending = Buffer.concat([pending, queue.shift()]);
            if (pending.length < SLICE_BYTES && !ended) {
              // wait for a full slice unless the response is complete
              await Promise.race([new Promise((r) => { wake = r; }), sleep(SLICE_MS)]);
              wake = null;
              while (queue.length) pending = Buffer.concat([pending, queue.shift()]);
            }
            const slice = pending.subarray(0, SLICE_BYTES);
            pending = pending.subarray(slice.length);
            await sleep(SLICE_MS);
            res.write(slice);
          }
          res.end();
        })();
      },
    );
    upstream.on("error", (e) => { if (!res.headersSent) res.writeHead(502); res.end(String(e)); });
    req.pipe(upstream);
  })
  .listen(LISTEN, "127.0.0.1", () =>
    console.log(`slow-origin proxy :${LISTEN} -> ${TARGET.origin}, ${MODE === "rate" ? `${SLICE_BYTES} B every ${SLICE_MS} ms` : `${CHUNK_MS} ms per chunk`}; /_next/static/ unthrottled`),
  );
