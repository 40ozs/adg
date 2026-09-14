/**
 * Every ADG endpoint this application calls, in one place, typed.
 *
 * Pages call these functions; nothing else builds a URL. Keeping the surface here is what
 * makes the OpenAPI check in `tests/contracts.test.ts` meaningful — there is one list of
 * paths to check, and a page cannot quietly invent a new one.
 */

import "server-only";

import type { ApiResult, RequestOptions } from "@/lib/api/client";
import { apiRequest } from "@/lib/api/client";
import type {
  AuthConfig,
  CollectionStatus,
  DevelopmentLogin,
  Principal,
  ScanRunList,
  SearchResults,
  ServersResponse,
} from "@/lib/contracts";

/** Paths this application depends on. Asserted against the published OpenAPI document. */
export const USED_PATHS = [
  "/auth/config",
  "/auth/me",
  "/auth/dev/login",
  "/api/v1/collection/status",
  "/api/v1/scan-runs",
  "/api/v1/search",
  "/api/v1/servers",
] as const;

/** Public: a client has to know how to sign in before it can. */
export function fetchAuthConfig(options: RequestOptions = {}): Promise<ApiResult<AuthConfig>> {
  return apiRequest<AuthConfig>("/auth/config", options);
}

export function fetchPrincipal(token: string): Promise<ApiResult<Principal>> {
  return apiRequest<Principal>("/auth/me", { token });
}

export function developmentLogin(username: string): Promise<ApiResult<DevelopmentLogin>> {
  return apiRequest<DevelopmentLogin>("/auth/dev/login", {
    method: "POST",
    body: { username },
  });
}

export function fetchCollectionStatus(token: string): Promise<ApiResult<CollectionStatus>> {
  return apiRequest<CollectionStatus>("/api/v1/collection/status", { token });
}

export function fetchScanRuns(
  token: string,
  query: { limit?: number; cursor?: string; collector?: string; status?: string } = {},
): Promise<ApiResult<ScanRunList>> {
  return apiRequest<ScanRunList>("/api/v1/scan-runs", { token, query });
}

export function search(token: string, q: string): Promise<ApiResult<SearchResults>> {
  return apiRequest<SearchResults>("/api/v1/search", { token, query: { q } });
}

export function fetchServers(
  token: string,
  query: { limit?: number; cursor?: string } = {},
): Promise<ApiResult<ServersResponse>> {
  return apiRequest<ServersResponse>("/api/v1/servers", { token, query });
}
