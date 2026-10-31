// Stub ADG API for a nav-only browser check: answers /auth/me, refuses everything else.
import http from "node:http";
const VIEWER = ["resources:read", "identities:read", "access:read", "risks:read", "changes:read", "collectors:read", "search"];
const ALL = [...VIEWER, "settings:read", "simulations:read", "governance:read"];
http.createServer((req, res) => {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  if (req.url === "/auth/me" && (token === "viewer" || token === "admin")) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ subject: token, display_name: token, email: null, source: "development",
      development: true, roles: [token], capabilities: token === "admin" ? ALL : VIEWER,
      inactive_roles: [], unrecognized_roles: [], expires_at: null }));
  }
  res.writeHead(503, { "content-type": "application/json" });
  res.end(JSON.stringify({ detail: "stub" }));
}).listen(18077, "127.0.0.1");
