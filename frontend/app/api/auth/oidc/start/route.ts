/**
 * Begin the OIDC authorization-code flow.
 *
 * The state and PKCE verifier are stored in short-lived httpOnly cookies and compared in
 * the callback. They are httpOnly because a script that can read the verifier can complete
 * the exchange, and `sameSite: lax` because the browser arrives back from the provider on a
 * cross-site GET — `strict` would drop both cookies at exactly the moment they are needed.
 *
 * Never reachable in development mode: the API reports `development` and this route refuses
 * rather than building a request against endpoints nobody configured.
 */

import { NextResponse } from "next/server";

import { fetchAuthConfig } from "@/lib/api/adg";
import {
  OIDC_STATE_COOKIE,
  OIDC_VERIFIER_COOKIE,
  buildAuthorizationRequest,
} from "@/lib/auth/oidc";
import { resolveRedirectUri, secureCookiesEnabled } from "@/lib/config";

/** Five minutes is longer than any honest sign-in and shorter than any useful replay. */
const HANDSHAKE_SECONDS = 300;

export async function GET(): Promise<NextResponse> {
  const config = await fetchAuthConfig();
  if (!config.ok) {
    return NextResponse.json(
      { error: `Could not read the authentication configuration: ${config.failure.message}` },
      { status: 502 },
    );
  }

  if (config.data.mode !== "oidc") {
    return NextResponse.json(
      {
        error:
          "This deployment uses development authentication. Sign in at /login; there is no " +
          "identity provider to redirect to.",
      },
      { status: 409 },
    );
  }

  const { authorization_endpoint: endpoint, client_id: clientId } = config.data;
  if (!endpoint || !clientId) {
    return NextResponse.json(
      {
        error:
          "The API is in OIDC mode but publishes no authorization endpoint or client id. " +
          "Set ADG_OIDC_AUTHORIZATION_ENDPOINT and ADG_OIDC_CLIENT_ID on the API.",
      },
      { status: 500 },
    );
  }

  const request = buildAuthorizationRequest({
    authorizationEndpoint: endpoint,
    clientId,
    redirectUri: resolveRedirectUri(),
    scopes: config.data.scopes,
    audience: config.data.issuer ? clientId : null,
  });

  const response = NextResponse.redirect(request.url);
  const options = {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: secureCookiesEnabled(),
    maxAge: HANDSHAKE_SECONDS,
  } as const;
  response.cookies.set({ name: OIDC_STATE_COOKIE, value: request.state, ...options });
  response.cookies.set({ name: OIDC_VERIFIER_COOKIE, value: request.codeVerifier, ...options });
  return response;
}
