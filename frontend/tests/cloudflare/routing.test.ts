import { describe, expect, it } from "vitest";

import { type OriginUpstream, StagingConfigurationError, originUpstream } from "../../cloudflare/environment";
import { forwardToOrigin, isApiPath, routeRequest } from "../../cloudflare/routing";

const PUBLIC = "https://adg-demo-staging.example.workers.dev";
const UPSTREAM: OriginUpstream = { origin: "https://adg-demo-web.onrender.com", key: "o".repeat(40) };

const demo = (body = "{}", status = 200) =>
  new Response(body, { status, headers: { "X-ADG-Runtime-Mode": "demo" } });
const page = (body = "<html>page</html>") => new Response(body, { headers: { "Content-Type": "text/html" } });

function capture(answer: (request: Request) => Response = (r) => (isApiPath(new URL(r.url).pathname) ? demo() : page())) {
  const sent: Request[] = [];
  return { sent, fetch: async (request: Request) => (sent.push(request), answer(request)) };
}

function request(path: string, init: RequestInit = {}): Request {
  return new Request(`${PUBLIC}${path}`, init);
}

describe("isApiPath", () => {
  it.each(["/runtime", "/version", "/health/live", "/health/ready", "/auth/config", "/auth/me", "/api/v1/demo", "/api/v1/scan-runs"])(
    "%s is the API's",
    (path) => expect(isApiPath(path)).toBe(true),
  );

  it.each([
    "/",
    "/demo",
    "/login",
    "/risks",
    "/resources/directory",
    "/identities/principal/S-1-5-21",
    "/access/explain",
    "/simulations",
    "/status",
    "/api/adg/api/v1/demo",
    "/api/auth/dev-login",
    "/api/simulations/preview",
    "/api/watches",
    "/_next/static/chunk.js",
    "/runtime-extra",
    "/versions",
    "/healthz",
    "/authority",
  ])("%s is the web application's", (path) => expect(isApiPath(path)).toBe(false));
});

describe("routeRequest", () => {
  it("serves a visitor with no credential of any kind: the deployment is public", async () => {
    const { sent, fetch } = capture();
    for (const path of ["/", "/demo", "/_next/static/a.js", "/runtime"]) {
      expect((await routeRequest(request(path), { upstream: () => UPSTREAM, fetch })).status).toBe(200);
    }
    expect(sent.map((r) => r.url)).toEqual([
      "https://adg-demo-web.onrender.com/",
      "https://adg-demo-web.onrender.com/demo",
      "https://adg-demo-web.onrender.com/_next/static/a.js",
      "https://adg-demo-web.onrender.com/runtime",
    ]);
  });

  it("answers a misconfigured deployment with a named 503 and reaches nothing", async () => {
    const { sent, fetch } = capture();
    const logs: Record<string, unknown>[] = [];
    const response = await routeRequest(request("/demo"), {
      upstream: () => originUpstream({ ADG_RENDER_ORIGIN: UPSTREAM.origin, ADG_ORIGIN_KEY: "hunter2" }),
      fetch,
      log: (entry) => logs.push(entry),
    });
    const body = await response.text();
    expect(response.status).toBe(503);
    expect(body).toContain("ADG_ORIGIN_KEY");
    expect(body).not.toContain("hunter2");
    expect(JSON.stringify(logs)).not.toContain("hunter2");
    expect(sent).toHaveLength(0);
  });

  it("answers 502 when the origin does not respond", async () => {
    const logs: Record<string, unknown>[] = [];
    const response = await routeRequest(request("/demo"), {
      upstream: () => UPSTREAM,
      fetch: async () => {
        throw new Error("connection refused");
      },
      log: (entry) => logs.push(entry),
    });
    expect(response.status).toBe(502);
    expect(logs.at(-1)).toMatchObject({ event: "adg.staging.origin_unavailable", path: "/demo" });
  });

  it("is the StagingConfigurationError that names the 503", () => {
    expect(() => originUpstream({})).toThrow(StagingConfigurationError);
  });
});

describe("forwardToOrigin", () => {
  it("sends path and query to the origin with the origin key", async () => {
    const { sent, fetch } = capture();
    await forwardToOrigin(request("/access/explain?principal=S-1&resource=r%2F1"), UPSTREAM, fetch);
    expect(sent[0].url).toBe("https://adg-demo-web.onrender.com/access/explain?principal=S-1&resource=r%2F1");
    expect(sent[0].headers.get("X-ADG-Origin-Key")).toBe(UPSTREAM.key);
    expect(sent[0].redirect).toBe("manual");
  });

  it("forwards method, body, cookies and authorization unchanged", async () => {
    const { sent, fetch } = capture();
    await forwardToOrigin(
      request("/api/auth/dev-login", {
        method: "POST",
        body: '{"username":"planner"}',
        headers: { Cookie: "adg_session=s", Authorization: "Bearer t", "Content-Type": "application/json" },
      }),
      UPSTREAM,
      fetch,
    );
    expect(sent[0].method).toBe("POST");
    expect(await sent[0].text()).toBe('{"username":"planner"}');
    expect(sent[0].headers.get("Cookie")).toBe("adg_session=s");
    expect(sent[0].headers.get("Authorization")).toBe("Bearer t");
    expect(sent[0].headers.get("Content-Type")).toBe("application/json");
  });

  it("forwards no hop-by-hop header, nor one the Connection header names", async () => {
    const { sent, fetch } = capture();
    const headers = new Headers({ Connection: "x-private", "X-Private": "p", "Keep-Alive": "timeout=9", "Proxy-Authorization": "Basic x" });
    // Node's `Request` drops some of these names itself; the Workers runtime keeps them, so the
    // Worker must remove them. A stand-in with the headers intact is what reaches the code.
    const incoming = { url: `${PUBLIC}/demo`, method: "GET", headers, signal: new AbortController().signal } as Request;
    await forwardToOrigin(incoming, UPSTREAM, fetch);
    for (const name of ["connection", "x-private", "keep-alive", "proxy-authorization"]) {
      expect(sent[0].headers.get(name), name).toBeNull();
    }
  });

  it("relays the origin's answer unchanged: status, cookies, body", async () => {
    const answer = new Response("<html>signed in</html>", { status: 200, headers: { "Set-Cookie": "adg_session=s; HttpOnly" } });
    const { fetch } = capture(() => answer);
    expect(await forwardToOrigin(request("/demo"), UPSTREAM, fetch)).toBe(answer);
  });

  it("relays an API demo answer, refusals included, unchanged", async () => {
    const { fetch } = capture(() => demo('{"status":"demo_only"}', 403));
    const response = await forwardToOrigin(request("/api/v1/scan-runs", { method: "POST", body: "{}" }), UPSTREAM, fetch);
    expect(response.status).toBe(403);
    expect(await response.text()).toBe('{"status":"demo_only"}');
  });

  it.each([
    ["a production API", () => new Response("{}", { headers: { "X-ADG-Runtime-Mode": "production" } })],
    ["an unmarked answer (a host's own page, the origin gate's refusal)", () => new Response("<html>waking up</html>")],
  ])("refuses to relay %s on an API path", async (_label, answer) => {
    const { fetch } = capture(answer);
    await expect(forwardToOrigin(request("/runtime"), UPSTREAM, fetch)).rejects.toThrow(/without X-ADG-Runtime-Mode: demo/);
  });

  it("gives a visitor's own X-ADG-Origin-Key no say: the Worker's key replaces it", async () => {
    for (const path of ["/auth/dev/login", "/demo"]) {
      for (const guess of ["a-caller-guess", UPSTREAM.key.slice(1), UPSTREAM.key, ""]) {
        const { sent, fetch } = capture();
        await routeRequest(request(path, { method: "POST", headers: { "X-ADG-Origin-Key": guess } }), { upstream: () => UPSTREAM, fetch });
        expect(sent).toHaveLength(1);
        expect(sent[0].headers.get("X-ADG-Origin-Key")).toBe(UPSTREAM.key);
      }
    }
  });

  it("does not reach the origin at all when the Worker has no key of its own", async () => {
    const { sent, fetch } = capture();
    const logs: Record<string, unknown>[] = [];
    const response = await routeRequest(request("/runtime", { headers: { "X-ADG-Origin-Key": UPSTREAM.key } }), {
      upstream: () => originUpstream({ ADG_RENDER_ORIGIN: UPSTREAM.origin }),
      fetch,
      log: (entry) => logs.push(entry),
    });
    expect(response.status).toBe(503);
    expect(sent).toHaveLength(0);
    expect(JSON.stringify(logs)).not.toContain(UPSTREAM.key);
  });
});
