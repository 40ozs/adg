/**
 * Reading the current session and principal inside a server component or route handler.
 *
 * `/auth/me` is called on each render rather than trusting anything stored in the cookie.
 * The cookie holds a token, not a claim about what that token may do: a role revoked in
 * Entra has to take effect on the next page load, not when a cached capability list
 * happens to expire.
 */

import "server-only";

import { cookies } from "next/headers";

import { fetchPrincipal } from "@/lib/api/adg";
import type { ApiFailure } from "@/lib/api/client";
import type { Principal } from "@/lib/contracts";
import { SESSION_COOKIE, decodeSession, type Session, type SessionState } from "@/lib/auth/session";

export async function currentSessionState(): Promise<SessionState> {
  const store = await cookies();
  return decodeSession(store.get(SESSION_COOKIE)?.value);
}

export async function currentSession(): Promise<Session | null> {
  const state = await currentSessionState();
  return state.status === "active" ? state.session : null;
}

export type Viewer =
  | { status: "signed-in"; session: Session; principal: Principal }
  | { status: "signed-out"; reason: "no-session" | "expired" | "rejected"; detail?: string }
  | { status: "unavailable"; failure: ApiFailure };

/**
 * Who is looking at this page.
 *
 * Three outcomes, not two. "Signed out" and "the API is down" must not be the same screen:
 * bouncing somebody to a login page because the database is unreachable sends them round a
 * loop that signing in cannot break.
 */
export async function currentViewer(): Promise<Viewer> {
  const state = await currentSessionState();
  if (state.status === "absent") {
    return { status: "signed-out", reason: "no-session" };
  }
  if (state.status === "expired") {
    return { status: "signed-out", reason: "expired" };
  }
  if (state.status === "malformed") {
    return { status: "signed-out", reason: "rejected", detail: state.detail };
  }

  const result = await fetchPrincipal(state.session.accessToken);
  if (result.ok) {
    return { status: "signed-in", session: state.session, principal: result.data };
  }
  if (result.failure.kind === "unauthenticated") {
    return { status: "signed-out", reason: "rejected", detail: result.failure.detail };
  }
  return { status: "unavailable", failure: result.failure };
}
