/**
 * The backend-for-frontend proxy.
 *
 * Browser code calls `/api/adg/<path>` on this origin; this handler attaches the session's
 * access token and forwards to the ADG API. The token therefore never exists in the browser,
 * and the browser never needs a cross-origin request — so no CORS, and no token for an
 * injected script to steal.
 *
 * It is a proxy, not a gateway. It adds authentication and nothing else: it makes no
 * authorization decision, rewrites no response, and hides no status code. The API's 403 is
 * forwarded as a 403 with its own message, because that message names the capability the
 * account is missing.
 *
 * Only GET is proxied. Every write in ADG today is collector ingestion, which authenticates
 * with a collector key from a Windows host and has no business coming through a browser
 * session; forwarding POST here would make the browser a route into ingestion that the
 * threat model does not include.
 */

import { NextResponse } from "next/server";

import { apiRequest } from "@/lib/api/client";
import { proxyTarget } from "@/lib/api/proxy";
import { currentSession } from "@/lib/auth/current";

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await context.params;
  const joined = proxyTarget(path);

  if (joined === null) {
    return NextResponse.json(
      { detail: `This application does not proxy /${path.join("/")}.` },
      { status: 404 },
    );
  }

  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }

  const query = Object.fromEntries(new URL(request.url).searchParams.entries());
  const result = await apiRequest<unknown>(`/${joined}`, {
    token: session.accessToken,
    query,
  });

  if (result.ok) {
    return NextResponse.json(result.data);
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
