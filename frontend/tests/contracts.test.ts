/**
 * The frontend's response types, checked against the backend's published contract.
 *
 * `lib/contracts.ts` is hand-written, which is only defensible if something proves it still
 * matches the API. That something is this file: it reads `docs/contracts/v1/openapi.json` —
 * kept current by the backend's own `test_openapi_snapshot.py` — and asserts, field by
 * field, that every property this application believes in is a property the API declares,
 * and that no required property has been dropped.
 *
 * The failure it exists to catch is the quiet one: the API stops sending a field, the
 * TypeScript type still declares it, `tsc` agrees with the type, and the page renders
 * `undefined` where an auditor expected a SID.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { USED_PATHS } from "@/lib/api/adg";

interface Schema {
  type?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  $ref?: string;
  anyOf?: Schema[];
  allOf?: Schema[];
  items?: Schema;
}

interface OpenApiDocument {
  paths: Record<string, Record<string, unknown>>;
  components: { schemas: Record<string, Schema> };
}

const document = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../docs/contracts/v1/openapi.json", import.meta.url)),
    "utf-8",
  ),
) as OpenApiDocument;

/** Field names this application reads, by the API schema they come from. */
const EXPECTED: Record<string, string[]> = {
  AuthConfigResponse: [
    "mode",
    "development",
    "environment",
    "issuer",
    "client_id",
    "authorization_endpoint",
    "token_endpoint",
    "scopes",
    "development_accounts",
    "roles",
  ],
  RoleDescription: ["role", "active", "capabilities"],
  // Risks (Phase 8B). The four entries below that carry no findings are the important ones:
  // coverage, configuration and the two count maps are what keep an empty report from
  // reading as a clean estate, and a page that believed in one the API stopped sending
  // would silently drop a caveat rather than render `undefined`.
  RiskSummaryResponse: [
    "coverage",
    "configuration",
    "severity_counts",
    "rule_counts",
    "status_counts",
    "total",
  ],
  CoverageView: [
    "has_ever_run",
    "evaluated_at",
    "trigger",
    "complete",
    "rules_run",
    "rules_skipped",
    "truncation",
    "evaluation_id",
  ],
  ConfigurationView: [
    "version",
    "rules_enabled",
    "rules_disabled",
    "marks_anything_sensitive",
  ],
  FindingsResponse: [
    "filters",
    "coverage",
    "configuration",
    "severity_counts",
    "rule_counts",
    "status_counts",
    "items",
    "page",
  ],
  FindingSummaryView: [
    "key",
    "rule_id",
    "title",
    "status",
    "severity",
    "confidence",
    "band",
    "qualifiers",
    "subject",
    "detail",
    "first_detected_at",
    "detected_at",
    "last_evaluated_at",
    "resolved_at",
    "occurrence_count",
    "evidence_count",
    "evidence_digest",
    "detail_url",
  ],
  FindingSubjectView: ["resource_key", "share_key", "principal_key", "discriminator", "kind"],
  FindingDetailView: ["finding", "rule", "evidence", "events", "reproduces", "reproduction_note"],
  EvidenceRecordView: ["kind", "key", "record"],
  FindingEventView: [
    "event_type",
    "occurred_at",
    "evaluation_id",
    "rule_version",
    "severity",
    "confidence",
    "evidence_digest",
    "previous_evidence_digest",
  ],
  RuleConfigView: [
    "rule_id",
    "title",
    "detects",
    "matters_because",
    "remediation",
    "enabled",
    "subject_kind",
    "version",
    "severities",
    "requires_configuration",
  ],
  RemediationView: ["summary", "steps", "caution"],
  RulesResponse: ["rules", "configuration"],
  // Alerts (Phase 8B).
  AlertsResponse: ["filters", "items", "status_counts", "trigger_counts", "page"],
  AlertView: [
    "alert_key",
    "trigger",
    "trigger_description",
    "lifecycle",
    "status",
    "summary",
    "watch_id",
    "watch_label",
    "resource_key",
    "share_key",
    "principal_key",
    "first_raised_at",
    "last_raised_at",
    "last_notified_at",
    "resolved_at",
    "occurrence_count",
    "suppressed_total",
    "suppressed_since_notice",
    "payload",
    "detail_url",
  ],
  AlertDetailView: ["alert", "events", "deliveries"],
  AlertEventView: [
    "event_id",
    "transition",
    "suppression_reason",
    "notified",
    "folds",
    "summary",
    "occurred_at",
    "recorded_at",
    "payload_digest",
    "source_run_id",
    "source_evaluation_id",
  ],
  DeliveryView: [
    "delivery_id",
    "sink_name",
    "status",
    "attempts",
    "last_error",
    "enqueued_at",
    "next_attempt_at",
    "delivered_at",
  ],
  WatchesResponse: [
    "watches",
    "kinds",
    "min_cooldown_seconds",
    "max_cooldown_seconds",
    "default_cooldown_seconds",
  ],
  WatchView: [
    "watch_id",
    "kind",
    "key",
    "label",
    "triggers",
    "cooldown_seconds",
    "enabled",
    "notes",
    "created_by",
    "created_at",
    "updated_at",
  ],
  WatchKindView: ["kind", "triggers", "covers"],
  QueueView: ["depth", "stale", "abandoned", "oldest_pending_at", "policy"],
  PrincipalView: [
    "subject",
    "display_name",
    "email",
    "source",
    "development",
    "roles",
    "capabilities",
    "inactive_roles",
    "unrecognized_roles",
    "expires_at",
  ],
  DevelopmentLoginResponse: ["access_token", "token_type", "expires_at", "principal", "warning"],
  CollectionStatusResponse: ["health", "summary", "concerns", "collectors"],
  CollectorCoverageView: [
    "collector",
    "status",
    "started_at",
    "completed_at",
    "error_count",
    "target",
    "downgrade_reason",
    "trustworthy",
    "concern",
  ],
  CollectionOperationsResponse: [
    "health",
    "summary",
    "notes",
    "scopes",
    "counts",
    "errors",
    "total_errors",
  ],
  ScopeOperationsView: [
    "collector",
    "target",
    "label",
    "completeness",
    "has_ever_succeeded",
    "stale_success",
    "note",
    "latest",
    "last_success",
    "last_failure",
  ],
  RunOutcomeView: [
    "run_id",
    "collector",
    "collector_host",
    "target",
    "status",
    "started_at",
    "completed_at",
    "error_count",
    "batches_reported",
    "batches_received",
    "observations_reported",
    "observations_applied",
    "declared_scopes",
    "reconciled_scopes",
    "incremental",
    "downgrade_reason",
    "completeness",
    "shortfall",
  ],
  ErrorGroupView: [
    "code",
    "count",
    "collectors",
    "latest_occurred_at",
    "sample_targets",
    "is_widespread",
  ],
  ObjectCountsView: [
    "principals",
    "membership_edges",
    "servers",
    "shares",
    "share_aces",
    "directories",
    "ntfs_aces",
    "scan_runs",
    "total",
  ],
  ScanRunListResponse: ["items", "page"],
  ScanRunSummaryView: [
    "run_id",
    "status",
    "incremental",
    "started_at",
    "completed_at",
    "collector",
    "collector_host",
    "method",
    "collector_version",
    "target",
    "mode",
    "batch_count_received",
    "observation_count_applied",
    "error_count",
    "downgrade_reason",
  ],
  PageInfo: ["limit", "has_more", "next_cursor", "total"],
  SearchResponse: [
    "query",
    "interpreted_as",
    "interpretation",
    "limit_per_category",
    "identities",
    "servers",
    "shares",
    "directories",
    "truncated",
    "not_searched",
  ],
  IdentityHitView: [
    "principal_key",
    "sid",
    "kind",
    "display_name",
    "sam_account_name",
    "user_principal_name",
    "host_key",
    "enabled",
    "is_deleted",
  ],
  ServerHitView: ["server_key", "name", "dns_host_name"],
  ShareHitView: ["share_key", "server_key", "name", "unc_path", "share_type", "description"],
  DirectoryHitView: ["resource_key", "path", "server_key", "share_key", "is_acl_boundary"],
  SkippedCategoryView: ["category", "reason"],
  ServersResponse: ["items", "page"],
  ServerSummary: [
    "key",
    "name",
    "dns_host_name",
    "netbios_name",
    "computer_sid",
    "domain_sid",
    "is_domain_member",
    "operating_system",
    "share_count",
  ],
  ReadinessResponse: ["status", "version", "dependencies"],
  DependencyStatus: ["name", "ok", "latency_ms", "error"],
  VersionResponse: ["name", "version", "environment"],

  // Detail views. These are long on purpose: the second assertion below fails when the API
  // grows a required field the frontend ignores, and a short list would pass by saying
  // nothing. Every name here is a field some page actually renders.
  PrincipalSummary: [
    "display_name",
    "distinguished_name",
    "enabled",
    "group_scope",
    "group_type",
    "host_key",
    "is_deleted",
    "is_group",
    "key",
    "kind",
    "last_known_name",
    "resolved",
    "sam_account_name",
    "sid",
    "unresolved_reason",
    "user_principal_name",
  ],
  PrincipalDetail: [
    "aliases",
    "direct_group_count",
    "direct_member_count",
    "display_name",
    "distinguished_name",
    "domain_sid",
    "enabled",
    "first_observed_at",
    "first_observed_run_id",
    "group_scope",
    "group_type",
    "host_key",
    "is_deleted",
    "is_group",
    "key",
    "kind",
    "last_known_name",
    "last_observed_at",
    "last_observed_run_id",
    "resolved",
    "sam_account_name",
    "sid",
    "unresolved_reason",
    "user_principal_name",
  ],
  AliasView: ["alias_kind", "first_observed_at", "last_observed_at", "value"],
  ProvenanceView: [
    "first_observed_at",
    "first_observed_run_id",
    "last_observed_at",
    "last_observed_run_id",
    "source_key",
  ],
  DirectGroupsResponse: ["items", "page", "principal"],
  EffectiveGroupsResponse: ["cycles", "items", "page", "principal", "traversal"],
  DirectMembersResponse: ["group", "items", "page"],
  EffectiveMembersResponse: ["cycles", "group", "include", "items", "page", "traversal"],
  DirectMemberView: [
    "edge_key",
    "edge_kind",
    "first_observed_at",
    "host_key",
    "is_foreign_security_principal",
    "last_observed_at",
    "last_observed_run_id",
    "principal",
  ],
  EffectiveMemberView: [
    "depth",
    "edge_kinds",
    "path",
    "principal",
    "via_foreign_security_principal",
  ],
  CycleView: ["members", "representative_path"],
  TraversalView: [
    "complete",
    "depth_reached",
    "edges_read",
    "limits",
    "nodes_visited",
    "truncation",
  ],
  LimitsView: ["max_depth", "max_edges", "max_nodes", "max_paths"],
  ServerDetail: [
    "computer_sid",
    "dns_host_name",
    "domain_sid",
    "is_domain_member",
    "key",
    "name",
    "netbios_name",
    "operating_system",
    "provenance",
    "share_count",
  ],
  SharesResponse: ["items", "page", "server"],
  ShareSummary: [
    "caching_mode",
    "carries_file_permissions",
    "concurrent_user_limit",
    "description",
    "is_administrative",
    "is_hidden",
    "is_special",
    "key",
    "local_path",
    "name",
    "server_key",
    "share_type",
    "unc_path",
  ],
  ShareDetailView: [
    "ace_count",
    "caching_mode",
    "carries_file_permissions",
    "concurrent_user_limit",
    "description",
    "is_administrative",
    "is_hidden",
    "is_special",
    "key",
    "local_path",
    "name",
    "provenance",
    "root_resource",
    "server",
    "server_key",
    "share_type",
    "unc_path",
  ],
  RawShareAclResponse: ["entries", "kind", "page", "share", "share_key"],
  ShareAceView: [
    "access_mask",
    "ace_key",
    "ace_type",
    "order_index",
    "permission",
    "provenance",
    "right",
    "trustee",
  ],
  RawNtfsAclResponse: ["acl_hash", "entries", "kind", "page", "resource", "resource_key"],
  NtfsAceView: [
    "access_mask",
    "ace_flags",
    "ace_key",
    "ace_type",
    "applies_to_this_object",
    "inherited_from",
    "is_inheritable",
    "order_index",
    "provenance",
    "rights",
    "source",
    "trustee",
    "unrecognized_bits",
  ],
  AclHashView: [
    "ace_count_agrees",
    "agrees",
    "algorithm",
    "computed",
    "declared_ace_count",
    "normal_form_version",
    "ordered",
    "reported",
    "stored_ace_count",
  ],
  NtfsResourceSummary: [
    "boundary_reason",
    "dacl_present",
    "dacl_protected",
    "declared_ace_count",
    "denies_everyone",
    "depth_from_share_root",
    "grants_everyone_full_access",
    "group_sid",
    "inheritance_enabled",
    "is_acl_boundary",
    "is_share_root",
    "key",
    "local_path",
    "owner_sid",
    "parent_path",
    "path",
    "resource_kind",
    "server_key",
    "share_key",
  ],
  NtfsResourceDetailView: [
    "boundary",
    "boundary_reason",
    "dacl_present",
    "dacl_protected",
    "declared_ace_count",
    "denies_everyone",
    "depth_from_share_root",
    "grants_everyone_full_access",
    "group_sid",
    "inheritance_enabled",
    "is_acl_boundary",
    "is_share_root",
    "key",
    "local_path",
    "owner_sid",
    "parent",
    "parent_path",
    "path",
    "provenance",
    "resource_kind",
    "server",
    "server_key",
    "share",
    "share_key",
    "stored_ace_count",
  ],
  BoundaryView: [
    "agrees",
    "computed",
    "computed_reason",
    "parent_acl_hash",
    "parent_acl_hash_agrees",
    "parent_key",
    "parent_observed",
    "projected_child_acl_hash",
    "projection_available",
    "reported",
    "reported_parent_acl_hash",
    "reported_reason",
    "resource_acl_hash",
  ],
  TrusteeSharesResponse: ["items", "kind", "page", "scope", "trustee"],
  TrusteeShareView: ["aces", "share", "share_key"],
  PrincipalResourcesResponse: ["access_path", "items", "page", "subject", "token"],
  ResourceAccessView: [
    "access",
    "certainty",
    "conditions",
    "limiting_layer",
    "resource",
    "rights",
    "share",
  ],
  ResourceRef: [
    "dacl_present",
    "dacl_protected",
    "is_acl_boundary",
    "key",
    "observed",
    "owner_sid",
    "path",
    "share_key",
  ],
  ShareRef: ["key", "name", "observed", "server_key"],
  RightsView: [
    "categories",
    "escalation_rights",
    "extra_rights",
    "indeterminate",
    "is_exact",
    "label",
    "layer",
    "mask",
    "primary",
    "unrecognized_bits",
    "value",
  ],
  TokenView: ["access_path", "assumption", "entries", "membership_complete", "subject"],
  TokenEntryView: ["assumed", "depth", "origin", "path", "principal"],
  ResourcePrincipalsResponse: [
    "access_path",
    "enumeration",
    "findings",
    "items",
    "page",
    "resource",
    "share",
  ],
  PrincipalAccessView: [
    "access",
    "certainty",
    "conditions",
    "limiting_layer",
    "principal",
    "rights",
    "via",
  ],
  EnumerationView: ["complete", "trustees_truncated", "unenumerable_trustees"],
  FindingView: ["condition", "detail", "may_overstate", "may_understate", "message"],
  // The access-review surface. Long on purpose, like the detail views above: the second
  // assertion below fails when the API grows a required field the frontend ignores, and a
  // short list would pass by saying nothing. Governance is where that matters most — a
  // field the screen stops showing is a fact a reviewer stops being told.
  CampaignView: ["activated_at", "baseline_at", "campaign_id", "closed_at", "closed_by_subject", "comment_requirement", "created_at", "created_by_subject", "description", "due_at", "excluded_counts", "focus", "generated_at", "item_count", "name", "options", "scopes", "snapshot_digest", "status"],
  CampaignListResponse: ["items", "page"],
  ScopeView: ["key", "kind"],
  GenerationOptionsBody: ["include_builtin", "include_deny", "include_inherited"],
  GrantView: ["access_mask", "ace_flags", "ace_key", "ace_type", "certainty", "deny", "inherited", "inherited_from", "last_confirmed_at", "observed_from", "order_index", "permission", "source", "trustee_key", "trustee_sid", "version_id"],
  ItemView: ["assignment_id", "campaign_id", "certainty", "created_at", "current_decision_id", "decided_at", "evidence_digest", "focus", "grants", "item_id", "principal_display_name", "principal_key", "principal_sid", "status", "target_key", "target_kind", "target_path"],
  ItemListResponse: ["items", "page"],
  DecisionView: ["campaign_id", "current", "decided_at", "decided_by_display_name", "decided_by_subject", "decided_late", "decision", "decision_id", "item_id", "rationale", "superseded_at", "superseded_by_decision_id", "supersedes_decision_id"],
  GrantChangeView: ["ace_key", "after", "before", "field_labels", "fields", "kind"],
  DriftView: ["baseline_digest", "changes", "compared_at", "current_certainty", "current_content_digest", "current_grants", "evidence_reissued", "has_drifted", "summary", "target_certainty", "target_present", "verdict"],
  ItemDetailResponse: ["decisions", "drift", "item"],
  DriftCountsView: ["modified", "removed", "unchanged", "unobserved"],
  DriftItemView: ["drift", "item"],
  DriftReportResponse: ["campaign_id", "compared_at", "counts", "covered", "drifted", "has_more", "total_items"],
  AccessSummaryView: ["at", "available", "certainty", "has_access", "limiting_layer", "rights", "unavailable_reason"],
  GroupRouteView: ["chain", "depth", "inherited", "layer", "principal", "rights"],
  EntryRemovalView: ["ace_key", "alternate_paths", "changes_nothing", "revokes_all_access", "rights_after", "rights_removed"],
  ReachView: ["available", "direct_paths", "group_paths", "removals", "removing_reviewed_entries_leaves_access", "routes", "truncated", "unavailable_reason"],
  RelatedFindingView: ["band", "confidence", "detail", "detected_at", "finding_key", "first_detected_at", "principal_key", "relation", "resource_key", "rule_id", "severity", "share_key", "status"],
  LastChangeView: ["action", "changed_after", "changed_at_or_before", "direction", "is_exact", "key", "kind", "reasons", "severity", "significance"],
  ItemContextResponse: ["baseline_access", "campaign_id", "changes", "changes_truncated", "comment_requirement", "current_access", "drift", "findings", "findings_truncated", "item", "reach", "resolved_resource_key"],
  BulkDecisionResponse: ["campaign_id", "decision", "decisions", "item_count", "note"],
  QueueEntryView: ["assigned", "baseline_at", "campaign_id", "decided", "due_at", "focus", "name", "overdue", "pending", "status"],
  QueueResponse: ["entries", "overdue_campaigns", "subject", "total_pending"],
  ReviewerProgressView: ["assigned", "assignment_id", "completion", "decided", "due_at", "late_decisions", "overdue", "pending", "reviewer_display_name", "reviewer_subject", "scope"],
  CampaignStatusResponse: ["audit_head", "campaign", "completion", "decided_items", "decisions_by_kind", "late_decisions", "overdue", "overdue_reviewers", "pending_items", "reviewers", "total_items", "unassigned_items"],
  // Simulations (Phase 9B). `notice` and `applied` are the two that matter most: the phase's
  // first acceptance criterion is that the non-destructive nature is unmistakable, and a
  // field the API stopped sending would leave the page rendering nothing where the sentence
  // belongs. `alternate_paths_retained` and the caveat list are the second pair -- they are
  // what stop a removal report being read as "this revokes access" when it does not.
  SimulationReportView: ["applications", "applied", "baseline", "bounds", "change_count", "complete", "cost", "deltas", "inert", "notice", "overlay_hash", "scope", "simulation_id", "summary", "truncation"],
  SimulationChangeView: ["description", "document", "group", "kind", "kind_description", "member", "trustee"],
  SimulationApplicationView: ["applied", "change", "detail", "outcome", "outcome_description"],
  SimulationCaveatView: ["code", "description"],
  SimulationRouteView: ["ace_key", "ace_position", "assumed", "chain", "inherited", "layer", "rights", "via_group"],
  SimulationResourceView: ["path", "resource_key", "sensitive", "sensitivity_labels", "share_key", "watched"],
  SimulationDeltaView: ["access_path", "alternate_path_retained", "caveats", "certainty_after", "certainty_before", "changed", "direction", "direction_description", "limiting_layer_after", "resource", "retained_routes", "rights_added", "rights_after", "rights_before", "rights_removed", "subject"],
  SimulationSummaryView: ["alternate_paths_retained", "changed", "evaluated", "expanded", "gained_access", "lost_access", "principals_affected", "principals_gaining", "principals_losing", "reduced", "resources_affected", "sensitive_resources_affected", "unchanged", "watched_resources_affected"],
  SimulationBaselineView: ["at", "captured_at", "current_token", "is_empty", "kind", "run_id", "stale", "token"],
  SimulationScopeView: ["after", "kind", "limit", "path", "resource_key", "subject_key"],
  SimulationBoundsView: ["max_explanations", "max_pairs", "max_principals", "max_resources", "time_budget_ms"],
  SimulationCostView: ["edges_read", "elapsed_ms", "explanations", "pairs_evaluated", "resolutions"],
  SimulationTruncationView: ["code", "description"],
  StoredSimulationView: ["baseline", "change_count", "changes", "created_at", "created_by", "description", "name", "overlay_hash", "simulation_id", "updated_at"],
  StoredSimulationsResponse: ["notice", "page", "simulations"],
  StoredSimulationResponse: ["notice", "report", "simulation"],
  SimulationDetailView: ["current_token", "evaluations", "notice", "simulation", "stale"],
  SimulationEvaluationView: ["baseline_token", "complete", "computed_at", "duration_ms", "evaluation_id", "pairs_evaluated", "report", "scope_kind", "simulation_id", "stale_baseline"],
  SimulationVocabularyEntry: ["code", "description"],
  SimulationVocabularyResponse: ["bounds_ceilings", "caveats", "change_kinds", "directions", "inherited_ace_dispositions", "max_changes", "notice", "outcomes", "scope_kinds", "truncations"],
  SimulationExportView: ["current_token", "document_version", "exported_at", "notice", "plan", "result", "stale", "vocabulary"],
  SimulationPlanView: ["baseline", "changes", "created_at", "created_by", "description", "name", "overlay_hash", "simulation_id"],
};

describe("the published contract", () => {
  it("is present and readable", () => {
    expect(Object.keys(document.paths).length).toBeGreaterThan(20);
  });

  it("serves every path this application calls", () => {
    const served = new Set(Object.keys(document.paths));
    const missing = USED_PATHS.filter((path) => !served.has(path));

    expect(missing).toEqual([]);
  });
});

describe.each(Object.entries(EXPECTED))("the %s schema", (name, fields) => {
  it("is declared by the API", () => {
    expect(document.components.schemas[name]).toBeDefined();
  });

  it("declares every field this application reads", () => {
    const declared = Object.keys(document.components.schemas[name]?.properties ?? {});
    const missing = fields.filter((field) => !declared.includes(field));

    expect(missing, `${name} is missing: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no required field this application ignores", () => {
    // The direction that catches a *new* field the frontend should be showing, rather than
    // an old one it should stop showing.
    const required = document.components.schemas[name]?.required ?? [];
    const ignored = required.filter((field) => !fields.includes(field));

    expect(ignored, `${name} requires fields lib/contracts.ts does not carry`).toEqual([]);
  });
});

describe("the search response", () => {
  it("declares exactly the interpretations the union allows", () => {
    // `interpreted_as` is a TypeScript union; a value the API can send and the union cannot
    // hold would be a type error only at runtime.
    const schema = document.components.schemas.SearchResponse?.properties?.interpreted_as as
      | { enum?: string[] }
      | undefined;

    expect(schema?.enum?.sort()).toEqual(
      ["name", "server", "share", "sid", "unc_path"].sort(),
    );
  });
});

describe("the access vocabulary", () => {
  // These three unions decide the wording of an answer. A value the API can send and the
  // union cannot hold would fall through every switch in `lib/access.ts` at runtime, in
  // the one place this product must not be vague.
  it.each([
    ["AccessCertainty", ["at_least", "at_most", "certain", "uncertain"]],
    ["LimitingLayer", ["both", "none", "ntfs", "smb_share", "unknown"]],
    ["MemberInclusion", ["all", "non_groups", "users"]],
  ])("declares exactly the %s values the union allows", (name, expected) => {
    const schema = document.components.schemas[name] as { enum?: string[] } | undefined;

    expect(schema?.enum?.slice().sort()).toEqual(expected);
  });
});

describe("the review vocabulary", () => {
  // Three unions that decide what a reviewer is shown. A value the API can send and the
  // union cannot hold would fall through every switch in `lib/governance.ts` at runtime —
  // and the switch that would fall through is the one separating "it was removed" from
  // "nobody has looked", which is the distinction this product exists to keep.
  it.each([
    ["DecisionKind", ["abstain", "certify", "investigate", "modify", "revoke"]],
    ["CommentRequirement", ["always", "standard"]],
  ])("declares exactly the %s values the union allows", (name, expected) => {
    const schema = document.components.schemas[name] as { enum?: string[] } | undefined;

    expect(schema?.enum?.slice().sort()).toEqual(expected);
  });

  it("declares every drift verdict the drift renderer branches on", () => {
    // Not an enum on the wire: the verdict is a plain string on DriftView, so the check is
    // that the description still names all four. A fifth verdict added without a fifth
    // branch would render as "unchanged", which is the worst possible default.
    const schema = document.components.schemas.DriftView?.properties?.verdict as
      | { description?: string }
      | undefined;

    for (const verdict of ["unchanged", "modified", "removed", "unobserved"]) {
      expect(schema?.description).toContain(verdict);
    }
  });
});

describe("the collection status", () => {
  it("declares exactly the health values the union allows", () => {
    const schema = document.components.schemas.CollectionStatusResponse?.properties?.health as
      | { enum?: string[] }
      | undefined;

    expect(schema?.enum?.sort()).toEqual(["failed", "healthy", "incomplete", "no_data"].sort());
  });
});
