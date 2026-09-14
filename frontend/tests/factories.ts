/**
 * Fixtures for the detail views.
 *
 * Every builder starts from the *uninformative* value — null kind, null enabled, resolved
 * true only because most rows are — so a test that cares about a field has to say so. The
 * alternative, a fully populated default, quietly hides the cases this application exists
 * to render honestly: a SID nothing has described, a group whose kind nobody recorded, a
 * directory whose parent was never read.
 */

import type {
  AclHashView,
  BoundaryView,
  NtfsAceView,
  NtfsResourceDetailView,
  NtfsResourceSummary,
  PageInfo,
  PrincipalDetail,
  PrincipalSummary,
  ProvenanceView,
  RightsView,
  ShareAceView,
  ShareDetailView,
  ShareSummary,
} from "@/lib/contracts";

export const RUN_ID = "11111111-2222-3333-4444-555555555555";

export const provenance: ProvenanceView = {
  source_key: "smb:fs01",
  first_observed_at: "2026-09-01T10:00:00Z",
  first_observed_run_id: RUN_ID,
  last_observed_at: "2026-09-12T10:00:00Z",
  last_observed_run_id: RUN_ID,
};

export const page = (over: Partial<PageInfo> = {}): PageInfo => ({
  limit: 100,
  has_more: false,
  next_cursor: null,
  total: null,
  ...over,
});

export function principal(over: Partial<PrincipalSummary> = {}): PrincipalSummary {
  return {
    key: "S-1-5-21-1-2-3-1104",
    sid: "S-1-5-21-1-2-3-1104",
    resolved: true,
    kind: null,
    is_group: null,
    display_name: null,
    sam_account_name: null,
    user_principal_name: null,
    distinguished_name: null,
    last_known_name: null,
    host_key: null,
    enabled: null,
    is_deleted: false,
    group_scope: null,
    group_type: null,
    unresolved_reason: null,
    ...over,
  };
}

export function principalDetail(over: Partial<PrincipalDetail> = {}): PrincipalDetail {
  return {
    ...principal(),
    domain_sid: "S-1-5-21-1-2-3",
    direct_member_count: 0,
    direct_group_count: 0,
    aliases: [],
    first_observed_at: "2026-09-01T10:00:00Z",
    first_observed_run_id: RUN_ID,
    last_observed_at: "2026-09-12T10:00:00Z",
    last_observed_run_id: RUN_ID,
    ...over,
  };
}

export function rights(over: Partial<RightsView> = {}): RightsView {
  return {
    mask: "0x001200A9",
    value: 0x001200a9,
    layer: "ntfs",
    label: "Read & Execute",
    primary: "read",
    categories: ["read"],
    is_exact: true,
    extra_rights: [],
    escalation_rights: [],
    unrecognized_bits: null,
    indeterminate: false,
    ...over,
  };
}

export function ntfsAce(over: Partial<NtfsAceView> = {}): NtfsAceView {
  return {
    ace_key: "ace-1",
    trustee: principal({ display_name: "Finance" }),
    ace_type: "allow",
    access_mask: 0x001200a9,
    rights: ["READ_DATA", "EXECUTE"],
    unrecognized_bits: 0,
    ace_flags: 0x13,
    source: "inherited",
    inherited_from: "\\\\FS01\\Finance",
    is_inheritable: true,
    applies_to_this_object: true,
    order_index: 0,
    provenance,
    ...over,
  };
}

export function shareAce(over: Partial<ShareAceView> = {}): ShareAceView {
  return {
    ace_key: "share-ace-1",
    trustee: principal({ display_name: "Everyone", sid: "S-1-1-0", key: "S-1-1-0" }),
    ace_type: "allow",
    right: "change",
    permission: "change",
    access_mask: null,
    order_index: 0,
    provenance,
    ...over,
  };
}

export function shareSummary(over: Partial<ShareSummary> = {}): ShareSummary {
  return {
    key: "fs01|finance",
    server_key: "fs01",
    name: "Finance",
    unc_path: "\\\\FS01\\Finance",
    share_type: "disk",
    description: null,
    local_path: "D:\\Shares\\Finance",
    caching_mode: null,
    concurrent_user_limit: null,
    is_hidden: false,
    is_special: null,
    is_administrative: false,
    carries_file_permissions: true,
    ...over,
  };
}

export function resourceSummary(over: Partial<NtfsResourceSummary> = {}): NtfsResourceSummary {
  return {
    key: "\\\\fs01\\finance",
    path: "\\\\FS01\\Finance",
    server_key: "fs01",
    share_key: "fs01|finance",
    local_path: "D:\\Shares\\Finance",
    parent_path: null,
    resource_kind: "directory",
    is_share_root: true,
    depth_from_share_root: 0,
    owner_sid: "S-1-5-32-544",
    group_sid: null,
    dacl_present: true,
    dacl_protected: false,
    inheritance_enabled: true,
    is_acl_boundary: false,
    boundary_reason: null,
    grants_everyone_full_access: false,
    denies_everyone: false,
    declared_ace_count: 4,
    ...over,
  };
}

export function boundary(over: Partial<BoundaryView> = {}): BoundaryView {
  return {
    reported: false,
    reported_reason: null,
    computed: false,
    computed_reason: null,
    agrees: true,
    parent_key: "\\\\fs01\\finance",
    parent_observed: true,
    parent_acl_hash: "sha256:parent",
    reported_parent_acl_hash: "sha256:parent",
    parent_acl_hash_agrees: true,
    projection_available: true,
    projected_child_acl_hash: "sha256:child",
    resource_acl_hash: "sha256:child",
    ...over,
  };
}

export function resourceDetail(
  over: Partial<NtfsResourceDetailView> = {},
): NtfsResourceDetailView {
  return {
    ...resourceSummary(),
    stored_ace_count: 4,
    boundary: boundary(),
    provenance,
    parent: null,
    server: null,
    share: null,
    ...over,
  };
}

export function shareDetail(over: Partial<ShareDetailView> = {}): ShareDetailView {
  return {
    ...shareSummary(),
    ace_count: 1,
    provenance,
    server: null,
    root_resource: resourceSummary(),
    ...over,
  };
}

export function aclHash(over: Partial<AclHashView> = {}): AclHashView {
  return {
    computed: "sha256:computed",
    reported: "sha256:computed",
    agrees: true,
    algorithm: "sha256",
    normal_form_version: "1",
    ordered: true,
    declared_ace_count: 4,
    stored_ace_count: 4,
    ace_count_agrees: true,
    ...over,
  };
}
