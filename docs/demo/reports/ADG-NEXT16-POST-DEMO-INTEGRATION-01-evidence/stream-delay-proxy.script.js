// A reverse proxy that reproduces, on one machine, the timing ADG-UI-EDGE-01 found behind React
// #418: the JavaScript arrives fast (the Worker served it from the edge) while the streamed
// HTML and RSC rows arrive slowly (Render, behind the Worker). Static chunks under
// /_next/static pass straight through; every other response is forwarded one chunk at a time,
// each chunk held for STREAM_DELAY_MS. Kept as evidence; not part of the application.
//
//   node stream-delay-proxy.script.js <listen port> <target origin> <delay ms>
//   node stream-delay-proxy.script.js 3100 http://localhost:3000 40
const http = require("http");

const [listen, target, delay] = [Number(process.argv[2]), new URL(process.argv[3]), Number(process.argv[4])];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

http
  .createServer((req, res) => {
    const fast = req.url.startsWith("/_next/static/");
    const headers = { ...req.headers, host: target.host };
    if (!fast) headers["accept-encoding"] = "identity"; // uncompressed, so the stream keeps its own chunking
    const upstream = http.request({ host: target.hostname, port: target.port, method: req.method, path: req.url, headers }, async (up) => {
      const out = { ...up.headers };
      if (out.location) out.location = out.location.replace(`//${target.host}`, `//localhost:${listen}`);
      res.writeHead(up.statusCode, out);
      if (fast) return up.pipe(res);
      up.pause();
      const chunks = [];
      let ended = false;
      let wake = null;
      up.on("data", (c) => { chunks.push(c); if (wake) wake(); });
      up.on("end", () => { ended = true; if (wake) wake(); });
      up.resume();
      while (true) {
        if (chunks.length === 0) {
          if (ended) break;
          await new Promise((resolve) => { wake = resolve; });
          wake = null;
          continue;
        }
        await sleep(delay);
        res.write(chunks.shift());
      }
      res.end();
    });
    upstream.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    req.pipe(upstream);
  })
  .listen(listen, () => console.log(`stream-delay proxy :${listen} -> ${target.origin}, ${delay} ms per chunk; /_next/static/ unthrottled`));
