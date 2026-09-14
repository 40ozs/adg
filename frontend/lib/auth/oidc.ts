/**
 * The OIDC authorization-code flow with PKCE, as pure functions.
 *
 * ADG's production identity provider is Microsoft Entra ID. The redirect itself cannot be
 * exercised without a tenant, so everything that *can* be checked without one lives here
 * and is tested: the authorize URL, the PKCE challenge, the state comparison, and the token
 * request body. What remains untested is the round trip through a real tenant — stated
 * plainly in `docs/handoffs/phase-06a-frontend-shell.md` rather than implied to work.
 *
 * PKCE (RFC 7636) is used even though this client has a secret available server-side,
 * because it costs nothing and closes the code-interception window. `state` is checked
 * against the value stored in a short-lived httpOnly cookie, which is what stops an
 * attacker completing a sign-in on somebody else's browser.
 */

import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

export const OIDC_STATE_COOKIE = "adg_oidc_state";
export const OIDC_VERIFIER_COOKIE = "adg_oidc_verifier";

/** Long enough that guessing is hopeless; RFC 7636 requires 43-128 characters. */
const VERIFIER_BYTES = 48;

export interface AuthorizationRequest {
  url: string;
  state: string;
  codeVerifier: string;
}

export interface AuthorizeInput {
  authorizationEndpoint: string;
  clientId: string;
  redirectUri: string;
  scopes: string[];
  /** The API's audience, so Entra issues a token ADG can verify rather than a Graph token. */
  audience?: string | null;
  state?: string;
  codeVerifier?: string;
}

export function createCodeVerifier(): string {
  return randomBytes(VERIFIER_BYTES).toString("base64url");
}

export function createState(): string {
  return randomBytes(32).toString("base64url");
}

export function codeChallenge(verifier: string): string {
  return createHash("sha256").update(verifier, "ascii").digest("base64url");
}

/**
 * Build the URL the browser is redirected to, plus the two values the callback needs back.
 */
export function buildAuthorizationRequest(input: AuthorizeInput): AuthorizationRequest {
  const state = input.state ?? createState();
  const codeVerifier = input.codeVerifier ?? createCodeVerifier();

  const url = new URL(input.authorizationEndpoint);
  url.searchParams.set("client_id", input.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("redirect_uri", input.redirectUri);
  url.searchParams.set("response_mode", "query");
  url.searchParams.set("scope", scopeString(input.scopes, input.audience));
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", codeChallenge(codeVerifier));
  url.searchParams.set("code_challenge_method", "S256");

  return { url: url.toString(), state, codeVerifier };
}

/**
 * The scope string to request.
 *
 * Entra issues an access token for whichever resource the scopes name. Asking only for
 * `openid profile email` yields a token for Microsoft Graph, which ADG's verifier correctly
 * rejects as the wrong audience — a confusing failure that looks like a broken login. The
 * API's own scope is appended so the token is issued for ADG.
 */
export function scopeString(scopes: string[], audience?: string | null): string {
  const requested = scopes.length > 0 ? [...scopes] : ["openid", "profile", "email"];
  if (audience) {
    const apiScope = audience.endsWith("/.default") ? audience : `${audience}/.default`;
    if (!requested.includes(apiScope)) {
      requested.push(apiScope);
    }
  }
  return requested.join(" ");
}

/**
 * Compare the returned state with the stored one, in constant time.
 *
 * Rejecting a mismatch is what stops somebody handing a victim a link that completes the
 * attacker's sign-in in the victim's browser.
 */
export function stateMatches(expected: string | undefined, received: string | null): boolean {
  if (!expected || !received) {
    return false;
  }
  const a = Buffer.from(expected, "utf-8");
  const b = Buffer.from(received, "utf-8");
  if (a.length !== b.length) {
    return false;
  }
  return timingSafeEqual(a, b);
}

export interface TokenExchangeInput {
  clientId: string;
  code: string;
  redirectUri: string;
  codeVerifier: string;
  clientSecret?: string | null;
  scopes: string[];
  audience?: string | null;
}

/** The form body for the token request. Returned rather than posted so it can be asserted. */
export function buildTokenRequestBody(input: TokenExchangeInput): URLSearchParams {
  const body = new URLSearchParams({
    client_id: input.clientId,
    grant_type: "authorization_code",
    code: input.code,
    redirect_uri: input.redirectUri,
    code_verifier: input.codeVerifier,
    scope: scopeString(input.scopes, input.audience),
  });
  if (input.clientSecret) {
    body.set("client_secret", input.clientSecret);
  }
  return body;
}

export interface TokenResponse {
  access_token: string;
  expires_in?: number;
  token_type?: string;
}

/** The instant a token expires, from the `expires_in` the provider returned. */
export function expiryFrom(response: TokenResponse, now: Date = new Date()): string {
  // Providers may omit expires_in. An hour is the conventional default and is shorter than
  // any real token's life, so the session is refreshed early rather than used after death.
  const seconds = typeof response.expires_in === "number" ? response.expires_in : 3600;
  return new Date(now.getTime() + seconds * 1000).toISOString();
}
