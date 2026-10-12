import { beforeEach, describe, expect, it } from "vitest";

import { clearAccessCertificateCache } from "../../cloudflare/access";
import { StagingConfigurationError, apiUpstream } from "../../cloudflare/environment";
import { type RoutingDependencies, forwardToApi, isApiPath, routeRequest } from "../../cloudflare/routing";
import { AccessSigner } from "./signer";

const TEAM = "https://adg-staging.cloudflareaccess.com";
const AUD = "aud-tag";
const ORIGIN = "https://adg-demo-staging.example.workers.dev";

let signer: AccessSigner;
let token: string;

beforeEach(async () => {
  clearAccessCertificateCache();
  signer = await AccessSigner.create("k");
  const now = Math.floor(Date.now() / 1000);
  token = await signer.sign({ aud: [AUD], iss: TEAM, email: "r@example.com", exp: now + 600, nbf: now - 5 });
});

function harness(overrides: Partial<RoutingDependencies> = {}) {
  const seen = { api: [] as Request[], frontend: [] as Request[], logs: [] as Record<string, unknown>[] };
  const dependencies: RoutingDependencies = {
    access: { teamDomain: TEAM, audience: AUD },
    api: async (request) => {
      seen.api.push(request);
      return new Response("api");
    },
    frontend: async (request) => {
      seen.frontend.push(request);
      return new Response("web");
    },
    fetchCertificates: signer.fetcher(),
    log: (entry) => seen.logs.push(entry),
    ...overrides,
  };
  return { seen, dependencies };
}

function request(path: string, init: RequestInit & { assertion?: string | null } = {}): Request {
  const headers = new Headers(init.headers);
  const assertion = init.assertion === undefined ? token : init.assertion;
  if (assertion !== null) {
    headers.set("Cf-Access-Jwt-Assertion", assertion);
  }
  return new Request(`${ORIGIN}${path}`, { ...init, headers });
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
  it("refuses everything with 503 when Access is not configured, reaching neither tier", async () => {
    const { seen, dependencies } = harness({ access: null });
    for (const path of ["/", "/runtime", "/_next/static/a.js"]) {
      const response = await routeRequest(request(path), dependencies);
      expect(response.status).toBe(503);
    }
    expect(seen.api).toHaveLength(0);
    expect(seen.frontend).toHaveLength(0);
  });

  it("refuses a request without an Access assertion", async () => {
    const { seen, dependencies } = harness();
    const response = await routeRequest(request("/demo", { assertion: null }), dependencies);
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Access denied." });
    expect(seen.frontend).toHaveLength(0);
  });

  it("refuses a forged assertion", async () => {
    const forger = await AccessSigner.create("k");
    const forged = await forger.sign({ aud: [AUD], iss: TEAM, exp: Math.floor(Date.now() / 1000) + 60 });
    const { seen, dependencies } = harness();
    expect((await routeRequest(request("/runtime", { assertion: forged }), dependencies)).status).toBe(403);
    expect(seen.api).toHaveLength(0);
  });

  it("routes API paths to the API and everything else to Next.js", async () => {
    const { seen, dependencies } = harness();
    expect(await (await routeRequest(request("/runtime"), dependencies)).text()).toBe("api");
    expect(await (await routeRequest(request("/demo"), dependencies)).text()).toBe("web");
    expect(seen.api.map((r) => new URL(r.url).pathname)).toEqual(["/runtime"]);
    expect(seen.frontend.map((r) => new URL(r.url).pathname)).toEqual(["/demo"]);
  });

  it("does not forward the Access assertion to either tier", async () => {
    const { seen, dependencies } = harness();
    await routeRequest(request("/runtime"), dependencies);
    await routeRequest(request("/"), dependencies);
    expect(seen.api[0].headers.get("Cf-Access-Jwt-Assertion")).toBeNull();
    expect(seen.frontend[0].headers.get("Cf-Access-Jwt-Assertion")).toBeNull();
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

  it("forwards method, body and authorization, and nothing meant for Cloudflare or the web tier", async () => {
    const { sent, fetcher } = capture();
    await forwardToApi(
      new Request(`${ORIGIN}/api/v1/scan-runs`, {
        method: "POST",
        body: '{"x":1}',
        headers: {
          Authorization: "Bearer api-token",
          Cookie: "CF_Authorization=access-jwt; adg_session=s",
          "CF-Access-Client-Id": "id",
          "CF-Access-Client-Secret": "service-secret",
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
    expect(sent[0].headers.get("CF-Access-Client-Id")).toBeNull();
    expect(sent[0].headers.get("CF-Access-Client-Secret")).toBeNull();
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
});
