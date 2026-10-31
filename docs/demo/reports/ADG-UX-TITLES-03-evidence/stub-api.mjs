// Stub ADG API for the title check (from ADG-UX-01's nav stub): answers development sign-in
// and /auth/me, refuses everything else, so every page renders its error state.
import http from "node:http";
const VIEWER = ["resources:read", "identities:read", "access:read", "risks:read", "changes:read", "collectors:read", "search"];
const ALL = [...VIEWER, "settings:read", "simulations:read", "governance:read"];
const ACCOUNTS = ["admin", "viewer"];
const json = (res, status, body) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
const me = (who) => ({ subject: who, display_name: who, email: null, source: "development", development: true,
  roles: [who], capabilities: who === "admin" ? ALL : VIEWER, inactive_roles: [], unrecognized_roles: [], expires_at: null });
http.createServer((req, res) => {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  if (req.url === "/auth/config") {
    return json(res, 200, { mode: "development", development: true, environment: "development", issuer: null, client_id: null,
      authorization_endpoint: null, token_endpoint: null, scopes: [], development_accounts: ACCOUNTS, roles: [] });
  }
  if (req.url === "/auth/dev/login" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    return req.on("end", () => {
      const who = JSON.parse(body || "{}").username;
      if (!ACCOUNTS.includes(who)) return json(res, 404, { detail: "unknown account" });
      json(res, 200, { access_token: who, token_type: "Bearer", expires_at: "2099-01-01T00:00:00Z", principal: me(who), warning: "stub" });
    });
  }
  if (req.url === "/auth/me" && ACCOUNTS.includes(token)) return json(res, 200, me(token));
  json(res, 503, { detail: "stub" });
}).listen(18077, "127.0.0.1");
