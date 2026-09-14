/**
 * The unauthenticated health surface.
 *
 * Separate from `lib/api/adg.ts` because these two endpoints are the only ones that answer
 * without a token, and the status page has to work when nobody is signed in — which is
 * exactly when somebody is trying to find out why.
 */

import { apiRequest } from "@/lib/api/client";
import { resolveServerApiUrl } from "@/lib/config";

export interface DependencyStatus {
  name: string;
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  version: string;
  dependencies: DependencyStatus[];
}

export interface VersionResponse {
  name: string;
  version: string;
  environment: string;
}

export type BackendStatus =
  | { reachable: true; readiness: ReadinessResponse; version: VersionResponse }
  | { reachable: false; error: string };

/**
 * Readiness and version together.
 *
 * A 503 from `/health/ready` still carries a well-formed body naming the failed dependency,
 * and that body is the whole point of the page — so it is rendered rather than treated as an
 * unreachable API.
 */
export async function fetchBackendStatus(
  apiUrl: string = resolveServerApiUrl(),
): Promise<BackendStatus> {
  const [readiness, version] = await Promise.all([
    apiRequest<ReadinessResponse>("/health/ready"),
    apiRequest<VersionResponse>("/version"),
  ]);

  if (version.ok && readiness.ok) {
    return { reachable: true, readiness: readiness.data, version: version.data };
  }

  // The readiness probe answers 503 with a body when the database is down. The client
  // classifies that as "unreachable"; here it is a result, so it is re-read from the body.
  if (version.ok && !readiness.ok && readiness.failure.status === 503) {
    return {
      reachable: true,
      version: version.data,
      readiness: {
        status: "not_ready",
        version: version.data.version,
        dependencies: [
          {
            name: "postgresql",
            ok: false,
            latency_ms: 0,
            error: readiness.failure.detail ?? readiness.failure.message,
          },
        ],
      },
    };
  }

  const failure = version.ok ? readiness : version;
  const detail = failure.ok ? "unknown" : (failure.failure.detail ?? failure.failure.message);
  return {
    reachable: false,
    error: `Could not reach the ADG API at ${apiUrl}: ${detail}`,
  };
}
