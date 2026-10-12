/**
 * One origin for the staging deployment: which requests the API Container answers and which
 * the Next.js application does, behind the Access check.
 *
 * The API paths are the API's own (`backend/app/api/`): `/api/v1/…`, `/auth/…`, `/health/…`,
 * `/version` and `/runtime`. None collides with a page or route of the web application,
 * whose own API routes live under `/api/adg`, `/api/auth`, `/api/simulations` and
 * `/api/watches`. Nothing is renamed or re-prefixed: a path means here what it means on the
 * API anywhere else.
 *
 * Pure, so the whole decision is unit-tested in Node (`tests/cloudflare/`).
 */

import { ACCESS_JWT_HEADER, type AccessConfiguration, verifyAccess } from "./access";
import { StagingConfigurationError } from "./environment";

const API_PREFIXES = ["/api/v1/", "/auth/", "/health/"] as const;
const API_EXACT = ["/version", "/runtime"] as const;

/** Whether the API Container answers `pathname`. */
export function isApiPath(pathname: string): boolean {
  return (
    API_EXACT.some((exact) => pathname === exact) ||
    API_PREFIXES.some((prefix) => pathname.startsWith(prefix))
  );
}

export interface RoutingDependencies {
  access: AccessConfiguration | null;
  /** Serve a request from the API Container. May throw `StagingConfigurationError`. */
  api: (request: Request) => Promise<Response>;
  /** Serve a request from the Next.js application. */
  frontend: (request: Request) => Promise<Response>;
  /** Fetch the Access certificate set. Defaults to the network. */
  fetchCertificates?: (url: string) => Promise<Response>;
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

  const verdict = await verifyAccess(request, dependencies.access, dependencies.fetchCertificates);
  if (!verdict.ok) {
    // The reason is about configuration or the assertion's shape, never its contents.
    log({ event: "adg.staging.access_refused", status: verdict.status, reason: verdict.reason, path: url.pathname });
    return refusal(verdict.status, verdict.status === 503 ? verdict.reason : "Access denied.");
  }

  // The assertion has done its job. Neither tier needs it, and a request log downstream is
  // one more place a bearer credential would otherwise be written.
  const headers = new Headers(request.headers);
  headers.delete(ACCESS_JWT_HEADER);
  const forwarded = new Request(request, { headers });

  if (!isApiPath(url.pathname)) {
    return dependencies.frontend(forwarded);
  }
  try {
    return await dependencies.api(forwarded);
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
    return refusal(502, "The ADG API container did not answer.");
  }
}
