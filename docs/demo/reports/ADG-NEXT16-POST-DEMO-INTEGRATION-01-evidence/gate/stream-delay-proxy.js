// Stream-delay proxy for the React #418 gate (ADG-NEXT16-POST-DEMO-INTEGRATION-01).
// Sits between the browser and `next start`. Static JavaScript and CSS (/_next/static/*) pass
// through at once; every other response (HTML documents, RSC payloads, route handlers) is
// forwarded with its headers and first chunk at once, then each later chunk CHUNK_DELAY_MS
// after the one before. That recreates the hosted timing ADG-UI-EDGE-01 §5 names as the cause:
// JavaScript arrives fast while the streamed HTML and RSC rows arrive late, so hydration starts
// with the document still loading.
//
//   LISTEN=3100 TARGET=http://127.0.0.1:3000 CHUNK_DELAY_MS=100 node stream-delay-proxy.js
const http = require("http");

const LISTEN = Number(process.env.LISTEN || 3100);
const TARGET = new URL(process.env.TARGET || "http://127.0.0.1:3000");
const DELAY = Number(process.env.CHUNK_DELAY_MS || 100);

http
  .createServer((req, res) => {
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
        if (req.url.startsWith("/_next/static/")) {
          up.pipe(res);
          return;
        }
        let chain = Promise.resolve();
        let first = true;
        up.on("data", (chunk) => {
          const wait = first ? 0 : DELAY;
          first = false;
          chain = chain.then(() => new Promise((r) => setTimeout(() => { res.write(chunk); r(); }, wait)));
        });
        up.on("end", () => { chain = chain.then(() => res.end()); });
      },
    );
    upstream.on("error", (e) => {
      if (!res.headersSent) res.writeHead(502);
      res.end(String(e));
    });
    req.pipe(upstream);
  })
  .listen(LISTEN, "127.0.0.1", () =>
    console.log(`stream-delay proxy :${LISTEN} -> ${TARGET.origin}, ${DELAY} ms between chunks`),
  );
