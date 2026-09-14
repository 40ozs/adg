/**
 * Complete the OIDC authorization-code flow.
 *
 * The order of checks is the security of this route:
 *
 * 1. A provider-reported error is surfaced as itself rather than as a missing code.
 * 2. `state` is compared with the stored value in constant time. A mismatch ends the flow —
 *    this is what stops somebody handing a victim a link that completes the attacker's
 *    sign-in in the victim's browser.
 * 3. Only then is the code exchanged, with the PKCE verifier.
 *
 * The handshake cookies are cleared on every path, success or failure, so a stale verifier
 * cannot be reused.
 */

import { NextResponse } from "next/server";

import { fetchAuthConfig } from "@/lib/api/adg";
import {
  OIDC_STATE_COOKIE,
  OIDC_VERIFIER_COOKIE,
  buildTokenRequestBody,
  expiryFrom,
  stateMatches,
  type TokenResponse,
} from "@/lib/auth/oidc";
import {
  SESSION_COOKIE,
  SESSION_COOKIE_OPTIONS,
  cookieMaxAge,
  encodeSession,
} from "@/lib/auth/session";
import { resolveAppUrl, resolveClientSecret, resolveRedirectUri, secureCookiesEnabled } from "@/lib/config";

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const providerError = url.searchParams.get("error");
  if (providerError) {
    return failed(
      providerError,
      url.searchParams.get("error_description") ?? "The identity provider refused the sign-in.",
    );
  }

  const code = url.searchParams.get("code");
  if (!code) {
    return failed("no_code", "The identity provider returned no authorization code.");
  }

  const cookieHeader = request.headers.get("cookie") ?? "";
  const expectedState = readCookie(cookieHeader, OIDC_STATE_COOKIE);
  const verifier = readCookie(cookieHeader, OIDC_VERIFIER_COOKIE);

  if (!stateMatches(expectedState, url.searchParams.get("state"))) {
    return failed(
      "state_mismatch",
      "This sign-in did not start in this browser, or it took too long. Start again.",
    );
  }
  if (!verifier) {
    return failed("no_verifier", "The sign-in took too long. Start again.");
  }

  const config = await fetchAuthConfig();
  if (!config.ok) {
    return failed("api_unreachable", config.failure.message);
  }
  const { token_endpoint: tokenEndpoint, client_id: clientId } = config.data;
  if (!tokenEndpoint || !clientId) {
    return failed(
      "not_configured",
      "The API publishes no token endpoint or client id; set ADG_OIDC_TOKEN_ENDPOINT.",
    );
  }

  let tokenResponse: Response;
  try {
    tokenResponse = await fetch(tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: buildTokenRequestBody({
        clientId,
        code,
        redirectUri: resolveRedirectUri(),
        codeVerifier: verifier,
        clientSecret: resolveClientSecret(),
        scopes: config.data.scopes,
        audience: clientId,
      }),
      cache: "no-store",
    });
  } catch (error) {
    return failed(
      "token_endpoint_unreachable",
      error instanceof Error ? error.message : String(error),
    );
  }

  if (!tokenResponse.ok) {
    // The provider's own error text is the only useful diagnostic here, and it names the
    // misconfiguration (wrong redirect URI, missing consent) that an operator must fix.
    const detail = await tokenResponse.text();
    return failed("token_exchange_failed", detail.slice(0, 500));
  }

  const token = (await tokenResponse.json()) as TokenResponse;
  if (!token.access_token) {
    return failed("no_access_token", "The token response carried no access token.");
  }

  const expiresAt = expiryFrom(token);
  const response = NextResponse.redirect(resolveAppUrl());
  response.cookies.set({
    name: SESSION_COOKIE,
    value: encodeSession({ accessToken: token.access_token, expiresAt, mode: "oidc" }),
    ...SESSION_COOKIE_OPTIONS,
    secure: secureCookiesEnabled(),
    maxAge: cookieMaxAge(expiresAt),
  });
  return clearHandshake(response);
}

function failed(code: string, detail: string): NextResponse {
  const destination = new URL("/login", resolveAppUrl());
  destination.searchParams.set("error", code);
  destination.searchParams.set("detail", detail);
  return clearHandshake(NextResponse.redirect(destination));
}

function clearHandshake(response: NextResponse): NextResponse {
  for (const name of [OIDC_STATE_COOKIE, OIDC_VERIFIER_COOKIE]) {
    response.cookies.set({
      name,
      value: "",
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      secure: secureCookiesEnabled(),
      maxAge: 0,
    });
  }
  return response;
}

function readCookie(header: string, name: string): string | undefined {
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return decodeURIComponent(rest.join("="));
    }
  }
  return undefined;
}
