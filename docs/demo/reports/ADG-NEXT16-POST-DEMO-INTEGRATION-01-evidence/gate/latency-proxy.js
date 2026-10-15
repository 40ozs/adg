// Latency proxy for the React #418 gate (ADG-NEXT16-POST-DEMO-INTEGRATION-01).
// Sits between a `next start` server (ADG_INTERNAL_API_URL) and the ADG API, and holds every
// request for DELAY_MS before forwarding it. Slower server data makes the page segment stream
// in after the shell and the JavaScript, which is the race behind React #418
// (docs/handoffs/adg-ui-edge-01.md section 5 on integration/ADG-UI-EDGE-01).
//
//   LISTEN=8100 TARGET=http://127.0.0.1:8000 DELAY_MS=100 node latency-proxy.js
const http = require("http");

const LISTEN = Number(process.env.LISTEN || 8100);
const TARGET = new URL(process.env.TARGET || "http://127.0.0.1:8000");
const DELAY_MS = Number(process.env.DELAY_MS || 0);

http
  .createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      setTimeout(() => {
        const upstream = http.request(
          {
            hostname: TARGET.hostname,
            port: TARGET.port,
            path: req.url,
            method: req.method,
            headers: { ...req.headers, host: TARGET.host },
          },
          (up) => {
            res.writeHead(up.statusCode, up.headers);
            up.pipe(res);
          },
        );
        upstream.on("error", (e) => {
          res.writeHead(502);
          res.end(String(e));
        });
        upstream.end(Buffer.concat(chunks));
      }, DELAY_MS);
    });
  })
  .listen(LISTEN, "127.0.0.1", () =>
    console.log(`latency proxy :${LISTEN} -> ${TARGET.origin}, ${DELAY_MS} ms`),
  );
