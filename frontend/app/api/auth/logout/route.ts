/**
 * End the session.
 *
 * Clears the cookie and nothing else. The access token stays valid at the identity provider
 * until it expires — ADG cannot revoke a tenant's token and does not pretend to. Signing out
 * of the tenant itself is the provider's own end-session endpoint, which a deployment can
 * link from here once it is configured.
 */

import { NextResponse } from "next/server";

import { SESSION_COOKIE, SESSION_COOKIE_OPTIONS } from "@/lib/auth/session";
import { secureCookiesEnabled } from "@/lib/config";

export async function POST(): Promise<NextResponse> {
  const response = NextResponse.json({ signedOut: true });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    ...SESSION_COOKIE_OPTIONS,
    secure: secureCookiesEnabled(),
    maxAge: 0,
  });
  return response;
}
