/**
 * The browser session.
 *
 * The access token never reaches the browser. It lives in an httpOnly cookie that only the
 * Next.js server can read, and every call to the ADG API is made server-side with that
 * token attached — see `app/api/adg/[...path]/route.ts`. A token in `localStorage` or in a
 * readable cookie is a token any injected script can take, and this token is a key to a map
 * of every weak permission in the estate.
 *
 * None of this is a security boundary on its own. The backend verifies the token on every
 * request; the session only decides which token gets sent.
 */

export const SESSION_COOKIE = "adg_session";

/** Set in production; omitted over plain http so local development works. */
export const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax",
  path: "/",
} as const;

export interface Session {
  accessToken: string;
  /** ISO-8601. Used to expire the session locally; the API re-checks it regardless. */
  expiresAt: string;
  mode: "oidc" | "development";
}

/** A parsed session, or the reason there is none. */
export type SessionState =
  | { status: "active"; session: Session }
  | { status: "absent" }
  | { status: "expired" }
  | { status: "malformed"; detail: string };

export function encodeSession(session: Session): string {
  return Buffer.from(JSON.stringify(session), "utf-8").toString("base64url");
}

/**
 * Read a session cookie.
 *
 * The cookie is signed by nothing and is not trusted: forging one only means presenting a
 * token the API will reject. It is decoded defensively all the same, because a cookie left
 * over from an older version of this app must log the user out rather than crash the shell.
 */
export function decodeSession(raw: string | undefined, now: Date = new Date()): SessionState {
  if (!raw) {
    return { status: "absent" };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(raw, "base64url").toString("utf-8"));
  } catch {
    return { status: "malformed", detail: "The session cookie is not readable." };
  }

  if (typeof parsed !== "object" || parsed === null) {
    return { status: "malformed", detail: "The session cookie is not an object." };
  }

  const candidate = parsed as Partial<Session>;
  if (typeof candidate.accessToken !== "string" || candidate.accessToken.length === 0) {
    return { status: "malformed", detail: "The session cookie carries no access token." };
  }
  if (typeof candidate.expiresAt !== "string") {
    return { status: "malformed", detail: "The session cookie carries no expiry." };
  }

  const expiry = Date.parse(candidate.expiresAt);
  if (Number.isNaN(expiry)) {
    return { status: "malformed", detail: "The session cookie's expiry is not a date." };
  }
  if (expiry <= now.getTime()) {
    return { status: "expired" };
  }

  return {
    status: "active",
    session: {
      accessToken: candidate.accessToken,
      expiresAt: candidate.expiresAt,
      mode: candidate.mode === "oidc" ? "oidc" : "development",
    },
  };
}

/**
 * Cookie lifetime in seconds, never negative and never longer than the token itself.
 *
 * A cookie that outlives its token produces the worst failure mode there is: a UI that
 * looks signed in and fails every request.
 */
export function cookieMaxAge(expiresAt: string, now: Date = new Date()): number {
  const seconds = Math.floor((Date.parse(expiresAt) - now.getTime()) / 1000);
  return Number.isNaN(seconds) || seconds < 0 ? 0 : seconds;
}
