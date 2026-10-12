import { describe, expect, it } from "vitest";

import { StagingConfigurationError, apiUpstream } from "../../cloudflare/environment";
import { type RoutingDependencies, forwardToApi, isApiPath, routeRequest } from "../../cloudflare/routing";

const ORIGIN = "https://adg-demo-staging.example.workers.dev";

function harness(overrides: Partial<RoutingDependencies> = {}) {
  const seen = { api: [] as Request[], frontend: [] as Request[], logs: [] as Record<string, unknown>[] };
  const dependencies: RoutingDependencies = {
    api: async (request) => {
      seen.api.push(request);
      return new Response("api");
    },
    frontend: async (request) => {
      seen.frontend.push(request);
      return new Response("web");
    },
    log: (entry) => seen.logs.push(entry),
    ...overrides,
  };
  return { seen, dependencies };
}

function request(path: string, init: RequestInit = {}): Request {
  return new Request(`${ORIGIN}${path}`, init);
}

describe("isApiPath", () => {
  it.each(["/runtime", "/version", "/health/live", "/health/ready", "/auth/config", "/auth/me", "/api/v1/demo", "/api/v1/scan-runs"])(
    "sends %s to the API",
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
  ])("sends %s to the web application", (path) => expect(isApiPath(path)).toBe(false));
});

describe("routeRequest", () => {
  it("serves a visitor with no credential of any kind: the deployment is public", async () => {
    const { seen, dependencies } = harness();
    for (const path of ["/", "/demo", "/_next/static/a.js", "/runtime"]) {
      expect((await routeRequest(request(path), dependencies)).status).toBe(200);
    }
    expect(seen.frontend).toHaveLength(3);
    expect(seen.api).toHaveLength(1);
  });

  it("routes API paths to the API and everything else to Next.js", async () => {
    const { seen, dependencies } = harness();
    expect(await (await routeRequest(request("/runtime"), dependencies)).text()).toBe("api");
    expect(await (await routeRequest(request("/demo"), dependencies)).text()).toBe("web");
    expect(seen.api.map((r) => new URL(r.url).pathname)).toEqual(["/runtime"]);
    expect(seen.frontend.map((r) => new URL(r.url).pathname)).toEqual(["/demo"]);
  });

  it("forwards method and body unchanged, so the API's own demo guard decides", async () => {
    const { seen, dependencies } = harness();
    await routeRequest(request("/api/v1/scan-runs", { method: "POST", body: '{"x":1}' }), dependencies);
    expect(seen.api[0].method).toBe("POST");
    expect(await seen.api[0].text()).toBe('{"x":1}');
  });

  it("answers a misconfigured deployment with a named 503, never a value", async () => {
    const { seen, dependencies } = harness({
      api: async () => {
        apiUpstream({ ADG_API_ORIGIN: "https://adg.onrender.com", ADG_ORIGIN_KEY: "hunter2" });
        return new Response("unreachable");
      },
    });
    const response = await routeRequest(request("/runtime"), dependencies);
    const body = await response.text();
    expect(response.status).toBe(503);
    expect(body).toContain("ADG_ORIGIN_KEY");
    expect(body).not.toContain("hunter2");
    expect(JSON.stringify(seen.logs)).not.toContain("hunter2");
  });

  it("answers 502 when the API does not respond", async () => {
    const { seen, dependencies } = harness({
      api: async () => {
        throw new Error("connection refused");
      },
    });
    expect((await routeRequest(request("/runtime"), dependencies)).status).toBe(502);
    expect(seen.logs.at(-1)).toMatchObject({ event: "adg.staging.api_unavailable" });
  });

  it("is the StagingConfigurationError that names the 503", () => {
    expect(() => apiUpstream({})).toThrow(StagingConfigurationError);
  });
});

describe("forwardToApi", () => {
  const UPSTREAM = { origin: "https://adg-demo-api.onrender.com", key: "o".repeat(40) };
  const demo = (body = "{}", status = 200) =>
    new Response(body, { status, headers: { "X-ADG-Runtime-Mode": "demo" } });

  function capture(answer: () => Response = () => demo()) {
    const sent: Request[] = [];
    return { sent, fetcher: async (r: Request) => (sent.push(r), answer()) };
  }

  it("sends the path and query to the API origin with the origin key", async () => {
    const { sent, fetcher } = capture();
    await forwardToApi(new Request(`${ORIGIN}/api/v1/demo?x=1&y=2`), UPSTREAM, fetcher);
    expect(sent[0].url).toBe("https://adg-demo-api.onrender.com/api/v1/demo?x=1&y=2");
    expect(sent[0].headers.get("X-ADG-Origin-Key")).toBe(UPSTREAM.key);
  });

  it("forwards method, body and authorization, and nothing meant for the web tier", async () => {
    const { sent, fetcher } = capture();
    await forwardToApi(
      new Request(`${ORIGIN}/api/v1/scan-runs`, {
        method: "POST",
        body: '{"x":1}',
        headers: {
          Authorization: "Bearer api-token",
          Cookie: "adg_session=s",
          "X-ADG-Origin-Key": "a-caller-guess",
        },
      }),
      UPSTREAM,
      fetcher,
    );
    expect(sent[0].method).toBe("POST");
    expect(await sent[0].text()).toBe('{"x":1}');
    expect(sent[0].headers.get("Authorization")).toBe("Bearer api-token");
    expect(sent[0].headers.get("Cookie")).toBeNull();
    expect(sent[0].headers.get("X-ADG-Origin-Key")).toBe(UPSTREAM.key);
  });

  it("relays a demo answer, refusals included, unchanged", async () => {
    const { fetcher } = capture(() => demo('{"status":"demo_only"}', 403));
    const response = await forwardToApi(new Request(`${ORIGIN}/api/v1/scan-runs`), UPSTREAM, fetcher);
    expect(response.status).toBe(403);
    expect(await response.text()).toBe('{"status":"demo_only"}');
  });

  it.each([
    ["a production API", () => new Response("{}", { headers: { "X-ADG-Runtime-Mode": "production" } })],
    ["an unmarked answer (a host's own page, the origin gate's refusal)", () => new Response("<html>waking up</html>")],
  ])("refuses to relay %s", async (_label, answer) => {
    const { fetcher } = capture(answer);
    await expect(forwardToApi(new Request(`${ORIGIN}/runtime`), UPSTREAM, fetcher)).rejects.toThrow(
      /without X-ADG-Runtime-Mode: demo/,
    );
  });

  it("gives a visitor's own X-ADG-Origin-Key no say: the Worker's key replaces it on the whole path", async () => {
    for (const guess of ["a-caller-guess", UPSTREAM.key.slice(1), ""]) {
      const { sent, fetcher } = capture();
      const { dependencies } = harness({ api: (forwarded) => forwardToApi(forwarded, UPSTREAM, fetcher) });
      await routeRequest(request("/auth/dev/login", { method: "POST", headers: { "X-ADG-Origin-Key": guess } }), dependencies);
      expect(sent).toHaveLength(1);
      expect(sent[0].headers.get("X-ADG-Origin-Key")).toBe(UPSTREAM.key);
    }
  });

  it("does not reach the API at all when the Worker has no key of its own", async () => {
    const { sent, fetcher } = capture();
    const { seen, dependencies } = harness({
      api: (forwarded) => forwardToApi(forwarded, apiUpstream({ ADG_API_ORIGIN: UPSTREAM.origin }), fetcher),
    });
    const response = await routeRequest(
      request("/runtime", { headers: { "X-ADG-Origin-Key": UPSTREAM.key } }),
      dependencies,
    );
    expect(response.status).toBe(503);
    expect(sent).toHaveLength(0);
    expect(JSON.stringify(seen.logs)).not.toContain(UPSTREAM.key);
  });
});
