import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { isApiPath as workerIsApiPath } from "../../cloudflare/routing";
import {
  REFUSAL,
  apiEnvironment,
  createGate,
  forwardableHeaders,
  isApiPath,
  keyMatches,
  webEnvironment,
} from "../../render/gate";

const KEY = "k".repeat(48);
const PUBLIC_URL = "https://adg-demo-staging.example.workers.dev";

interface Seen {
  upstream: "api" | "web";
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

let seen: Seen[] = [];
const servers: http.Server[] = [];

async function listen(server: http.Server): Promise<number> {
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return (server.address() as AddressInfo).port;
}

/** A stand-in for FastAPI or Next.js: records each request, answers with a recognisable body. */
function upstream(name: "api" | "web"): http.Server {
  return http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      seen.push({ upstream: name, method: request.method ?? "", url: request.url ?? "", headers: request.headers, body });
      response.writeHead(200, [
        "Content-Type", "text/plain",
        "Set-Cookie", "a=1; Path=/",
        "Set-Cookie", "b=2; Path=/",
        "Keep-Alive", "timeout=77",
        "X-Upstream", name,
      ]);
      response.end(`${name}:${request.method} ${request.url}`);
    });
  });
}

let gatePort = 0;
let deadGatePort = 0;

beforeAll(async () => {
  const apiPort = await listen(upstream("api"));
  const webPort = await listen(upstream("web"));
  const quiet = () => undefined;
  gatePort = await listen(http.createServer(createGate({ key: KEY, apiPort, webPort, publicUrl: PUBLIC_URL, log: quiet })));
  // Nothing listens on port 9: the upstreams of this gate are down.
  deadGatePort = await listen(http.createServer(createGate({ key: KEY, apiPort: 9, webPort: 9, log: quiet })));
});

afterAll(async () => {
  await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))));
});

beforeEach(() => {
  seen = [];
});

interface Answer {
  status: number;
  headers: http.IncomingHttpHeaders;
  body: string;
}

/** A raw HTTP request, so hop-by-hop headers are sent exactly as given. */
function send(
  path: string,
  { method = "GET", headers = {}, body, port = gatePort }: { method?: string; headers?: Record<string, string | string[]>; body?: string; port?: number } = {},
): Promise<Answer> {
  return new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port, method, path, headers, agent: false }, (response) => {
      let text = "";
      response.on("data", (chunk) => (text += chunk));
      response.on("end", () => resolve({ status: response.statusCode ?? 0, headers: response.headers, body: text }));
    });
    request.on("error", reject);
    request.end(body);
  });
}

const keyed = (headers: Record<string, string | string[]> = {}) => ({ "X-ADG-Origin-Key": KEY, ...headers });

describe("the origin key", () => {
  it.each([
    ["no key", {}],
    ["a wrong key", { "X-ADG-Origin-Key": "x".repeat(64) }],
    ["the key minus a character", { "X-ADG-Origin-Key": KEY.slice(1) }],
    ["the key plus a character", { "X-ADG-Origin-Key": `${KEY}k` }],
    ["the key twice", { "X-ADG-Origin-Key": [KEY, KEY] }],
    ["an empty key", { "X-ADG-Origin-Key": "" }],
  ])("refuses %s, for pages and the API alike, and reaches nothing", async (_label, headers) => {
    for (const path of ["/", "/demo", "/runtime", "/api/v1/demo", "/auth/config", "/health/ready", "/api/adg/api/v1/demo", "/_next/static/a.js"]) {
      const answer = await send(path, { headers });
      expect(answer.status, path).toBe(403);
      expect(JSON.parse(answer.body)).toEqual({ detail: REFUSAL });
    }
    const signIn = await send("/auth/dev/login", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: '{"username":"admin"}' });
    expect(signIn.status).toBe(403);
    expect(seen).toEqual([]);
  });

  it("lets the keyed request through", async () => {
    const answer = await send("/runtime", { headers: keyed() });
    expect(answer.status).toBe(200);
    expect(answer.body).toBe("api:GET /runtime");
  });

  it("leaves only /health/live open, relayed as exactly that path", async () => {
    expect((await send("/health/live")).body).toBe("api:GET /health/live");
    expect((await send("/health/live?probe=1")).body).toBe("api:GET /health/live");
    for (const path of ["/health/live/", "/health/ready", "//health/live", "/health/live/../ready", "/HEALTH/LIVE"]) {
      expect((await send(path)).status, path).toBe(403);
    }
    expect(seen.map((s) => s.url)).toEqual(["/health/live", "/health/live"]);
  });

  it("refuses a request target that is not a path", async () => {
    expect((await send("http://evil.example/runtime", { headers: keyed() })).status).toBe(400);
    expect(seen).toEqual([]);
  });

  it("is compared through a digest: same answer for any length, and a repeated header is no key", () => {
    const digest = createHash("sha256").update(KEY).digest();
    expect(keyMatches(KEY, digest)).toBe(true);
    expect(keyMatches(KEY.slice(1), digest)).toBe(false);
    expect(keyMatches("", digest)).toBe(false);
    expect(keyMatches(undefined, digest)).toBe(false);
    expect(keyMatches([KEY], digest)).toBe(false);
  });

  it("must be at least 32 characters", () => {
    expect(() => createGate({ key: "short" })).toThrow(/ADG_ORIGIN_KEY/);
  });
});

describe("routing", () => {
  it.each(["/runtime", "/version", "/health/ready", "/auth/config", "/auth/me", "/api/v1/demo", "/api/v1/scan-runs"])(
    "sends %s to FastAPI",
    async (path) => {
      expect((await send(path, { headers: keyed() })).headers["x-upstream"]).toBe("api");
    },
  );

  it.each(["/", "/demo", "/risks", "/access/explain", "/api/adg/api/v1/demo", "/api/auth/dev-login", "/_next/static/chunks/a.js", "/runtime-extra", "/versions"])(
    "sends %s to Next.js",
    async (path) => {
      expect((await send(path, { headers: keyed() })).headers["x-upstream"]).toBe("web");
    },
  );

  it("classifies every path exactly as the Worker does", () => {
    for (const path of ["/", "/runtime", "/version", "/versions", "/health/live", "/healthz", "/auth/x", "/authority", "/api/v1/x", "/api/adg/x", "/api/v1", "/_next/static/a.js"]) {
      expect(isApiPath(path), path).toBe(workerIsApiPath(path));
    }
  });
});

describe("relaying", () => {
  it("keeps method, path, query and body, and answers with the upstream's status, headers and body", async () => {
    const answer = await send("/api/v1/simulations?page=2&kind=a&kind=b", {
      method: "POST",
      headers: keyed({ "Content-Type": "application/json", Authorization: "Bearer t" }),
      body: '{"x":1}',
    });
    expect(seen[0]).toMatchObject({ upstream: "api", method: "POST", url: "/api/v1/simulations?page=2&kind=a&kind=b", body: '{"x":1}' });
    expect(seen[0].headers.authorization).toBe("Bearer t");
    expect(answer.status).toBe(200);
    expect(answer.headers["set-cookie"]).toEqual(["a=1; Path=/", "b=2; Path=/"]);
    expect(answer.body).toBe("api:POST /api/v1/simulations?page=2&kind=a&kind=b");
  });

  it("never passes the origin key on, to either process", async () => {
    await send("/runtime", { headers: keyed() });
    await send("/demo", { headers: keyed() });
    expect(seen.map((s) => s.headers["x-adg-origin-key"])).toEqual([undefined, undefined]);
  });

  it("gives Next.js the session cookie and FastAPI none", async () => {
    await send("/demo", { headers: keyed({ Cookie: "adg_session=s" }) });
    await send("/auth/me", { headers: keyed({ Cookie: "adg_session=s" }) });
    expect(seen.map((s) => [s.upstream, s.headers.cookie])).toEqual([
      ["web", "adg_session=s"],
      ["api", undefined],
    ]);
  });

  it("tells Next.js the public host, whatever the request claimed", async () => {
    await send("/governance", { headers: keyed({ "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http" }) });
    expect(seen[0].headers["x-forwarded-host"]).toBe("adg-demo-staging.example.workers.dev");
    expect(seen[0].headers["x-forwarded-proto"]).toBe("https");
  });

  it("forwards no hop-by-hop header, in either direction", async () => {
    const answer = await send("/demo", {
      headers: keyed({ Connection: "keep-alive, X-Private", "X-Private": "p", "Keep-Alive": "timeout=9", "Proxy-Authorization": "Basic x", TE: "trailers" }),
    });
    for (const name of ["x-private", "keep-alive", "proxy-authorization", "te"]) {
      expect(seen[0].headers[name], name).toBeUndefined();
    }
    // The gate's own connection may carry Keep-Alive; the upstream's never does.
    expect(answer.headers["keep-alive"]).not.toBe("timeout=77");
  });

  it("answers 502 when a process does not answer", async () => {
    const answer = await send("/demo", { port: deadGatePort, headers: keyed() });
    expect(answer.status).toBe(502);
    expect(JSON.parse(answer.body).detail).toMatch(/did not answer/);
  });
});

describe("forwardableHeaders", () => {
  it("drops hop-by-hop headers, the headers Connection names, and the names given", () => {
    expect(
      forwardableHeaders(
        ["Host", "h", "Connection", "close, X-A", "X-A", "1", "Transfer-Encoding", "chunked", "Upgrade", "websocket", "Cookie", "c", "X-B", "2"],
        new Set(["cookie"]),
      ),
    ).toEqual(["Host", "h", "X-B", "2"]);
  });
});

describe("child environments", () => {
  const service = {
    PATH: "/usr/bin",
    HOME: "/home/adg",
    ADG_ORIGIN_KEY: KEY,
    ADG_DATABASE_URL: "postgresql+psycopg://u:p@db/adg_demo",
    ADG_DEV_AUTH_SECRET: "s".repeat(64),
    ADG_RUNTIME_MODE: "demo",
    ADG_WEB_URL: PUBLIC_URL,
  };

  it("gives FastAPI the service's environment without the origin key", () => {
    const env = apiEnvironment(service);
    expect(env.ADG_ORIGIN_KEY).toBeUndefined();
    expect(env.ADG_DATABASE_URL).toBe(service.ADG_DATABASE_URL);
    expect(env.ADG_RUNTIME_MODE).toBe("demo");
  });

  it("gives Next.js only what it reads: no key, no database, no session secret", () => {
    expect(webEnvironment(service)).toEqual({
      PATH: "/usr/bin",
      HOME: "/home/adg",
      NODE_ENV: "production",
      NEXT_TELEMETRY_DISABLED: "1",
      HOSTNAME: "127.0.0.1",
      PORT: "3000",
      ADG_INTERNAL_API_URL: "http://127.0.0.1:8000",
      ADG_WEB_URL: PUBLIC_URL,
      NEXT_PUBLIC_ADG_API_URL: PUBLIC_URL,
    });
  });
});

describe("startup", () => {
  it("does not start, start a process or open a port without the key", () => {
    const gate = fileURLToPath(new URL("../../render/gate.ts", import.meta.url));
    for (const key of [undefined, "", "short"]) {
      const result = spawnSync(process.execPath, ["--disable-warning=ExperimentalWarning", gate], {
        env: { NODE_ENV: "production", PATH: process.env.PATH, PORT: "0", ...(key === undefined ? {} : { ADG_ORIGIN_KEY: key }) },
        encoding: "utf8",
        timeout: 20_000,
      });
      expect(result.status).toBe(1);
      expect(result.stderr).toContain("ADG_ORIGIN_KEY is not set");
      expect(result.stdout).not.toContain("adg.gate.starting");
    }
  });
});
