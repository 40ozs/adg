/**
 * The shapes the ADG API returns.
 *
 * Hand-written rather than generated, and then *checked* against the backend's published
 * OpenAPI document by `tests/contracts.test.ts`. Generation would produce a faithful but
 * unreadable surface (every field optional, every union widened) and would still not catch
 * the mistake that matters, which is the frontend believing in a field the backend stopped
 * sending. The check does catch that, and it fails in CI rather than in a browser.
 *
 * `docs/contracts/v1/openapi.json` is the snapshot both sides read. The backend's
 * `tests/contracts/test_openapi_snapshot.py` keeps it current.
 */

/** Roles and capabilities are strings on the wire; the backend owns the vocabulary. */
export type Capability =
  | "resources:read"
  | "identities:read"
  | "access:read"
  | "risks:read"
  | "changes:read"
  | "collectors:read"
  | "collectors:ingest"
  | "search"
  | "settings:read"
  | "settings:write"
  | "remediation:execute";

export interface RoleDescription {
  role: string;
  active: boolean;
  capabilities: string[];
}

export interface AuthConfig {
  mode: "oidc" | "development";
  development: boolean;
  environment: string;
  issuer: string | null;
  client_id: string | null;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  scopes: string[];
  development_accounts: string[];
  roles: RoleDescription[];
}

export interface Principal {
  subject: string;
  display_name: string | null;
  email: string | null;
  source: "oidc" | "development" | "collector_key";
  development: boolean;
  roles: string[];
  capabilities: string[];
  inactive_roles: string[];
  unrecognized_roles: string[];
  expires_at: string | null;
}

export interface DevelopmentLogin {
  access_token: string;
  token_type: "Bearer";
  expires_at: string;
  principal: Principal;
  warning: string;
}

/** The judgement every empty state consults. See `app/domain/collection.py`. */
export type CollectionHealth = "no_data" | "healthy" | "incomplete" | "failed";

export interface CollectorCoverage {
  collector: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  error_count: number;
  target: string | null;
  downgrade_reason: string | null;
  trustworthy: boolean;
  concern: string | null;
}

export interface CollectionStatus {
  health: CollectionHealth;
  summary: string;
  concerns: string[];
  collectors: CollectorCoverage[];
}

/**
 * The operator view of collection: `GET /api/v1/collection/operations`.
 *
 * Deliberately a different response from `CollectionStatus`. That one is consulted by every
 * page before it renders an empty list and is kept cheap; this one is five statements and is
 * read by one screen. Both carry the same `health`, computed from the same runs, so the
 * banner an auditor sees and the page an operator reads can never disagree.
 */
export type Completeness = "complete" | "partial" | "none" | "in_progress";

export interface RunOutcome {
  run_id: string;
  collector: string;
  collector_host: string;
  target: string | null;
  status: string;
  started_at: string;
  completed_at: string | null;
  error_count: number;
  batches_reported: number | null;
  batches_received: number;
  observations_reported: number | null;
  observations_applied: number;
  declared_scopes: number;
  reconciled_scopes: number;
  incremental: boolean;
  downgrade_reason: string | null;
  completeness: Completeness;
  shortfall: string | null;
}

export interface ScopeOperations {
  collector: string;
  target: string | null;
  label: string;
  completeness: Completeness;
  has_ever_succeeded: boolean;
  stale_success: boolean;
  note: string | null;
  latest: RunOutcome;
  last_success: RunOutcome | null;
  last_failure: RunOutcome | null;
}

export interface CollectorErrorGroup {
  code: string;
  count: number;
  collectors: string[];
  latest_occurred_at: string | null;
  sample_targets: string[];
  is_widespread: boolean;
}

export interface ObjectCounts {
  principals: number;
  membership_edges: number;
  servers: number;
  shares: number;
  share_aces: number;
  directories: number;
  ntfs_aces: number;
  scan_runs: number;
  total: number;
}

export interface CollectionOperations {
  health: CollectionHealth;
  summary: string;
  notes: string[];
  scopes: ScopeOperations[];
  counts: ObjectCounts;
  errors: CollectorErrorGroup[];
  total_errors: number;
}

export interface PageInfo {
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  total: number | null;
}

export interface ScanRunSummary {
  run_id: string;
  status: string;
  incremental: boolean;
  started_at: string;
  completed_at: string | null;
  collector: string;
  collector_host: string;
  method: string;
  collector_version: string | null;
  target: string | null;
  batch_count_received: number;
  observation_count_applied: number;
  error_count: number;
  downgrade_reason: string | null;
}

export interface ScanRunList {
  items: ScanRunSummary[];
  page: PageInfo;
}

export interface IdentityHit {
  principal_key: string;
  sid: string;
  kind: string;
  display_name: string | null;
  sam_account_name: string | null;
  user_principal_name: string | null;
  host_key: string | null;
  enabled: boolean | null;
  is_deleted: boolean;
}

export interface ServerHit {
  server_key: string;
  name: string;
  dns_host_name: string | null;
}

export interface ShareHit {
  share_key: string;
  server_key: string;
  name: string;
  unc_path: string;
  share_type: string;
  description: string | null;
}

export interface DirectoryHit {
  resource_key: string;
  path: string;
  server_key: string;
  share_key: string;
  is_acl_boundary: boolean;
}

export interface SkippedCategory {
  category: string;
  reason: string;
}

export interface SearchResults {
  query: string;
  interpreted_as: "sid" | "unc_path" | "server" | "share" | "name";
  interpretation: string;
  limit_per_category: number;
  identities: IdentityHit[];
  servers: ServerHit[];
  shares: ShareHit[];
  directories: DirectoryHit[];
  truncated: string[];
  not_searched: SkippedCategory[];
}

export interface ServerSummary {
  key: string;
  name: string;
  dns_host_name: string | null;
  netbios_name: string | null;
  computer_sid: string | null;
  domain_sid: string | null;
  is_domain_member: boolean | null;
  operating_system: string | null;
  share_count: number;
}

export interface ServersResponse {
  items: ServerSummary[];
  page: PageInfo;
}

/* ------------------------------------------------------------------ identities
 *
 * A principal is keyed by its SID, or by `host|SID` when the SID is only meaningful on one
 * computer. Almost every descriptive field is nullable, and null means "no run has said" —
 * never a default. `is_group: null` in particular is not `false`: a membership edge can
 * reach a principal nothing has described, and calling that an account would be an
 * invention.
 */

export interface PrincipalSummary {
  key: string;
  sid: string;
  resolved: boolean;
  kind: string | null;
  is_group: boolean | null;
  display_name: string | null;
  sam_account_name: string | null;
  user_principal_name: string | null;
  distinguished_name: string | null;
  last_known_name: string | null;
  host_key: string | null;
  enabled: boolean | null;
  is_deleted: boolean;
  group_scope: string | null;
  group_type: string | null;
  unresolved_reason: string | null;
}

export interface AliasView {
  alias_kind: string;
  value: string;
  first_observed_at: string;
  last_observed_at: string;
}

/** Which run first and last saw a fact, and where it came from. */
export interface ProvenanceView {
  source_key: string;
  first_observed_at: string;
  first_observed_run_id: string;
  last_observed_at: string;
  last_observed_run_id: string;
}

export interface PrincipalDetail extends PrincipalSummary {
  domain_sid: string | null;
  direct_member_count: number;
  direct_group_count: number;
  aliases: AliasView[];
  first_observed_at: string | null;
  first_observed_run_id: string | null;
  last_observed_at: string | null;
  last_observed_run_id: string | null;
}

/** How many hops a traversal took, and whether it finished. */
export interface LimitsView {
  max_depth: number;
  max_nodes: number;
  max_edges: number;
  max_paths: number;
}

export interface TraversalView {
  complete: boolean;
  limits: LimitsView;
  depth_reached: number;
  nodes_visited: number;
  edges_read: number;
  truncation: string[];
}

/** A membership loop. Always a finding, never a rendering artifact. */
export interface CycleView {
  members: string[];
  representative_path: string[];
}

export interface DirectMemberView {
  principal: PrincipalSummary;
  edge_kind: string;
  edge_key: string;
  host_key: string | null;
  is_foreign_security_principal: boolean;
  first_observed_at: string;
  last_observed_at: string;
  last_observed_run_id: string;
}

export interface EffectiveMemberView {
  principal: PrincipalSummary;
  depth: number;
  path: string[];
  edge_kinds: string[];
  via_foreign_security_principal: boolean;
}

export interface DirectMembersResponse {
  group: PrincipalSummary;
  items: DirectMemberView[];
  page: PageInfo;
}

export interface EffectiveMembersResponse {
  group: PrincipalSummary;
  include: string;
  items: EffectiveMemberView[];
  page: PageInfo;
  traversal: TraversalView;
  cycles: CycleView[];
}

export interface DirectGroupsResponse {
  principal: PrincipalSummary;
  items: DirectMemberView[];
  page: PageInfo;
}

export interface EffectiveGroupsResponse {
  principal: PrincipalSummary;
  items: EffectiveMemberView[];
  page: PageInfo;
  traversal: TraversalView;
  cycles: CycleView[];
}

/* ------------------------------------------------------------------- resources */

export interface ServerDetail extends ServerSummary {
  provenance: ProvenanceView;
}

export interface ShareSummary {
  key: string;
  server_key: string;
  name: string;
  unc_path: string;
  share_type: string;
  description: string | null;
  local_path: string | null;
  caching_mode: string | null;
  concurrent_user_limit: number | null;
  is_hidden: boolean;
  is_special: boolean | null;
  is_administrative: boolean;
  carries_file_permissions: boolean;
}

export interface NtfsResourceSummary {
  key: string;
  path: string;
  server_key: string;
  share_key: string;
  local_path: string | null;
  parent_path: string | null;
  resource_kind: string;
  is_share_root: boolean;
  depth_from_share_root: number | null;
  owner_sid: string | null;
  group_sid: string | null;
  dacl_present: boolean;
  dacl_protected: boolean;
  inheritance_enabled: boolean;
  is_acl_boundary: boolean;
  boundary_reason: string | null;
  grants_everyone_full_access: boolean;
  denies_everyone: boolean;
  declared_ace_count: number;
}

export interface SharesResponse {
  server: ServerSummary | null;
  items: ShareSummary[];
  page: PageInfo;
}

export interface ShareDetailView extends ShareSummary {
  ace_count: number;
  provenance: ProvenanceView;
  server: ServerSummary | null;
  root_resource: NtfsResourceSummary | null;
}

/**
 * Whether the DACL ADG holds is the descriptor the collector read.
 *
 * `agrees: null` is unknown — a collector that computed no digest has not disagreed with
 * anything — and is never rendered as agreement.
 */
export interface AclHashView {
  computed: string;
  reported: string | null;
  agrees: boolean | null;
  algorithm: string;
  normal_form_version: string;
  ordered: boolean;
  declared_ace_count: number;
  stored_ace_count: number;
  ace_count_agrees: boolean;
}

/** One entry on a share-level ACL, as the SMB server reported it. */
export interface ShareAceView {
  ace_key: string;
  trustee: PrincipalSummary;
  ace_type: string;
  right: string;
  permission: string | null;
  access_mask: number | null;
  order_index: number | null;
  provenance: ProvenanceView;
}

/** One entry on an NTFS DACL. The mask is authoritative; `rights` only names its bits. */
export interface NtfsAceView {
  ace_key: string;
  trustee: PrincipalSummary;
  ace_type: string;
  access_mask: number;
  rights: string[];
  unrecognized_bits: number;
  ace_flags: number;
  source: string;
  inherited_from: string | null;
  is_inheritable: boolean;
  applies_to_this_object: boolean;
  order_index: number | null;
  provenance: ProvenanceView;
}

export interface RawShareAclResponse {
  kind: "raw_smb_acl";
  share_key: string;
  share: ShareSummary | null;
  entries: ShareAceView[];
  page: PageInfo;
}

export interface RawNtfsAclResponse {
  kind: "raw_ntfs_acl";
  resource_key: string;
  resource: NtfsResourceSummary | null;
  acl_hash: AclHashView | null;
  entries: NtfsAceView[];
  page: PageInfo;
}

/** ADG's own boundary verdict beside the collector's, with the parent each one used. */
export interface BoundaryView {
  reported: boolean;
  reported_reason: string | null;
  computed: boolean | null;
  computed_reason: string | null;
  agrees: boolean | null;
  parent_key: string | null;
  parent_observed: boolean;
  parent_acl_hash: string | null;
  reported_parent_acl_hash: string | null;
  parent_acl_hash_agrees: boolean | null;
  projection_available: boolean;
  projected_child_acl_hash: string | null;
  resource_acl_hash: string;
}

export interface NtfsResourceDetailView extends NtfsResourceSummary {
  stored_ace_count: number;
  boundary: BoundaryView;
  provenance: ProvenanceView;
  parent: NtfsResourceSummary | null;
  server: ServerSummary | null;
  share: ShareSummary | null;
}

export interface TrusteeShareView {
  share_key: string;
  share: ShareSummary | null;
  aces: ShareAceView[];
}

export interface TrusteeSharesResponse {
  kind: "raw_smb_acl";
  trustee: PrincipalSummary;
  scope: string;
  items: TrusteeShareView[];
  page: PageInfo;
}

/* --------------------------------------------------------------- effective access
 *
 * Everything below is an engine answer, not a descriptor fact. `access: false` must always
 * be read together with `certainty`: false with `at_least` means no access was
 * established, not that none exists.
 */

export type AccessCertainty = "certain" | "at_most" | "at_least" | "uncertain";
export type LimitingLayer = "none" | "smb_share" | "ntfs" | "both" | "unknown";
export type MemberInclusion = "users" | "non_groups" | "all";

export interface RightsView {
  mask: string;
  value: number;
  layer: string;
  label: string;
  primary: string;
  categories: string[];
  is_exact: boolean;
  extra_rights: string[];
  escalation_rights: string[];
  unrecognized_bits: string | null;
  indeterminate: boolean;
}

export interface ResourceRef {
  key: string;
  path: string | null;
  share_key: string | null;
  observed: boolean;
  owner_sid: string | null;
  dacl_present: boolean | null;
  dacl_protected: boolean | null;
  is_acl_boundary: boolean | null;
}

export interface ShareRef {
  key: string;
  name: string | null;
  server_key: string | null;
  observed: boolean;
}

export interface TokenEntryView {
  principal: PrincipalSummary;
  origin: string;
  assumed: boolean;
  depth: number;
  path: string[];
}

export interface TokenView {
  subject: PrincipalSummary;
  assumption: string;
  access_path: string;
  membership_complete: boolean;
  entries: TokenEntryView[];
}

/** A gap in what was collected, and which direction it could push an answer. */
export interface FindingView {
  condition: string;
  message: string;
  may_overstate: boolean;
  may_understate: boolean;
  detail?: Record<string, unknown>;
}

export interface ResourceAccessView {
  resource: ResourceRef;
  share: ShareRef | null;
  access: boolean;
  rights: RightsView;
  certainty: AccessCertainty;
  limiting_layer: LimitingLayer;
  conditions: string[];
}

export interface PrincipalResourcesResponse {
  subject: PrincipalSummary;
  access_path: string;
  token: TokenView;
  items: ResourceAccessView[];
  page: PageInfo;
}

export interface PrincipalAccessView {
  principal: PrincipalSummary;
  access: boolean;
  rights: RightsView;
  certainty: AccessCertainty;
  limiting_layer: LimitingLayer;
  conditions: string[];
  via: TokenEntryView[];
}

/** Whether the list of principals is everyone, and which trustees could not be expanded. */
export interface EnumerationView {
  complete: boolean;
  unenumerable_trustees: PrincipalSummary[];
  trustees_truncated: boolean;
}

export interface ResourcePrincipalsResponse {
  resource: ResourceRef;
  share: ShareRef | null;
  access_path: string;
  enumeration: EnumerationView;
  findings: FindingView[];
  items: PrincipalAccessView[];
  page: PageInfo;
}
