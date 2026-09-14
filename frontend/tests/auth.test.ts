/**
 * Session handling and the OIDC handshake.
 *
 * The round trip through a real Entra tenant cannot be exercised here — there is no tenant —
 * so everything that does not need one is pinned instead: the authorize URL, the PKCE
 * challenge, the state comparison, the token request body, and the defensive decoding of a
 * cookie that may be stale, forged, or left over from an older build.
 */

import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  buildAuthorizationRequest,
  buildTokenRequestBody,
  codeChallenge,
  createCodeVerifier,
  expiryFrom,
  scopeString,
  stateMatches,
} from "@/lib/auth/oidc";
import { cookieMaxAge, decodeSession, encodeSession } from "@/lib/auth/session";

const NOW = new Date("2026-09-14T12:00:00.000Z");
const LATER = "2026-09-14T13:00:00.000Z";
const EARLIER = "2026-09-14T11:00:00.000Z";

describe("the session cookie", () => {
  it("round-trips a session", () => {
    const session = { accessToken: "a-token", expiresAt: LATER, mode: "development" as const };

    const state = decodeSession(encodeSession(session), NOW);

    expect(state.status).toBe("active");
    expect(state.status === "active" && state.session).toEqual(session);
  });

  it("reports no session rather than an error when the cookie is absent", () => {
    expect(decodeSession(undefined, NOW).status).toBe("absent");
  });

  it("treats an expired session as expired, not as active", () => {
    const encoded = encodeSession({ accessToken: "t", expiresAt: EARLIER, mode: "oidc" });

    expect(decodeSession(encoded, NOW).status).toBe("expired");
  });

  it("refuses a cookie that is not decodable rather than throwing", () => {
    // A cookie left over from an older build must sign the user out, not crash the shell.
    const state = decodeSession("not-base64-json", NOW);

    expect(state.status).toBe("malformed");
    expect(state.status === "malformed" && state.detail).toContain("not readable");
  });

  it("refuses a cookie carrying no token", () => {
    const encoded = Buffer.from(JSON.stringify({ expiresAt: LATER }), "utf-8").toString(
      "base64url",
    );

    expect(decodeSession(encoded, NOW).status).toBe("malformed");
  });

  it("refuses a cookie whose expiry is not a date", () => {
    const encoded = Buffer.from(
      JSON.stringify({ accessToken: "t", expiresAt: "whenever" }),
      "utf-8",
    ).toString("base64url");

    expect(decodeSession(encoded, NOW).status).toBe("malformed");
  });

  it("defaults an unrecognized mode to development rather than trusting it", () => {
    const encoded = Buffer.from(
      JSON.stringify({ accessToken: "t", expiresAt: LATER, mode: "whatever" }),
      "utf-8",
    ).toString("base64url");
    const state = decodeSession(encoded, NOW);

    expect(state.status === "active" && state.session.mode).toBe("development");
  });
});

describe("cookieMaxAge", () => {
  it("matches the token's remaining life", () => {
    expect(cookieMaxAge(LATER, NOW)).toBe(3600);
  });

  it("is zero, never negative, for a token that has already expired", () => {
    // A negative maxAge is a session cookie, which would survive the browser restart it is
    // supposed to be dead for.
    expect(cookieMaxAge(EARLIER, NOW)).toBe(0);
  });

  it("is zero for an unparseable expiry", () => {
    expect(cookieMaxAge("whenever", NOW)).toBe(0);
  });
});

describe("the authorization request", () => {
  const input = {
    authorizationEndpoint: "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize",
    clientId: "11111111-1111-1111-1111-111111111111",
    redirectUri: "https://adg.example.com/api/auth/oidc/callback",
    scopes: ["openid", "profile", "email"],
  };

  it("asks for a code with S256 PKCE", () => {
    const request = buildAuthorizationRequest(input);
    const url = new URL(request.url);

    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("client_id")).toBe(input.clientId);
    expect(url.searchParams.get("redirect_uri")).toBe(input.redirectUri);
  });

  it("sends the challenge, never the verifier", () => {
    const request = buildAuthorizationRequest(input);
    const url = new URL(request.url);

    expect(url.searchParams.get("code_challenge")).toBe(codeChallenge(request.codeVerifier));
    expect(request.url).not.toContain(request.codeVerifier);
  });

  it("computes the challenge as base64url(sha256(verifier))", () => {
    const verifier = "a-fixed-verifier-value-for-this-test";

    expect(codeChallenge(verifier)).toBe(
      createHash("sha256").update(verifier, "ascii").digest("base64url"),
    );
  });

  it("generates a different verifier every time", () => {
    expect(createCodeVerifier()).not.toBe(createCodeVerifier());
  });

  it("generates a different state every time", () => {
    expect(buildAuthorizationRequest(input).state).not.toBe(
      buildAuthorizationRequest(input).state,
    );
  });

  it("requests a token for ADG rather than for Microsoft Graph", () => {
    // Asking only for openid/profile/email yields a Graph token, which ADG's verifier
    // correctly rejects as the wrong audience — a failure that looks like a broken login.
    const request = buildAuthorizationRequest({ ...input, audience: input.clientId });
    const scope = new URL(request.url).searchParams.get("scope") ?? "";

    expect(scope).toContain(`${input.clientId}/.default`);
  });
});

describe("scopeString", () => {
  it("falls back to the standard OIDC scopes when none are configured", () => {
    expect(scopeString([])).toBe("openid profile email");
  });

  it("does not append /.default twice", () => {
    expect(scopeString(["openid"], "api://adg/.default")).toBe("openid api://adg/.default");
  });

  it("leaves the scope list alone when no audience is given", () => {
    expect(scopeString(["openid"], null)).toBe("openid");
  });
});

describe("stateMatches", () => {
  it("accepts the value that was stored", () => {
    expect(stateMatches("abc123", "abc123")).toBe(true);
  });

  it("rejects a different value", () => {
    expect(stateMatches("abc123", "abc124")).toBe(false);
  });

  it("rejects a missing stored value", () => {
    // Otherwise a callback with no prior request would be accepted, which is the whole
    // attack: a link that completes somebody else's sign-in in this browser.
    expect(stateMatches(undefined, "abc123")).toBe(false);
  });

  it("rejects a missing returned value", () => {
    expect(stateMatches("abc123", null)).toBe(false);
  });

  it("rejects a value of a different length without throwing", () => {
    expect(stateMatches("abc", "abcdef")).toBe(false);
  });
});

describe("the token request", () => {
  const input = {
    clientId: "client",
    code: "the-code",
    redirectUri: "https://adg.example.com/api/auth/oidc/callback",
    codeVerifier: "the-verifier",
    scopes: ["openid"],
  };

  it("sends the authorization code and the verifier", () => {
    const body = buildTokenRequestBody(input);

    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe("the-verifier");
  });

  it("omits the client secret for a public client", () => {
    expect(buildTokenRequestBody(input).has("client_secret")).toBe(false);
  });

  it("includes the client secret when one is configured", () => {
    const body = buildTokenRequestBody({ ...input, clientSecret: "s3cret" });

    expect(body.get("client_secret")).toBe("s3cret");
  });
});

describe("expiryFrom", () => {
  it("uses the provider's expires_in", () => {
    expect(expiryFrom({ access_token: "t", expires_in: 900 }, NOW)).toBe(
      "2026-09-14T12:15:00.000Z",
    );
  });

  it("falls back to an hour when the provider omits it", () => {
    expect(expiryFrom({ access_token: "t" }, NOW)).toBe("2026-09-14T13:00:00.000Z");
  });
});
