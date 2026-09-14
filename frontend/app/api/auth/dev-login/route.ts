/**
 * DEVELOPMENT ONLY: exchange an account name for a session.
 *
 * This route holds no authority of its own. It asks the API to issue a development token
 * and stores it in the httpOnly session cookie; if the deployment is not in development
 * mode the API has no such endpoint and answers 404, which is what this route returns.
 * There is no path here that produces a session the backend would not have produced.
 */

import { NextResponse } from "next/server";

import { developmentLogin } from "@/lib/api/adg";
import { SESSION_COOKIE, SESSION_COOKIE_OPTIONS, cookieMaxAge, encodeSession } from "@/lib/auth/session";
import { secureCookiesEnabled } from "@/lib/config";

export async function POST(request: Request): Promise<NextResponse> {
  let username: unknown;
  try {
    username = ((await request.json()) as { username?: unknown }).username;
  } catch {
    return NextResponse.json({ error: "Send a JSON body with a username." }, { status: 400 });
  }

  if (typeof username !== "string" || username.trim() === "") {
    return NextResponse.json({ error: "Choose an account to sign in as." }, { status: 400 });
  }

  const result = await developmentLogin(username.trim());
  if (!result.ok) {
    return NextResponse.json(
      { error: result.failure.detail ?? result.failure.message },
      { status: result.failure.status ?? 502 },
    );
  }

  const response = NextResponse.json({ principal: result.data.principal });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: encodeSession({
      accessToken: result.data.access_token,
      expiresAt: result.data.expires_at,
      mode: "development",
    }),
    ...SESSION_COOKIE_OPTIONS,
    secure: secureCookiesEnabled(),
    // Matched to the token's own life. A cookie that outlives its token produces a UI that
    // looks signed in and fails every request.
    maxAge: cookieMaxAge(result.data.expires_at),
  });
  return response;
}
