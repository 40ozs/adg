/**
 * One public origin for the staging deployment: which requests the ADG API (on Render) answers
 * and which the Next.js application does, and how an API request leaves Cloudflare.
 *
 * The API paths are the API's own (`backend/app/api/`): `/api/v1/…`, `/auth/…`, `/health/…`,
 * `/version` and `/runtime`. None collides with a page or route of the web application,
 * whose own API routes live under `/api/adg`, `/api/auth`, `/api/simulations` and
 * `/api/watches`. Nothing is renamed or re-prefixed: a path means here what it means on the
 * API anywhere else.
 *
 * Pure, so the whole decision is unit-tested in Node (`tests/cloudflare/`).
 */

import {
  type ApiUpstream,
  ORIGIN_KEY_HEADER,
  REQUIRED_RUNTIME_MODE,
  StagingConfigurationError,
} from "./environment";

const API_PREFIXES = ["/api/v1/", "/auth/", "/health/"] as const;
const API_EXACT = ["/version", "/runtime"] as const;

/** Whether the ADG API answers `pathname`. */
export function isApiPath(pathname: string): boolean {
  return (
    API_EXACT.some((exact) => pathname === exact) ||
    API_PREFIXES.some((prefix) => pathname.startsWith(prefix))
  );
}

export interface RoutingDependencies {
  /** Serve a request from the ADG API. May throw `StagingConfigurationError`. */
  api: (request: Request) => Promise<Response>;
  /** Serve a request from the Next.js application. */
  frontend: (request: Request) => Promise<Response>;
  log?: (entry: Record<string, unknown>) => void;
}

function refusal(status: number, detail: string): Response {
  return Response.json(
    { detail },
    { status, headers: { "Cache-Control": "no-store", "X-ADG-Staging": "refused" } },
  );
}

/** The Worker's whole request path. */
export async function routeRequest(
  request: Request,
  dependencies: RoutingDependencies,
): Promise<Response> {
  const log = dependencies.log ?? ((entry) => console.log(JSON.stringify(entry)));
  const url = new URL(request.url);
  if (!isApiPath(url.pathname)) {
    return dependencies.frontend(request);
  }
  try {
    return await dependencies.api(request);
  } catch (error) {
    if (error instanceof StagingConfigurationError) {
      log({ event: "adg.staging.misconfigured", detail: error.message });
      return refusal(503, error.message);
    }
    log({
      event: "adg.staging.api_unavailable",
      path: url.pathname,
      error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    });
    return refusal(502, "The ADG API did not answer.");
  }
}

/** Set by the API on every answer of a demo deployment (`backend/app/main.py`). */
const RUNTIME_MODE_HEADER = "X-ADG-Runtime-Mode";
/**
 * What a caller sends that must not reach the API: the web tier's session cookie (the API reads
 * only a bearer token) and any origin key of the caller's own, which the Worker's replaces.
 */
const NOT_FOR_THE_API = ["cookie", ORIGIN_KEY_HEADER];

/**
 * Send one API request — from the edge or from Next.js — to the API's origin, with the key its
 * origin gate requires, and relay the answer only if the API marked it as a demo answer.
 *
 * The request leaves Cloudflare here, so nothing meant for the web tier goes with it, and the
 * origin key is always the Worker's: a visitor who sends `X-ADG-Origin-Key` has it replaced.
 * The demo check is the Worker's half of "this deployment serves Null Meridian only": an API
 * in any other mode, or anything else answering at that address, is a 502, never a page.
 */
export async function forwardToApi(
  request: Request,
  upstream: ApiUpstream,
  fetchUpstream: (request: Request) => Promise<Response> = fetch,
): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  for (const name of NOT_FOR_THE_API) {
    headers.delete(name);
  }
  headers.set(ORIGIN_KEY_HEADER, upstream.key);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const response = await fetchUpstream(
    new Request(`${upstream.origin}${url.pathname}${url.search}`, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal,
      redirect: "manual",
    }),
  );
  const mode = response.headers.get(RUNTIME_MODE_HEADER);
  if (mode !== REQUIRED_RUNTIME_MODE) {
    throw new Error(`the API answered HTTP ${response.status} without ${RUNTIME_MODE_HEADER}: demo`);
  }
  return response;
}
