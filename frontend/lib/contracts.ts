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
  | "alerts:read"
  | "alerts:manage"
  | "changes:read"
  | "collectors:read"
  | "collectors:ingest"
  | "search"
  | "settings:read"
  | "settings:write"
  | "simulations:read"
  | "simulations:run"
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
  /**
   * Why this run is or is not incremental. Added alongside `incremental`, which stays the
   * flag the reconciliation guard reads; this is the finer statement layered over it.
   *
   * Carried here because the published contract marks it required, and a response field the
   * frontend does not carry is exactly the drift `tests/contracts.test.ts` exists to catch.
   */
  mode: string;
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

/* ------------------------------------------------------------------------ changes */

/**
 * What happened to an object.
 *
 * `first_observed` is **not** a creation. It means ADG had no prior view of the thing that
 * contains the object, so its appearance in the record is the beginning of observation.
 * `lib/changes.ts` renders it as "first seen" and never as "added"; an estate's first scan
 * produces one per object, and calling them additions would report a whole estate as having
 * been built on a Tuesday.
 */
export type ChangeAction = "added" | "modified" | "removed" | "first_observed";

/** Whether a change is about access, about description, or about nothing at all. */
export type ChangeSignificance = "security" | "metadata" | "noise" | "undetermined";

/** Which way an edit moved access. Not whether anybody's effective access moved. */
export type ChangeDirection =
  | "broadened"
  | "narrowed"
  | "mixed"
  | "neutral"
  | "undetermined";

export type ChangeSeverity = "critical" | "high" | "medium" | "low" | "info";

/**
 * When a change happened, as the interval it is known to have happened inside.
 *
 * Both ends, always (ADR-0019). A collector samples rather than watches, so the instant in
 * `ChangeView.at` is the moment somebody looked — render *this*, not that.
 */
export interface ChangeWindowView {
  after: string;
  at_or_before: string;
  is_exact: boolean;
  duration_seconds: number;
}

export interface ChangeVersionView {
  present: boolean;
  valid_from: string;
  last_seen_at: string;
  valid_to: string | null;
  origin: string;
  opened_by_run_id: string;
  last_seen_run_id: string;
  state: Record<string, unknown> | null;
}

export interface FieldDeltaView {
  field: string;
  before: unknown;
  after: unknown;
  significance: string;
}

export interface ChangeSubjectView {
  container_kind: string | null;
  container_key: string | null;
  related_kind: string | null;
  related_key: string | null;
}

export interface ChangeView {
  kind: string;
  key: string;
  action: ChangeAction;
  significance: ChangeSignificance;
  direction: ChangeDirection;
  severity: ChangeSeverity;
  reasons: string[];
  rule_ids: string[];
  at: string;
  window: ChangeWindowView | null;
  reconstructed: boolean;
  subject: ChangeSubjectView;
  before: ChangeVersionView | null;
  after: ChangeVersionView;
  deltas: FieldDeltaView[];
  edit: number | null;
}

/** A removal and an addition that are one edit of one ACL entry. */
export interface AceEditView {
  index: number;
  kind: string;
  container_key: string;
  trustee_key: string;
  ace_type: string;
  direction: ChangeDirection;
  severity: ChangeSeverity;
  summary: string;
  rights_before: RightsView | null;
  rights_after: RightsView | null;
}

export interface ChangeFiltersView {
  window_from: string;
  window_to: string;
  scope_target: string | null;
  scope_key: string | null;
  kinds: string[] | null;
  actions: string[];
  significance: string[];
  min_severity: string;
}

export interface ChangesResponse {
  filters: ChangeFiltersView;
  changes: ChangeView[];
  edits: AceEditView[];
  page: PageInfo;
  scanned: number;
  scan_exhausted: boolean;
}

export interface ChangeSummaryResponse {
  window_from: string;
  window_to: string;
  total: number;
  returned: number;
  excluded: number;
  highest_severity: ChangeSeverity | null;
  reconstructed: number;
  truncated: boolean;
  by_action: Record<string, number>;
  by_significance: Record<string, number>;
  by_severity: Record<string, number>;
  by_kind: Record<string, number>;
}

export interface ChangeTimelineResponse {
  kind: string;
  key: string;
  changes: ChangeView[];
  edits: AceEditView[];
  truncated: boolean;
}

export interface ChangeComparisonResponse {
  at_from: string;
  at_to: string;
  scope_target: string | null;
  scope_key: string | null;
  changes: ChangeView[];
  edits: AceEditView[];
  unchanged: number;
  unobserved_at_from: number;
  unobserved_at_to: number;
  truncated: boolean;
  summary: ChangeSummaryResponse;
}

export interface AccessSideView {
  at: string;
  access: boolean;
  /**
   * Branch on this, never on `access`. `false` means three different things, and
   * `indeterminate` is the one that must never be rendered as "no access".
   *
   * Spelled out here rather than imported from `lib/derived.ts`: this module is the
   * hand-written mirror of the wire shapes and deliberately depends on nothing.
   */
  outcome: "granted" | "denied" | "no_grant" | "indeterminate";
  conclusive: boolean;
  rights: RightsView;
  certainty: string;
  reason: string;
}

export interface AccessDeltaView {
  subject_key: string;
  resource_key: string;
  access_path: string;
  before: AccessSideView;
  after: AccessSideView;
  gained: RightsView;
  lost: RightsView;
  direction: ChangeDirection;
  certainty: string;
  conclusive: boolean;
}

export interface MembershipDeltaView {
  subject_key: string;
  gained: string[];
  lost: string[];
  before_count: number;
  after_count: number;
  certainty: string;
}

/**
 * Anything but `resolved` means the engine was not given a pair to resolve — not that
 * nothing happened.
 */
export type ImpactVerdict =
  | "resolved"
  | "needs_a_subject"
  | "needs_a_resource"
  | "unbounded"
  | "not_applicable";

export interface ChangeImpactResponse {
  change: ChangeView;
  at_before: string;
  at_after: string;
  verdict: ImpactVerdict;
  explanation: string;
  access: AccessDeltaView | null;
  membership: MembershipDeltaView | null;
}

/* ------------------------------------------------------------------------ governance */

/**
 * The access-review surface.
 *
 * Every conclusion below is the backend's. This application chooses wording and layout and
 * derives nothing: it does not decide whether a grant drifted, whether a reviewer is
 * overdue, whether removing an entry would end somebody's access, or what a rights mask
 * means. Each of those is a sentence or a boolean on the wire, for the reason
 * `docs/contracts/derived-responses.md` gives once — a client that re-derives a conclusion
 * is a second implementation of it, and the two disagree on the day it matters.
 */

export type CampaignFocus = "resource" | "principal";
export type CampaignStatus = "draft" | "active" | "closed" | "canceled";
export type DecisionKind = "certify" | "revoke" | "modify" | "abstain" | "investigate";
export type ReviewItemStatus = "pending" | "decided";
export type CommentRequirement = "standard" | "always";
export type DriftVerdict = "unchanged" | "modified" | "removed" | "unobserved";
export type GrantChangeKind = "added" | "removed" | "changed";
export type FindingRelation = "access" | "target" | "principal";

export interface ScopeView {
  kind: string;
  key: string;
}

export interface GenerationOptionsView {
  include_inherited: boolean;
  include_builtin: boolean;
  include_deny: boolean;
}

export interface CampaignView {
  campaign_id: string;
  name: string;
  description: string | null;
  focus: string;
  status: string;
  baseline_at: string;
  due_at: string | null;
  scopes: ScopeView[];
  options: GenerationOptionsView;
  comment_requirement: string;
  item_count: number;
  /** Grants present at the baseline that produced no item, by reason. Never hide this. */
  excluded_counts: Record<string, number>;
  snapshot_digest: string | null;
  generated_at: string | null;
  activated_at: string | null;
  closed_at: string | null;
  closed_by_subject: string | null;
  created_by_subject: string;
  created_at: string;
}

export interface CampaignListResponse {
  items: CampaignView[];
  page: PageInfo;
}

export interface GrantView {
  ace_key: string;
  trustee_sid: string;
  trustee_key: string;
  ace_type: string;
  access_mask: number | null;
  permission: string | null;
  ace_flags: number | null;
  source: string | null;
  inherited_from: string | null;
  order_index: number | null;
  inherited: boolean;
  deny: boolean;
  version_id: number;
  observed_from: string;
  last_confirmed_at: string;
  certainty: string;
}

export interface ItemView {
  item_id: string;
  campaign_id: string;
  focus: string;
  target_kind: string;
  target_key: string;
  target_path: string | null;
  principal_key: string;
  principal_sid: string;
  principal_display_name: string | null;
  grants: GrantView[];
  evidence_digest: string;
  certainty: string;
  status: string;
  assignment_id: string | null;
  current_decision_id: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface ItemListResponse {
  items: ItemView[];
  page: PageInfo;
}

export interface DecisionView {
  decision_id: string;
  item_id: string;
  campaign_id: string;
  decision: string;
  rationale: string | null;
  decided_by_subject: string;
  decided_by_display_name: string | null;
  decided_at: string;
  decided_late: boolean;
  supersedes_decision_id: string | null;
  superseded_at: string | null;
  superseded_by_decision_id: string | null;
  current: boolean;
}

export interface GrantChangeView {
  kind: string;
  ace_key: string;
  fields: string[];
  /** The same fields in words. Rendered by the backend so every screen agrees. */
  field_labels: string[];
  before: GrantView | null;
  after: GrantView | null;
}

export interface DriftView {
  verdict: string;
  has_drifted: boolean;
  /**
   * One sentence written for the person deciding. Rendered rather than assembled here: the
   * difference between "it was removed" and "nobody has looked" is the one a client would
   * get wrong, and it changes what the reviewer does next.
   */
  summary: string;
  compared_at: string;
  baseline_digest: string;
  current_content_digest: string | null;
  changes: GrantChangeView[];
  current_grants: GrantView[];
  current_certainty: string | null;
  target_present: boolean | null;
  target_certainty: string | null;
  evidence_reissued: boolean;
}

export interface ItemDetailResponse {
  item: ItemView;
  decisions: DecisionView[];
  drift: DriftView;
}

export interface DriftCountsView {
  unchanged: number;
  modified: number;
  removed: number;
  unobserved: number;
}

export interface DriftItemView {
  item: ItemView;
  drift: DriftView;
}

export interface DriftReportResponse {
  campaign_id: string;
  compared_at: string;
  total_items: number;
  /** Items actually compared. Shown beside the total, always. */
  covered: number;
  has_more: boolean;
  counts: DriftCountsView;
  drifted: DriftItemView[];
}

export interface AccessSummaryView {
  at: string;
  available: boolean;
  unavailable_reason: string | null;
  has_access: boolean | null;
  rights: RightsView | null;
  certainty: string | null;
  limiting_layer: string | null;
}

export interface GroupRouteView {
  principal: PrincipalSummary;
  depth: number;
  chain: string[];
  rights: RightsView;
  layer: string;
  inherited: boolean;
}

export interface EntryRemovalView {
  ace_key: string;
  rights_removed: RightsView;
  rights_after: RightsView;
  revokes_all_access: boolean;
  changes_nothing: boolean;
  alternate_paths: number;
}

export interface ReachView {
  available: boolean;
  unavailable_reason: string | null;
  direct_paths: number;
  group_paths: number;
  routes: GroupRouteView[];
  removals: EntryRemovalView[];
  /**
   * `true` when removing this item's entries would leave the principal with access anyway.
   * `null` means no explanation could be produced — never rendered as "removing it works",
   * because that is the reassuring answer and has to be measured.
   */
  removing_reviewed_entries_leaves_access: boolean | null;
  truncated: boolean;
}

export interface RelatedFindingView {
  finding_key: string;
  rule_id: string;
  status: string;
  severity: string;
  band: string;
  confidence: string;
  relation: string;
  detected_at: string;
  first_detected_at: string;
  resource_key: string | null;
  share_key: string | null;
  principal_key: string | null;
  detail: Record<string, unknown>;
}

export interface LastChangeView {
  kind: string;
  key: string;
  action: string;
  significance: string;
  severity: string;
  direction: string;
  reasons: string[];
  changed_after: string | null;
  changed_at_or_before: string | null;
  is_exact: boolean;
}

export interface ItemContextResponse {
  item: ItemView;
  campaign_id: string;
  comment_requirement: string;
  drift: DriftView;
  baseline_access: AccessSummaryView;
  current_access: AccessSummaryView;
  reach: ReachView;
  resolved_resource_key: string | null;
  findings: RelatedFindingView[];
  findings_truncated: boolean;
  changes: LastChangeView[];
  changes_truncated: boolean;
}

export interface BulkDecisionResponse {
  campaign_id: string;
  decision: string;
  item_count: number;
  decisions: DecisionView[];
  note: string;
}

export interface QueueEntryView {
  campaign_id: string;
  name: string;
  focus: string;
  status: string;
  baseline_at: string;
  due_at: string | null;
  assigned: number;
  decided: number;
  pending: number;
  overdue: boolean;
}

export interface QueueResponse {
  subject: string;
  entries: QueueEntryView[];
  total_pending: number;
  overdue_campaigns: number;
}

export interface ReviewerProgressView {
  assignment_id: string;
  reviewer_subject: string;
  reviewer_display_name: string | null;
  scope: ScopeView | null;
  due_at: string | null;
  assigned: number;
  decided: number;
  pending: number;
  completion: number;
  late_decisions: number;
  overdue: boolean;
}

export interface CampaignStatusResponse {
  campaign: CampaignView;
  total_items: number;
  pending_items: number;
  decided_items: number;
  /** Items nobody was asked about. Never folded into the pending total. */
  unassigned_items: number;
  completion: number;
  decisions_by_kind: Record<string, number>;
  overdue: boolean;
  reviewers: ReviewerProgressView[];
  overdue_reviewers: number;
  late_decisions: number;
  audit_head: string | null;
}

export interface AssignmentView {
  assignment_id: string;
  campaign_id: string;
  reviewer_subject: string;
  reviewer_display_name: string | null;
  reviewer_email: string | null;
  scope: ScopeView | null;
  due_at: string | null;
  assigned_by_subject: string;
  assigned_at: string;
  revoked_at: string | null;
  revoked_by_subject: string | null;
  active: boolean;
}

export interface AssignmentListResponse {
  items: AssignmentView[];
}

export interface ProposalView {
  proposal_id: string;
  item_id: string;
  campaign_id: string;
  decision_id: string | null;
  action: string;
  status: string;
  target_kind: string;
  target_key: string;
  principal_key: string;
  ace_keys: string[];
  details: Record<string, unknown>;
  proposed_by_subject: string;
  proposed_at: string;
  /** Constant. Present so a client cannot render a proposal as an action taken. */
  note: string;
}

/* --------------------------------------------------------------------------------
 * Risks (Phase 8B)
 *
 * The four fields below that carry no findings are the important ones. `coverage`,
 * `configuration`, `severity_counts` (including its zeros) and `status_counts` are what
 * keep an empty report from reading as a clean estate — which is this product's own
 * failure mode applied to its own interface.
 * ------------------------------------------------------------------------------ */

export interface FindingSubjectView {
  resource_key: string | null;
  share_key: string | null;
  principal_key: string | null;
  discriminator: string | null;
  kind: string;
}

/**
 * What the last evaluation covered.
 *
 * `has_ever_run: false` is the state a client must never render as a clean report: the
 * rules have not been evaluated, so an empty list means nothing has looked.
 */
export interface RiskCoverageView {
  has_ever_run: boolean;
  evaluated_at: string | null;
  trigger: string | null;
  complete: boolean;
  rules_run: string[];
  rules_skipped: string[];
  truncation: string | null;
  evaluation_id: string | null;
}

export interface RiskConfigurationView {
  version: string;
  rules_enabled: number;
  rules_disabled: string[];
  marks_anything_sensitive: boolean;
}

export interface RemediationView {
  summary: string;
  steps: string[];
  caution: string | null;
}

export interface RiskRuleView {
  rule_id: string;
  title: string;
  detects: string;
  matters_because: string;
  remediation: RemediationView;
  enabled: boolean;
  subject_kind: string;
  version: string;
  severities: Record<string, string>;
  requires_configuration: boolean;
}

export interface FindingSummaryView {
  key: string;
  rule_id: string;
  title: string;
  status: string;
  severity: string;
  confidence: string;
  band: string;
  qualifiers: string[];
  subject: FindingSubjectView;
  detail: Record<string, unknown>;
  first_detected_at: string;
  detected_at: string;
  last_evaluated_at: string;
  resolved_at: string | null;
  occurrence_count: number;
  evidence_count: number;
  evidence_digest: string;
  detail_url: string;
}

export interface EvidenceRecordView {
  kind: string;
  key: string;
  record: Record<string, unknown>;
}

export interface FindingEventView {
  event_type: string;
  occurred_at: string;
  evaluation_id: string;
  rule_version: string;
  severity: string | null;
  confidence: string | null;
  evidence_digest: string | null;
  previous_evidence_digest: string | null;
}

export interface FindingDetailView {
  finding: FindingSummaryView;
  rule: RiskRuleView;
  evidence: EvidenceRecordView[];
  events: FindingEventView[];
  reproduces: boolean;
  reproduction_note: string;
}

export interface RiskSummaryResponse {
  coverage: RiskCoverageView;
  configuration: RiskConfigurationView;
  severity_counts: Record<string, number>;
  rule_counts: Record<string, number>;
  status_counts: Record<string, number>;
  total: number;
}

export interface FindingsResponse {
  filters: Record<string, unknown>;
  coverage: RiskCoverageView;
  configuration: RiskConfigurationView;
  severity_counts: Record<string, number>;
  rule_counts: Record<string, number>;
  status_counts: Record<string, number>;
  items: FindingSummaryView[];
  page: PageInfo;
}

export interface RiskRulesResponse {
  rules: RiskRuleView[];
  configuration: RiskConfigurationView;
}

/* --------------------------------------------------------------------------------
 * Alerts (Phase 8B)
 * ------------------------------------------------------------------------------ */

export interface WatchView {
  watch_id: string;
  kind: string;
  key: string;
  label: string;
  triggers: string[];
  cooldown_seconds: number;
  enabled: boolean;
  notes: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

/** What each kind of watch can be told about. Served, never hard-coded here. */
export interface WatchKindView {
  kind: string;
  triggers: string[];
  covers: string;
}

export interface WatchesResponse {
  watches: WatchView[];
  kinds: WatchKindView[];
  min_cooldown_seconds: number;
  max_cooldown_seconds: number;
  /** What a new watch gets if it does not name one. Served, so a form cannot guess wrong. */
  default_cooldown_seconds: number;
}

export interface AlertView {
  alert_key: string;
  trigger: string;
  trigger_description: string;
  /** `stateful` resolves and can reopen; `transient` never resolves. */
  lifecycle: string;
  status: string;
  summary: string;
  watch_id: string | null;
  watch_label: string | null;
  resource_key: string | null;
  share_key: string | null;
  principal_key: string | null;
  first_raised_at: string;
  last_raised_at: string;
  last_notified_at: string | null;
  resolved_at: string | null;
  occurrence_count: number;
  /** Above zero means this happened more often than anybody was told. */
  suppressed_total: number;
  suppressed_since_notice: number;
  payload: Record<string, unknown>;
  detail_url: string;
}

export interface AlertEventView {
  event_id: string;
  transition: string;
  suppression_reason: string | null;
  notified: boolean;
  folds: number;
  summary: string;
  occurred_at: string;
  recorded_at: string;
  payload_digest: string;
  source_run_id: string | null;
  source_evaluation_id: string | null;
}

export interface AlertDeliveryView {
  delivery_id: string;
  sink_name: string;
  status: string;
  attempts: number;
  last_error: string | null;
  enqueued_at: string;
  next_attempt_at: string;
  delivered_at: string | null;
}

export interface AlertDetailResponse {
  alert: AlertView;
  events: AlertEventView[];
  deliveries: AlertDeliveryView[];
}

export interface AlertsResponse {
  filters: Record<string, unknown>;
  items: AlertView[];
  status_counts: Record<string, number>;
  trigger_counts: Record<string, number>;
  page: PageInfo;
}

export interface AlertQueueResponse {
  depth: Record<string, number>;
  stale: number;
  abandoned: number;
  oldest_pending_at: string | null;
  policy: string[];
}

// ---------------------------------------------------------------- simulations (9B)

/**
 * What-if proposals.
 *
 * `notice` and `applied` appear on every simulation payload and are not decoration: the
 * phase's first acceptance criterion is that the non-destructive nature is unmistakable, and
 * the sentence is served by the API so that three surfaces cannot each word it their own way.
 */
export interface SimulationChangeView {
  kind: string;
  kind_description: string;
  description: string;
  document: Record<string, unknown>;
  group: PrincipalSummary | null;
  member: PrincipalSummary | null;
  trustee: PrincipalSummary | null;
}

export interface SimulationApplicationView {
  change: SimulationChangeView;
  outcome: string;
  outcome_description: string;
  applied: boolean;
  detail: Record<string, string>;
}

export interface SimulationCaveatView {
  code: string;
  description: string;
}

export interface SimulationRouteView {
  layer: string;
  chain: PrincipalSummary[];
  ace_key: string | null;
  ace_position: number;
  rights: RightsView;
  assumed: boolean;
  inherited: boolean;
  via_group: boolean;
}

export interface SimulationResourceView {
  resource_key: string;
  share_key: string | null;
  path: string | null;
  sensitive: boolean;
  sensitivity_labels: string[];
  /** Null when the caller does not hold `alerts:read`. Never a guessed false. */
  watched: boolean | null;
}

export interface SimulationDeltaView {
  subject: PrincipalSummary;
  resource: SimulationResourceView;
  access_path: string;
  direction: string;
  direction_description: string;
  changed: boolean;
  rights_before: RightsView;
  rights_after: RightsView;
  rights_added: RightsView;
  rights_removed: RightsView;
  certainty_before: string;
  certainty_after: string;
  limiting_layer_after: string;
  caveats: SimulationCaveatView[];
  alternate_path_retained: boolean;
  retained_routes: SimulationRouteView[];
}

export interface SimulationSummaryView {
  evaluated: number;
  unchanged: number;
  gained_access: number;
  lost_access: number;
  expanded: number;
  reduced: number;
  changed: number;
  principals_gaining: PrincipalSummary[];
  principals_losing: PrincipalSummary[];
  principals_affected: number;
  resources_affected: string[];
  sensitive_resources_affected: string[];
  watched_resources_affected: string[] | null;
  /**
   * Deltas where the proposal removes one route and another survives. A high number against
   * a removal proposal usually means the change achieves less than it appears to.
   */
  alternate_paths_retained: number;
}

export interface SimulationBaselineView {
  kind: string;
  token: string;
  run_id: string | null;
  at: string | null;
  captured_at: string;
  is_empty: boolean;
  stale: boolean;
  current_token: string;
}

export interface SimulationBoundsView {
  max_principals: number;
  max_resources: number;
  max_pairs: number;
  max_explanations: number;
  time_budget_ms: number;
}

export interface SimulationScopeView {
  kind: string;
  subject_key: string | null;
  resource_key: string | null;
  path: string;
  limit: number;
  after: string | null;
}

export interface SimulationTruncationView {
  code: string;
  description: string;
}

export interface SimulationCostView {
  pairs_evaluated: number;
  resolutions: number;
  explanations: number;
  edges_read: number;
  elapsed_ms: number;
}

export interface SimulationReportView {
  notice: string;
  /** Always false. A field rather than prose so a client can assert on it. */
  applied: false;
  simulation_id: string | null;
  overlay_hash: string;
  change_count: number;
  baseline: SimulationBaselineView;
  scope: SimulationScopeView;
  bounds: SimulationBoundsView;
  applications: SimulationApplicationView[];
  inert: boolean;
  summary: SimulationSummaryView;
  deltas: SimulationDeltaView[];
  complete: boolean;
  truncation: SimulationTruncationView[];
  cost: SimulationCostView;
}

export interface StoredSimulationView {
  simulation_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  change_count: number;
  overlay_hash: string;
  changes: SimulationChangeView[];
  baseline: SimulationBaselineView;
}

export interface SimulationEvaluationView {
  evaluation_id: string;
  simulation_id: string;
  scope_kind: string;
  baseline_token: string;
  stale_baseline: boolean;
  pairs_evaluated: number;
  complete: boolean;
  duration_ms: number;
  computed_at: string;
  report: Record<string, unknown>;
}

export interface StoredSimulationsResponse {
  notice: string;
  simulations: StoredSimulationView[];
  page: PageInfo;
}

export interface SimulationDetailResponse {
  notice: string;
  simulation: StoredSimulationView;
  stale: boolean;
  current_token: string;
  evaluations: SimulationEvaluationView[];
}

export interface StoredSimulationResponse {
  notice: string;
  simulation: StoredSimulationView;
  report: SimulationReportView;
}

export interface SimulationVocabularyEntry {
  code: string;
  description: string;
}

export interface SimulationVocabularyResponse {
  notice: string;
  change_kinds: SimulationVocabularyEntry[];
  inherited_ace_dispositions: SimulationVocabularyEntry[];
  outcomes: SimulationVocabularyEntry[];
  directions: SimulationVocabularyEntry[];
  caveats: SimulationVocabularyEntry[];
  truncations: SimulationVocabularyEntry[];
  scope_kinds: SimulationVocabularyEntry[];
  max_changes: number;
  bounds_ceilings: SimulationBoundsView;
}

export interface SimulationExportResponse {
  document_version: string;
  notice: string;
  exported_at: string;
  plan: {
    simulation_id: string;
    name: string;
    description: string | null;
    created_by: string | null;
    created_at: string;
    overlay_hash: string;
    baseline: SimulationBaselineView;
    changes: SimulationChangeView[];
  };
  result: SimulationEvaluationView | null;
  stale: boolean;
  current_token: string;
  vocabulary: SimulationVocabularyResponse;
}
