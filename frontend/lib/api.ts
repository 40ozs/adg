/**
 * Typed access to the ADG backend.
 *
 * The frontend is a consumer of stable APIs: it never reimplements permission math and it
 * never assumes an endpoint succeeded. Every call returns either parsed data or an
 * explicit, displayable error.
 */

import { resolveServerApiUrl } from "./config";

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

const REQUEST_TIMEOUT_MS = 5000;

async function getJson<T>(apiUrl: string, path: string): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: { Accept: "application/json" },
  });

  // 503 from /health/ready still carries a well-formed body that the status view shows.
  if (!response.ok && response.status !== 503) {
    throw new Error(`GET ${path} returned ${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}

export async function fetchBackendStatus(
  apiUrl: string = resolveServerApiUrl(),
): Promise<BackendStatus> {
  try {
    const [readiness, version] = await Promise.all([
      getJson<ReadinessResponse>(apiUrl, "/health/ready"),
      getJson<VersionResponse>(apiUrl, "/version"),
    ]);
    return { reachable: true, readiness, version };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return {
      reachable: false,
      error: `Could not reach the ADG API at ${apiUrl}: ${detail}`,
    };
  }
}
