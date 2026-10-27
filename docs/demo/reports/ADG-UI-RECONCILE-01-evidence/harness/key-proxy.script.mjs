// Local stand-in for the Worker: adds X-ADG-Origin-Key and forwards to the gate. Loopback only.
import http from "node:http";
import fs from "node:fs";
const KEY = fs.readFileSync(process.argv[2], "utf8").trim();
const UPSTREAM = { host: "127.0.0.1", port: 10077 };
http.createServer((req, res) => {
  const headers = { ...req.headers, "x-adg-origin-key": KEY, "x-forwarded-proto": "http" };
  const up = http.request({ ...UPSTREAM, method: req.method, path: req.url, headers }, (r) => {
    res.writeHead(r.statusCode, r.rawHeaders);
    r.pipe(res);
  });
  up.on("error", () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  res.on("close", () => up.destroy());
  req.pipe(up);
}).listen(8787, "127.0.0.1", () => console.log("key-proxy listening 127.0.0.1:8787"));
