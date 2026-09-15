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
  ChangeComparisonResponse,
  ChangeImpactResponse,
  ChangeSummaryResponse,
  ChangeTimelineResponse,
  ChangesResponse,
  CollectionOperations,
  CollectionStatus,
  DevelopmentLogin,
  DirectGroupsResponse,
  DirectMembersResponse,
  EffectiveGroupsResponse,
  EffectiveMembersResponse,
  MemberInclusion,
  NtfsResourceDetailView,
  Principal,
  PrincipalDetail,
  PrincipalResourcesResponse,
  RawNtfsAclResponse,
  RawShareAclResponse,
  ResourcePrincipalsResponse,
  ScanRunList,
  SearchResults,
  ServerDetail,
  ServersResponse,
  ShareDetailView,
  SharesResponse,
  TrusteeSharesResponse,
} from "@/lib/contracts";

/** Paths this application depends on. Asserted against the published OpenAPI document. */
export const USED_PATHS = [
  "/auth/config",
  "/auth/me",
  "/auth/dev/login",
  "/api/v1/collection/status",
  "/api/v1/collection/operations",
  "/api/v1/scan-runs",
  "/api/v1/search",
  "/api/v1/servers",
  "/api/v1/servers/{server}",
  "/api/v1/servers/{server}/shares",
  "/api/v1/shares/{share}",
  "/api/v1/shares/{share}/acl",
  "/api/v1/shares/{share}/root-acl",
  "/api/v1/resources/{resource}",
  "/api/v1/resources/{resource}/acl",
  "/api/v1/principals/{identifier}",
  "/api/v1/principals/{identifier}/groups",
  "/api/v1/principals/{trustee}/shares",
  "/api/v1/groups/{identifier}/members",
  "/api/v1/groups/{identifier}/effective-members",
  "/api/v1/access/principals/{identifier}/shares",
  "/api/v1/access/principals/{identifier}/resources",
  "/api/v1/access/resources/{resource}/principals",
  "/api/v1/changes",
  "/api/v1/changes/summary",
  "/api/v1/changes/timeline",
  "/api/v1/changes/compare",
  "/api/v1/changes/impact",
] as const;

/**
 * A path segment carrying an identifier.
 *
 * `encodeURIComponent` and not a template hole: a directory is named by its UNC path, so
 * the segment contains backslashes, and a local group's key contains a pipe. Both have to
 * reach the API as one segment rather than as structure. The API's own tests encode with
 * `quote(value, safe="")`, which is the same rule.
 */
function segment(value: string): string {
  return encodeURIComponent(value);
}

/**
 * Every list endpoint takes the same two, and none of them accepts a page number.
 *
 * A type alias rather than an interface: only aliases get an implicit index signature, and
 * `RequestOptions.query` is a `Record`. An interface here compiles nowhere.
 */
export type PageQuery = {
  limit?: number;
  cursor?: string;
};

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

/**
 * The collector status page's one call.
 *
 * Separate from `fetchCollectionStatus` because the two answer different questions: that
 * one is on the path of every screen, this one is the operator's page and costs more.
 */
export function fetchCollectionOperations(
  token: string,
): Promise<ApiResult<CollectionOperations>> {
  return apiRequest("/api/v1/collection/operations", { token });
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

/* ------------------------------------------------------------------- resources */

export function fetchServer(token: string, key: string): Promise<ApiResult<ServerDetail>> {
  return apiRequest<ServerDetail>(`/api/v1/servers/${segment(key)}`, { token });
}

export function fetchServerShares(
  token: string,
  key: string,
  query: PageQuery = {},
): Promise<ApiResult<SharesResponse>> {
  return apiRequest<SharesResponse>(`/api/v1/servers/${segment(key)}/shares`, { token, query });
}

export function fetchShare(token: string, key: string): Promise<ApiResult<ShareDetailView>> {
  return apiRequest<ShareDetailView>(`/api/v1/shares/${segment(key)}`, { token });
}

/** The share-layer ACL. Not the file system, and not an access answer. */
export function fetchShareAcl(
  token: string,
  key: string,
  query: PageQuery = {},
): Promise<ApiResult<RawShareAclResponse>> {
  return apiRequest<RawShareAclResponse>(`/api/v1/shares/${segment(key)}/acl`, { token, query });
}

/** The NTFS ACL of the directory a share publishes — a different layer, a different page. */
export function fetchShareRootAcl(
  token: string,
  key: string,
  query: PageQuery = {},
): Promise<ApiResult<RawNtfsAclResponse>> {
  return apiRequest<RawNtfsAclResponse>(`/api/v1/shares/${segment(key)}/root-acl`, {
    token,
    query,
  });
}

export function fetchResource(
  token: string,
  key: string,
): Promise<ApiResult<NtfsResourceDetailView>> {
  return apiRequest<NtfsResourceDetailView>(`/api/v1/resources/${segment(key)}`, { token });
}

export function fetchResourceAcl(
  token: string,
  key: string,
  query: PageQuery = {},
): Promise<ApiResult<RawNtfsAclResponse>> {
  return apiRequest<RawNtfsAclResponse>(`/api/v1/resources/${segment(key)}/acl`, {
    token,
    query,
  });
}

/* ------------------------------------------------------------------ identities */

export function fetchPrincipalDetail(
  token: string,
  identifier: string,
  query: { host?: string } = {},
): Promise<ApiResult<PrincipalDetail>> {
  return apiRequest<PrincipalDetail>(`/api/v1/principals/${segment(identifier)}`, {
    token,
    query,
  });
}

/**
 * Groups containing a principal.
 *
 * Two different answers behind one route: `direct` is what the groups' own membership
 * lists say, `effective` is every group reached through nesting. The response shapes
 * differ, so the scope is a separate function rather than a flag — a caller cannot read an
 * effective answer as a direct one by forgetting which it asked for.
 */
export function fetchDirectGroups(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string } = {},
): Promise<ApiResult<DirectGroupsResponse>> {
  return apiRequest<DirectGroupsResponse>(`/api/v1/principals/${segment(identifier)}/groups`, {
    token,
    query: { ...query, scope: "direct" },
  });
}

export function fetchEffectiveGroups(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string } = {},
): Promise<ApiResult<EffectiveGroupsResponse>> {
  return apiRequest<EffectiveGroupsResponse>(`/api/v1/principals/${segment(identifier)}/groups`, {
    token,
    query: { ...query, scope: "effective" },
  });
}

export function fetchDirectMembers(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string } = {},
): Promise<ApiResult<DirectMembersResponse>> {
  return apiRequest<DirectMembersResponse>(`/api/v1/groups/${segment(identifier)}/members`, {
    token,
    query,
  });
}

export function fetchEffectiveMembers(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string; include?: MemberInclusion } = {},
): Promise<ApiResult<EffectiveMembersResponse>> {
  return apiRequest<EffectiveMembersResponse>(
    `/api/v1/groups/${segment(identifier)}/effective-members`,
    { token, query },
  );
}

/** Shares whose share-level ACL names a SID. Raw entries, not reachability. */
export function fetchTrusteeShares(
  token: string,
  trustee: string,
  query: PageQuery = {},
): Promise<ApiResult<TrusteeSharesResponse>> {
  return apiRequest<TrusteeSharesResponse>(`/api/v1/principals/${segment(trustee)}/shares`, {
    token,
    query,
  });
}

/* ------------------------------------------------------------- effective access */

export function fetchAccessibleShares(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string } = {},
): Promise<ApiResult<PrincipalResourcesResponse>> {
  return apiRequest<PrincipalResourcesResponse>(
    `/api/v1/access/principals/${segment(identifier)}/shares`,
    { token, query },
  );
}

export function fetchAccessibleResources(
  token: string,
  identifier: string,
  query: PageQuery & { host?: string } = {},
): Promise<ApiResult<PrincipalResourcesResponse>> {
  return apiRequest<PrincipalResourcesResponse>(
    `/api/v1/access/principals/${segment(identifier)}/resources`,
    { token, query },
  );
}

export function fetchResourcePrincipals(
  token: string,
  resource: string,
  query: PageQuery & { members?: MemberInclusion } = {},
): Promise<ApiResult<ResourcePrincipalsResponse>> {
  return apiRequest<ResourcePrincipalsResponse>(
    `/api/v1/access/resources/${segment(resource)}/principals`,
    { token, query },
  );
}

/* ----------------------------------------------------------------------- changes */

/**
 * The filters `/api/v1/changes` and `/api/v1/changes/summary` share.
 *
 * `from` and `to` are required and must carry a UTC offset; the API refuses a naive
 * instant rather than assuming one, because a window an hour out reports a different day's
 * changes. At most one scope may be given — two at once could mean their intersection or
 * their union, and the API refuses rather than picking.
 */
export type ChangeQuery = {
  from: string;
  to: string;
  server?: string;
  share?: string;
  directory?: string;
  principal?: string;
  group?: string;
  kind?: string[];
  action?: string[];
  significance?: string[];
  min_severity?: string;
};

export function fetchChanges(
  token: string,
  query: ChangeQuery & PageQuery,
): Promise<ApiResult<ChangesResponse>> {
  return apiRequest<ChangesResponse>("/api/v1/changes", { token, query });
}

/**
 * Counts over the whole window, taken **before** the filter.
 *
 * The one thing a client cannot compute from a page: whether the page it is showing is the
 * whole story. Without it, a page that is clean because of a default is indistinguishable
 * from a quiet week.
 */
export function fetchChangeSummary(
  token: string,
  query: ChangeQuery,
): Promise<ApiResult<ChangeSummaryResponse>> {
  return apiRequest<ChangeSummaryResponse>("/api/v1/changes/summary", { token, query });
}

export function fetchChangeTimeline(
  token: string,
  query: { kind: string; key: string; limit?: number },
): Promise<ApiResult<ChangeTimelineResponse>> {
  return apiRequest<ChangeTimelineResponse>("/api/v1/changes/timeline", { token, query });
}

export function compareInstants(
  token: string,
  query: {
    from: string;
    to: string;
    server?: string;
    share?: string;
    directory?: string;
    principal?: string;
    group?: string;
    kind?: string[];
    significance?: string[];
    min_severity?: string;
  },
): Promise<ApiResult<ChangeComparisonResponse>> {
  return apiRequest<ChangeComparisonResponse>("/api/v1/changes/compare", { token, query });
}

/**
 * Why access changed: the live engine, resolved either side of one edit.
 *
 * `at` is the change's own `at` value, handed straight back. Requires `access:read` rather
 * than `changes:read`, so a viewer who may read the feed can still be refused here.
 */
export function fetchChangeImpact(
  token: string,
  query: {
    kind: string;
    key: string;
    at: string;
    subject?: string;
    resource?: string;
    access_path?: string;
  },
): Promise<ApiResult<ChangeImpactResponse>> {
  return apiRequest<ChangeImpactResponse>("/api/v1/changes/impact", { token, query });
}
