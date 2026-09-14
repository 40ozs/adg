/**
 * The typed client for the ADG API. Server-side only.
 *
 * Two rules shape this module.
 *
 * **A call either returns data or returns a described failure.** There is no third case and
 * nothing throws past the caller. A page that renders a blank table because a fetch threw
 * and something upstream swallowed it is precisely how an auditor concludes a share has no
 * risky permissions.
 *
 * **Failures are classified, not stringified.** "You are not signed in", "you may not see
 * this", "the API is down" and "nothing is there" lead to four different screens, so the
 * classification happens once, here, and the views branch on a tagged union.
 */

import "server-only";

import { resolveServerApiUrl } from "@/lib/config";

/** Why a request did not produce data. */
export type FailureKind =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "invalid_request"
  | "unreachable"
  | "server_error";

export interface ApiFailure {
  kind: FailureKind;
  status: number | null;
  /** Written for the person looking at the screen. */
  message: string;
  /** The API's own detail, when it sent one worth showing.  */
  detail?: string;
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; failure: ApiFailure };

export interface RequestOptions {
  /** The session's access token, or null for an unauthenticated call such as /auth/config. */
  token?: string | null;
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined | null>;
  /** Milliseconds. A hung API must not hang the page. */
  timeoutMs?: number;
  signal?: AbortSignal;
}

const DEFAULT_TIMEOUT_MS = 10_000;

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const base = resolveServerApiUrl();
  const url = new URL(`${base}${path}`);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      // Permission data is never cached: a stale answer about who can reach payroll is
      // worse than a slow one.
      cache: "no-store",
      signal: options.signal ?? AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    return {
      ok: false,
      failure: {
        kind: "unreachable",
        status: null,
        message: `The ADG API at ${base} did not respond.`,
        detail: error instanceof Error ? error.message : String(error),
      },
    };
  }

  if (response.ok) {
    try {
      return { ok: true, data: (await response.json()) as T };
    } catch (error) {
      return {
        ok: false,
        failure: {
          kind: "server_error",
          status: response.status,
          message: "The ADG API returned a response this application could not read.",
          detail: error instanceof Error ? error.message : String(error),
        },
      };
    }
  }

  return { ok: false, failure: await describeFailure(response) };
}

async function describeFailure(response: Response): Promise<ApiFailure> {
  const detail = await readDetail(response);

  switch (response.status) {
    case 401:
      return {
        kind: "unauthenticated",
        status: 401,
        message: "Your session has ended. Sign in again to continue.",
        detail,
      };
    case 403:
      // The API's own message names the missing capability and the roles held, which is
      // exactly what the person needs to forward to an administrator.
      return {
        kind: "forbidden",
        status: 403,
        message: detail ?? "Your account does not have access to this.",
        detail,
      };
    case 404:
      return {
        kind: "not_found",
        status: 404,
        message: "ADG holds nothing under that identifier.",
        detail,
      };
    case 422:
      return {
        kind: "invalid_request",
        status: 422,
        message: detail ?? "That request could not be understood.",
        detail,
      };
    case 503:
      return {
        kind: "unreachable",
        status: 503,
        message: "The ADG API is running but cannot reach its database.",
        detail,
      };
    default:
      return {
        kind: response.status >= 500 ? "server_error" : "invalid_request",
        status: response.status,
        message: `The ADG API returned ${response.status}.`,
        detail,
      };
  }
}

/**
 * The `detail` from an error body, whatever shape it took.
 *
 * FastAPI sends a string for an `HTTPException`, an object for a domain validation error,
 * and a list for a request-validation error. All three are rendered rather than dropped:
 * these messages are written for the operator and are the most useful thing on the screen.
 */
async function readDetail(response: Response): Promise<string | undefined> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return undefined;
  }
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return undefined;
  }

  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "object" && item !== null && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : JSON.stringify(item),
      )
      .join("; ");
  }
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return JSON.stringify(detail);
}
