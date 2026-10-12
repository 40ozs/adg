import { beforeEach, describe, expect, it } from "vitest";

import {
  type AccessConfiguration,
  accessConfiguration,
  clearAccessCertificateCache,
  verifyAccess,
} from "../../cloudflare/access";
import { AccessSigner } from "./signer";

const TEAM = "https://adg-staging.cloudflareaccess.com";
const AUD = "aud-tag-0123456789abcdef";
const CONFIG: AccessConfiguration = { teamDomain: TEAM, audience: AUD };
const NOW = Date.UTC(2026, 9, 12, 12, 0, 0);
const SECONDS = Math.floor(NOW / 1000);

function withAssertion(token: string | null): Request {
  const headers = new Headers();
  if (token !== null) {
    headers.set("Cf-Access-Jwt-Assertion", token);
  }
  return new Request("https://adg-demo-staging.example.workers.dev/demo", { headers });
}

let signer: AccessSigner;

beforeEach(async () => {
  clearAccessCertificateCache();
  signer = await AccessSigner.create("key-1");
});

function claims(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    aud: [AUD],
    iss: TEAM,
    email: "reviewer@example.com",
    exp: SECONDS + 600,
    iat: SECONDS - 10,
    nbf: SECONDS - 10,
    ...overrides,
  };
}

describe("accessConfiguration", () => {
  it("is null — so every request is refused — until both settings are present", () => {
    expect(accessConfiguration({})).toBeNull();
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: TEAM })).toBeNull();
    expect(accessConfiguration({ ACCESS_AUD: AUD })).toBeNull();
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: " ", ACCESS_AUD: " " })).toBeNull();
  });

  it("normalizes the team domain to its origin", () => {
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: `${TEAM}/`, ACCESS_AUD: AUD })).toEqual(CONFIG);
  });

  it("refuses a non-https team domain unless it is loopback (the local rehearsal)", () => {
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: "http://adg.cloudflareaccess.com", ACCESS_AUD: AUD })).toBeNull();
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: "not a url", ACCESS_AUD: AUD })).toBeNull();
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: `${TEAM}/path`, ACCESS_AUD: AUD })).toBeNull();
    expect(accessConfiguration({ ACCESS_TEAM_DOMAIN: "http://127.0.0.1:8788", ACCESS_AUD: AUD })).toEqual({
      teamDomain: "http://127.0.0.1:8788",
      audience: AUD,
    });
  });
});

describe("verifyAccess", () => {
  it("fails closed with 503 when Access is not configured", async () => {
    const verdict = await verifyAccess(withAssertion(await signer.sign(claims())), null, signer.fetcher(), NOW);
    expect(verdict).toMatchObject({ ok: false, status: 503 });
  });

  it("refuses a request with no assertion", async () => {
    expect(await verifyAccess(withAssertion(null), CONFIG, signer.fetcher(), NOW)).toMatchObject({
      ok: false,
      status: 401,
    });
  });

  it("admits a person's assertion signed for this application", async () => {
    const verdict = await verifyAccess(withAssertion(await signer.sign(claims())), CONFIG, signer.fetcher(), NOW);
    expect(verdict).toEqual({ ok: true, identity: "reviewer@example.com" });
  });

  it("admits a service token's assertion (the hosted smoke checks use one)", async () => {
    const token = await signer.sign(claims({ email: undefined, common_name: "abc.access", aud: AUD }));
    const verdict = await verifyAccess(withAssertion(token), CONFIG, signer.fetcher(), NOW);
    expect(verdict).toEqual({ ok: true, identity: "service-token:abc.access" });
  });

  it.each([
    ["another application's audience", { aud: ["someone-else"] }],
    ["another issuer", { iss: "https://evil.cloudflareaccess.com" }],
    ["an expired assertion", { exp: SECONDS - 3600 }],
    ["an assertion without exp", { exp: undefined }],
    ["an assertion not yet valid", { nbf: SECONDS + 3600 }],
  ])("refuses %s", async (_label, overrides) => {
    const token = await signer.sign(claims(overrides));
    expect(await verifyAccess(withAssertion(token), CONFIG, signer.fetcher(), NOW)).toMatchObject({
      ok: false,
      status: 403,
    });
  });

  it("refuses an assertion signed by a key Access did not publish", async () => {
    const forger = await AccessSigner.create("key-1");
    const token = await forger.sign(claims());
    expect(await verifyAccess(withAssertion(token), CONFIG, signer.fetcher(), NOW)).toMatchObject({
      ok: false,
      status: 403,
    });
  });

  it("refuses a tampered payload", async () => {
    const [header, , signature] = (await signer.sign(claims())).split(".");
    const forged = Buffer.from(JSON.stringify(claims({ email: "admin@example.com" }))).toString("base64url");
    expect(
      await verifyAccess(withAssertion(`${header}.${forged}.${signature}`), CONFIG, signer.fetcher(), NOW),
    ).toMatchObject({ ok: false, status: 403 });
  });

  it.each([
    ["alg none", { alg: "none", kid: "key-1" }],
    ["HS256", { alg: "HS256", kid: "key-1" }],
    ["no key id", { alg: "RS256" }],
  ])("refuses a header with %s", async (_label, header) => {
    const token = await signer.sign(claims(), header);
    expect(await verifyAccess(withAssertion(token), CONFIG, signer.fetcher(), NOW)).toMatchObject({
      ok: false,
      status: 403,
    });
  });

  it("refuses garbage", async () => {
    for (const token of ["x", "a.b", "a.b.c", "....."]) {
      expect(await verifyAccess(withAssertion(token), CONFIG, signer.fetcher(), NOW)).toMatchObject({
        ok: false,
        status: 403,
      });
    }
  });

  it("refetches the certificates once when Access has rotated its key", async () => {
    let calls = 0;
    const old = await AccessSigner.create("key-old");
    const fetcher = async (url: string) => {
      calls += 1;
      return (calls === 1 ? old : signer).fetcher()(url);
    };
    const verdict = await verifyAccess(withAssertion(await signer.sign(claims())), CONFIG, fetcher, NOW);
    expect(verdict.ok).toBe(true);
    expect(calls).toBe(2);
  });

  it("answers 503 when the certificates cannot be fetched", async () => {
    const verdict = await verifyAccess(
      withAssertion(await signer.sign(claims())),
      CONFIG,
      async () => new Response("down", { status: 502 }),
      NOW,
    );
    expect(verdict).toMatchObject({ ok: false, status: 503 });
  });

  it("fetches the team's published certificate URL", async () => {
    const seen: string[] = [];
    await verifyAccess(
      withAssertion(await signer.sign(claims())),
      CONFIG,
      async (url) => {
        seen.push(url);
        return signer.fetcher()(url);
      },
      NOW,
    );
    expect(seen).toEqual([`${TEAM}/cdn-cgi/access/certs`]);
  });
});
