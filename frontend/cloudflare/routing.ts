/**
 * The Worker's whole request path: every request that is not a built static file goes to the
 * Render service, with the Worker's origin key, and comes back unchanged.
 *
 * Static files never reach this code: Cloudflare serves them from the assets binding before the
 * Worker runs (`wrangler.jsonc`). Nothing here renders, parses or rewrites a page, so the
 * Worker's CPU time is that of a proxy, within the Workers Free limit.
 *
 * The API paths are the API's own (`backend/app/api/`): `/api/v1/…`, `/auth/…`, `/health/…`,
 * `/version` and `/runtime`. None collides with a page or route of the web application,
 * whose own API routes live under `/api/adg`, `/api/auth`, `/api/simulations` and
 * `/api/watches`. The origin gate on Render sends the same paths to FastAPI and the rest to
 * Next.js (`render/gate.ts`). Here the list decides one thing only: which answers must carry
 * the API's demo marker.
 *
 * Pure, so the whole decision is unit-tested in Node (`tests/cloudflare/`).
 */

import {
  ORIGIN_KEY_HEADER,
  type OriginUpstream,
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
  /** Where the origin is. May throw `StagingConfigurationError`. */
  upstream: () => OriginUpstream;
  fetch?: (request: Request) => Promise<Response>;
  log?: (entry: Record<string, unknown>) => void;
}

function refusal(status: number, detail: string): Response {
  return Response.json(
    { detail },
    { status, headers: { "Cache-Control": "no-store", "X-ADG-Staging": "refused" } },
  );
}

/** The Worker's whole request path. */
export async function routeRequest(request: Request, dependencies: RoutingDependencies): Promise<Response> {
  const log = dependencies.log ?? ((entry) => console.log(JSON.stringify(entry)));
  try {
    return await forwardToOrigin(request, dependencies.upstream(), dependencies.fetch);
  } catch (error) {
    if (error instanceof StagingConfigurationError) {
      log({ event: "adg.staging.misconfigured", detail: error.message });
      return refusal(503, error.message);
    }
    log({
      event: "adg.staging.origin_unavailable",
      path: new URL(request.url).pathname,
      error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    });
    return refusal(502, "The ADG demo service did not answer.");
  }
}

/** Set by the API on every answer of a demo deployment (`backend/app/main.py`). */
const RUNTIME_MODE_HEADER = "X-ADG-Runtime-Mode";
/** RFC 9110 §7.6.1: these describe one connection and are never forwarded. */
const HOP_BY_HOP = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

/**
 * Send one request to the Render service with the key its origin gate requires, and relay the
 * answer as it came.
 *
 * Method, path, query, body, cookies and other end-to-end headers go through unchanged; hop-by-hop
 * headers do not. The origin key is always the Worker's: a visitor who sends `X-ADG-Origin-Key`
 * has it replaced. An API answer must carry the API's demo marker. That is the Worker's half of
 * "this deployment serves Genesee Press only": an API in any other mode, or anything else
 * answering at that address, is a 502, never a page.
 */
export async function forwardToOrigin(
  request: Request,
  upstream: OriginUpstream,
  fetchUpstream: (request: Request) => Promise<Response> = fetch,
): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  const named = (headers.get("connection") ?? "").split(",").map((token) => token.trim());
  for (const name of [...HOP_BY_HOP, ...named]) {
    if (name) {
      headers.delete(name);
    }
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
  if (isApiPath(url.pathname) && response.headers.get(RUNTIME_MODE_HEADER) !== REQUIRED_RUNTIME_MODE) {
    throw new Error(`the API answered HTTP ${response.status} without ${RUNTIME_MODE_HEADER}: demo`);
  }
  return response;
}
