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

describe("the collection status", () => {
  it("declares exactly the health values the union allows", () => {
    const schema = document.components.schemas.CollectionStatusResponse?.properties?.health as
      | { enum?: string[] }
      | undefined;

    expect(schema?.enum?.sort()).toEqual(["failed", "healthy", "incomplete", "no_data"].sort());
  });
});
