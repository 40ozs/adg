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
import type { SimulationRequestBody } from "@/lib/simulation";
import type {
  AlertDetailResponse,
  AlertQueueResponse,
  AlertsResponse,
  AuthConfig,
  BulkDecisionResponse,
  CampaignListResponse,
  CampaignStatusResponse,
  CampaignView,
  ChangeComparisonResponse,
  ChangeImpactResponse,
  ChangeSummaryResponse,
  ChangeTimelineResponse,
  ChangesResponse,
  CollectionOperations,
  CollectionStatus,
  DecisionView,
  DevelopmentLogin,
  DirectGroupsResponse,
  DriftReportResponse,
  DirectMembersResponse,
  FindingDetailView,
  FindingsResponse,
  EffectiveGroupsResponse,
  EffectiveMembersResponse,
  ItemContextResponse,
  ItemDetailResponse,
  ItemListResponse,
  MemberInclusion,
  NtfsResourceDetailView,
  Principal,
  PrincipalDetail,
  PrincipalResourcesResponse,
  QueueResponse,
  RawNtfsAclResponse,
  RawShareAclResponse,
  ResourcePrincipalsResponse,
  RiskRulesResponse,
  RiskSummaryResponse,
  ScanRunList,
  SearchResults,
  ServerDetail,
  ServersResponse,
  ShareDetailView,
  SharesResponse,
  SimulationDetailResponse,
  SimulationExportResponse,
  SimulationReportView,
  SimulationVocabularyResponse,
  StoredSimulationResponse,
  StoredSimulationsResponse,
  TrusteeSharesResponse,
  WatchView,
  WatchesResponse,
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
  "/api/v1/governance/campaigns",
  "/api/v1/governance/campaigns/{campaign_id}",
  "/api/v1/governance/campaigns/{campaign_id}/status",
  "/api/v1/governance/campaigns/{campaign_id}/items",
  "/api/v1/governance/campaigns/{campaign_id}/drift",
  "/api/v1/governance/campaigns/{campaign_id}/decisions",
  "/api/v1/governance/queue",
  "/api/v1/governance/items/{item_id}",
  "/api/v1/governance/items/{item_id}/context",
  "/api/v1/governance/items/{item_id}/decisions",
  "/api/v1/simulations",
  "/api/v1/simulations/preview",
  "/api/v1/simulations/vocabulary",
  "/api/v1/simulations/{simulation_id}",
  "/api/v1/simulations/{simulation_id}/evaluations",
  "/api/v1/simulations/{simulation_id}/export",
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

/* -------------------------------------------------------------------------- governance */

/**
 * The access-review surface.
 *
 * Two of these are writes, which is new: every other call in this module is a `GET`. They
 * are reached from server actions (`app/governance/actions.ts`) rather than from the
 * browser-facing proxy, which forwards `GET` only and says why. The token therefore still
 * never exists in the browser, and the proxy's rule is not weakened to let a review screen
 * post a decision.
 */

export function fetchCampaigns(
  token: string,
  query: { status?: string; mine?: boolean; limit?: number; cursor?: string } = {},
): Promise<ApiResult<CampaignListResponse>> {
  return apiRequest<CampaignListResponse>("/api/v1/governance/campaigns", {
    token,
    query: { ...query, mine: query.mine === undefined ? undefined : String(query.mine) },
  });
}

export function fetchCampaign(
  token: string,
  campaignId: string,
): Promise<ApiResult<CampaignView>> {
  return apiRequest<CampaignView>(`/api/v1/governance/campaigns/${segment(campaignId)}`, {
    token,
  });
}

/** How far a campaign has got, who is behind, and what it deliberately left out. */
export function fetchCampaignStatus(
  token: string,
  campaignId: string,
): Promise<ApiResult<CampaignStatusResponse>> {
  return apiRequest<CampaignStatusResponse>(
    `/api/v1/governance/campaigns/${segment(campaignId)}/status`,
    { token },
  );
}

export function fetchCampaignItems(
  token: string,
  campaignId: string,
  query: PageQuery & {
    status?: string;
    mine?: boolean;
    unassigned?: boolean;
    principal_key?: string;
    target_key?: string;
  } = {},
): Promise<ApiResult<ItemListResponse>> {
  const { mine, unassigned, ...rest } = query;
  return apiRequest<ItemListResponse>(
    `/api/v1/governance/campaigns/${segment(campaignId)}/items`,
    {
      token,
      query: {
        ...rest,
        mine: mine === undefined ? undefined : String(mine),
        unassigned: unassigned === undefined ? undefined : String(unassigned),
      },
    },
  );
}

/**
 * What the estate has done to a campaign's items since it was frozen.
 *
 * Bounded on the server and the response says by how much, so a page rendering "nothing has
 * changed" must show `covered` against `total_items` beside it.
 */
export function fetchCampaignDrift(
  token: string,
  campaignId: string,
  query: PageQuery = {},
): Promise<ApiResult<DriftReportResponse>> {
  return apiRequest<DriftReportResponse>(
    `/api/v1/governance/campaigns/${segment(campaignId)}/drift`,
    { token, query },
  );
}

export function fetchReviewerQueue(
  token: string,
  query: { include_closed?: boolean } = {},
): Promise<ApiResult<QueueResponse>> {
  return apiRequest<QueueResponse>("/api/v1/governance/queue", {
    token,
    query: {
      include_closed:
        query.include_closed === undefined ? undefined : String(query.include_closed),
    },
  });
}

export function fetchReviewItem(
  token: string,
  itemId: string,
): Promise<ApiResult<ItemDetailResponse>> {
  return apiRequest<ItemDetailResponse>(`/api/v1/governance/items/${segment(itemId)}`, {
    token,
  });
}

/** The whole review screen in one call: drift, both access answers, why, risk, last change. */
export function fetchReviewContext(
  token: string,
  itemId: string,
): Promise<ApiResult<ItemContextResponse>> {
  return apiRequest<ItemContextResponse>(
    `/api/v1/governance/items/${segment(itemId)}/context`,
    { token },
  );
}

export function submitDecision(
  token: string,
  itemId: string,
  body: { decision: string; rationale?: string | null },
): Promise<ApiResult<DecisionView>> {
  return apiRequest<DecisionView>(`/api/v1/governance/items/${segment(itemId)}/decisions`, {
    token,
    method: "POST",
    body,
  });
}

/**
 * One decision across several items the API has agreed are one question.
 *
 * The homogeneity rules are the server's and are not re-implemented here. This application
 * disables the control when it can already tell the selection will be refused — which is a
 * courtesy, exactly like the navigation filter, and never the control.
 */
export function submitBulkDecision(
  token: string,
  campaignId: string,
  body: { item_ids: string[]; decision: string; rationale?: string | null },
): Promise<ApiResult<BulkDecisionResponse>> {
  return apiRequest<BulkDecisionResponse>(
    `/api/v1/governance/campaigns/${segment(campaignId)}/decisions`,
    { token, method: "POST", body },
  );
}

/* ------------------------------------------------------------------ risks and alerts */

/** The dashboard header: counts, coverage and rule configuration in one call. */
export function fetchRiskSummary(token: string): Promise<ApiResult<RiskSummaryResponse>> {
  return apiRequest<RiskSummaryResponse>("/api/v1/risks/summary", { token });
}

/**
 * A filtered page of findings.
 *
 * Every filter is repeatable except `place`, `principal` and the two instants. Unknown
 * values are refused by the API with a 422 rather than ignored, so a typo in a severity
 * surfaces as an error rather than as a wider result nobody notices.
 */
export function fetchFindings(
  token: string,
  query: {
    status?: string[];
    severity?: string[];
    confidence?: string[];
    rule?: string[];
    place?: string;
    principal?: string;
    first_seen_from?: string;
    last_seen_to?: string;
  } & PageQuery,
): Promise<ApiResult<FindingsResponse>> {
  return apiRequest<FindingsResponse>("/api/v1/risks/findings", { token, query });
}

/** One finding, its evidence records, its timeline, and whether it still reproduces. */
export function fetchFinding(
  token: string,
  key: string,
): Promise<ApiResult<FindingDetailView>> {
  return apiRequest<FindingDetailView>(`/api/v1/risks/findings/${segment(key)}`, { token });
}

/** The rule catalog as configured, including the rules that are off. */
export function fetchRiskRules(token: string): Promise<ApiResult<RiskRulesResponse>> {
  return apiRequest<RiskRulesResponse>("/api/v1/risks/rules", { token });
}

export function fetchAlerts(
  token: string,
  query: { status?: string[]; trigger?: string[]; watch_id?: string; since?: string } & PageQuery,
): Promise<ApiResult<AlertsResponse>> {
  return apiRequest<AlertsResponse>("/api/v1/alerts", { token, query });
}

/** One alert, every occurrence of it including the suppressed ones, and its deliveries. */
export function fetchAlert(
  token: string,
  key: string,
): Promise<ApiResult<AlertDetailResponse>> {
  return apiRequest<AlertDetailResponse>(`/api/v1/alerts/${segment(key)}`, { token });
}

export function fetchWatches(token: string): Promise<ApiResult<WatchesResponse>> {
  return apiRequest<WatchesResponse>("/api/v1/alerts/watches", { token });
}

export function createWatch(
  token: string,
  body: {
    kind: string;
    key: string;
    label: string;
    triggers: string[];
    cooldown_seconds?: number;
    notes?: string | null;
  },
): Promise<ApiResult<WatchView>> {
  return apiRequest<WatchView>("/api/v1/alerts/watches", { token, method: "POST", body });
}

export function updateWatch(
  token: string,
  watchId: string,
  body: {
    label?: string;
    triggers?: string[];
    cooldown_seconds?: number;
    enabled?: boolean;
    notes?: string | null;
  },
): Promise<ApiResult<WatchView>> {
  return apiRequest<WatchView>(`/api/v1/alerts/watches/${segment(watchId)}`, {
    token,
    method: "PATCH",
    body,
  });
}

export function deleteWatch(token: string, watchId: string): Promise<ApiResult<null>> {
  return apiRequest<null>(`/api/v1/alerts/watches/${segment(watchId)}`, {
    token,
    method: "DELETE",
  });
}

/** Depth, staleness, abandonment, and the policy in force. */
export function fetchAlertQueue(token: string): Promise<ApiResult<AlertQueueResponse>> {
  return apiRequest<AlertQueueResponse>("/api/v1/alerts/deliveries", { token });
}


// ---------------------------------------------------------------- simulations (9B)

/**
 * What-if proposals.
 *
 * Two POSTs that look alike and are not: `previewSimulation` computes an answer and keeps
 * nothing, and `storeSimulation` writes the proposal and its first evaluation down. Neither
 * writes to Active Directory, to a share, or to an NTFS descriptor -- there is no code path
 * from any of these to a Windows object, and every response carries the sentence saying so.
 */
export function previewSimulation(
  token: string,
  body: SimulationRequestBody,
): Promise<ApiResult<SimulationReportView>> {
  return apiRequest<SimulationReportView>("/api/v1/simulations/preview", {
    token,
    method: "POST",
    body,
    // A bounded simulation is still the most expensive request this API serves, and the
    // server's own time budget is ten seconds. A client timeout under it would abandon a
    // request the API was about to answer.
    timeoutMs: 30_000,
  });
}

export function storeSimulation(
  token: string,
  body: SimulationRequestBody & { name: string; description?: string | null },
): Promise<ApiResult<StoredSimulationResponse>> {
  return apiRequest<StoredSimulationResponse>("/api/v1/simulations", {
    token,
    method: "POST",
    body,
    timeoutMs: 30_000,
  });
}

export function fetchSimulations(
  token: string,
  options: PageQuery = {},
): Promise<ApiResult<StoredSimulationsResponse>> {
  return apiRequest<StoredSimulationsResponse>("/api/v1/simulations", { token, query: options });
}

export function fetchSimulation(
  token: string,
  simulationId: string,
): Promise<ApiResult<SimulationDetailResponse>> {
  return apiRequest<SimulationDetailResponse>(`/api/v1/simulations/${segment(simulationId)}`, {
    token,
  });
}

export function evaluateSimulation(
  token: string,
  simulationId: string,
): Promise<ApiResult<SimulationReportView>> {
  return apiRequest<SimulationReportView>(
    `/api/v1/simulations/${segment(simulationId)}/evaluations`,
    { token, method: "POST", timeoutMs: 30_000 },
  );
}

export function deleteSimulation(token: string, simulationId: string): Promise<ApiResult<null>> {
  return apiRequest<null>(`/api/v1/simulations/${segment(simulationId)}`, {
    token,
    method: "DELETE",
  });
}

/** The plan, the result, and the vocabulary that explains it, in one document. */
export function fetchSimulationExport(
  token: string,
  simulationId: string,
  evaluationId?: string,
): Promise<ApiResult<SimulationExportResponse>> {
  return apiRequest<SimulationExportResponse>(
    `/api/v1/simulations/${segment(simulationId)}/export`,
    { token, query: { evaluation_id: evaluationId } },
  );
}

/**
 * The closed vocabularies a report speaks, with their wording.
 *
 * Fetched rather than hard-coded for the reason the navigation's capability list is fetched:
 * a second copy of a vocabulary is a second copy that can be wrong, and the wrong one is
 * always the one somebody trusts. `loss_may_not_hold` is the entry that matters -- a client
 * that invented its own text for it would be writing the sentence that stands between a
 * report and a remediation that achieves nothing.
 */
export function fetchSimulationVocabulary(
  token: string,
): Promise<ApiResult<SimulationVocabularyResponse>> {
  return apiRequest<SimulationVocabularyResponse>("/api/v1/simulations/vocabulary", { token });
}
