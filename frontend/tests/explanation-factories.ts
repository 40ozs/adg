/**
 * Explanations to render, built by hand.
 *
 * Deliberately its own module rather than an extension of `tests/factories.ts`: these are
 * bodies of the *derived* contract, which versions separately, and a fixture that drifted
 * would be checked against nothing. `tests/explanation-contract.test.ts` checks the types
 * against the published schema; these check the rendering against realistic shapes of
 * those types.
 *
 * The four scenarios are the four an auditor actually meets, and three of them are the
 * ones a careless screen gets wrong:
 *
 * - `twoRoutes` — access arrives twice, so no single removal is a fix.
 * - `denied` — a Deny withheld what an Allow would have given, and removing it would
 *   *widen* access.
 * - `noGrant` — nothing names this principal. There is no control here to remove.
 * - `indeterminate` — nothing was established and collection is incomplete. Not "no".
 */

import type {
  FindingView,
  PrincipalSummary,
  ResourceRef,
  RightsView,
  ShareRef,
  TokenView,
} from "@/lib/contracts";
import type {
  AccessExplanationResponse,
  AppliedAceView,
  CausalPathView,
  EdgeKind,
  ExplanationEdgeView,
  ExplanationNodeView,
  NodeKind,
  RemovalTargetView,
  VerdictView,
} from "@/lib/derived";

const DOMAIN = "S-1-5-21-1004336348-1177238915-682003330";
export const ALICE_SID = `${DOMAIN}-1104`;
export const TEAM_SID = `${DOMAIN}-1201`;
export const RW_SID = `${DOMAIN}-1202`;
export const RESOURCE_KEY = "\\\\fs01\\finance";
export const RESOURCE_PATH = "\\\\FS01\\Finance";

export function rights(overrides: Partial<RightsView> = {}): RightsView {
  return {
    mask: "0x001301BF",
    value: 1245631,
    layer: "effective",
    label: "Modify",
    primary: "Modify",
    categories: ["Modify"],
    is_exact: true,
    extra_rights: [],
    escalation_rights: [],
    unrecognized_bits: null,
    indeterminate: false,
    ...overrides,
  };
}

export const NO_RIGHTS = rights({
  mask: "0x00000000",
  value: 0,
  label: "No access",
  primary: "No access",
  categories: [],
});

export function principal(overrides: Partial<PrincipalSummary> = {}): PrincipalSummary {
  return {
    key: ALICE_SID,
    sid: ALICE_SID,
    resolved: true,
    kind: "user",
    is_group: false,
    display_name: "Alice Smith",
    sam_account_name: null,
    user_principal_name: null,
    distinguished_name: null,
    last_known_name: null,
    host_key: null,
    enabled: true,
    is_deleted: false,
    group_scope: null,
    group_type: null,
    unresolved_reason: null,
    ...overrides,
  };
}

export const ALICE = principal();
export const TEAM = principal({
  key: TEAM_SID,
  sid: TEAM_SID,
  kind: "domain_group",
  is_group: true,
  display_name: "Finance-Team",
  enabled: null,
  group_scope: "global",
  group_type: "security",
});
export const FINANCE_RW = principal({
  key: RW_SID,
  sid: RW_SID,
  kind: "domain_group",
  is_group: true,
  display_name: "Finance-RW",
  enabled: null,
  group_scope: "domain_local",
  group_type: "security",
});

export function node(
  id: string,
  kind: NodeKind,
  overrides: Partial<ExplanationNodeView> = {},
): ExplanationNodeView {
  return {
    id,
    kind,
    key: id,
    sid: null,
    display_name: null,
    layer: null,
    position: null,
    ...overrides,
  };
}

export function edge(
  id: string,
  kind: EdgeKind,
  source: string,
  target: string,
  overrides: Partial<ExplanationEdgeView> = {},
): ExplanationEdgeView {
  return {
    id,
    kind,
    source,
    target,
    removable: kind !== "assumed_membership" && kind !== "grant" && kind !== "deny",
    membership_edge_key: null,
    ace_key: null,
    ...overrides,
  };
}

export function path(overrides: Partial<CausalPathView> = {}): CausalPathView {
  return {
    id: "p0000",
    layer: "ntfs",
    relation: "grant",
    effect: "contributes",
    ace_rights: rights({ layer: "ntfs" }),
    layer_rights: rights({ layer: "ntfs" }),
    constrained_rights: NO_RIGHTS,
    effective_rights: rights(),
    ace_key: "ace-0",
    ace_position: 0,
    inherited: false,
    via_group: true,
    assumed: false,
    chain: [ALICE],
    nodes: [],
    edges: [],
    ...overrides,
  };
}

export function applied(overrides: Partial<AppliedAceView> = {}): AppliedAceView {
  return {
    layer: "ntfs",
    position: 0,
    ace_type: "allow",
    ace_key: "ace-0",
    access_mask: "0x001301BF",
    contributed: "0x001301BF",
    flags: 3,
    source: "explicit",
    inherited_from: null,
    matched_key: RW_SID,
    trustee: FINANCE_RW,
    via_group: true,
    ...overrides,
  };
}

export function removal(overrides: Partial<RemovalTargetView> = {}): RemovalTargetView {
  return {
    edge_id: "m1",
    kind: "membership",
    source: `principal:${ALICE_SID}`,
    target: `group:${TEAM_SID}`,
    rights_after: NO_RIGHTS,
    rights_removed: rights(),
    rights_added: NO_RIGHTS,
    changes_nothing: false,
    revokes_all_access: true,
    paths_removed: ["p0000"],
    alternate_paths: [],
    ...overrides,
  };
}

export function verdict(overrides: Partial<VerdictView> = {}): VerdictView {
  return {
    outcome: "granted",
    certainty: "certain",
    conclusive: true,
    may_overstate: false,
    may_understate: false,
    reason: "This principal holds rights on this object.",
    denials: [],
    ...overrides,
  };
}

export function token(overrides: Partial<TokenView> = {}): TokenView {
  return {
    subject: ALICE,
    assumption: "authenticated_user",
    access_path: "remote_smb",
    membership_complete: true,
    entries: [],
    ...overrides,
  };
}

export function resource(overrides: Partial<ResourceRef> = {}): ResourceRef {
  return {
    key: RESOURCE_KEY,
    path: RESOURCE_PATH,
    share_key: "fs01|finance",
    observed: true,
    owner_sid: null,
    dacl_present: true,
    dacl_protected: false,
    is_acl_boundary: true,
    ...overrides,
  };
}

export function share(overrides: Partial<ShareRef> = {}): ShareRef {
  return {
    key: "fs01|finance",
    name: "Finance",
    server_key: "fs01",
    observed: true,
    ...overrides,
  };
}

export function finding(overrides: Partial<FindingView> = {}): FindingView {
  return {
    condition: "assumed_token_sids",
    message: "Well-known SIDs that Windows places in every token of this kind were added.",
    may_overstate: false,
    may_understate: false,
    ...overrides,
  };
}

export function explanation(
  overrides: Partial<AccessExplanationResponse> = {},
): AccessExplanationResponse {
  return {
    schema_version: "1.0",
    basis: {
      token: "0a6c0ae82c06105bc9e807805d354570",
      runs: 1,
      latest_run_id: "67fccf66-d30a-4804-a1c1-6c00b414dead",
      latest_activity_at: "2026-09-14T21:13:52.961056Z",
      observations_applied: 10,
      batches_received: 1,
      is_empty: false,
    },
    subject: ALICE,
    resource: resource(),
    share: share(),
    access_path: "remote_smb",
    verdict: verdict(),
    token: token(),
    effective: {
      resource_key: RESOURCE_KEY,
      share_key: "fs01|finance",
      access_path: "remote_smb",
      access: true,
      rights: rights(),
      certainty: "certain",
      limiting_layer: "ntfs",
      acl_provenance: "observed",
      conditions: [],
      findings: [],
      ntfs: {
        layer: "ntfs",
        rights: rights({ layer: "ntfs" }),
        canonical_rights: rights({ layer: "ntfs" }),
        entries_supplied: 1,
        entries_evaluated: 1,
        order_dependent: false,
        owner_rights: null,
        granted_by: [applied()],
        denied_by: [],
        superseded: [],
      },
      share: {
        layer: "smb_share",
        rights: rights({
          layer: "smb_share",
          mask: "0x001F01FF",
          value: 2032127,
          label: "Full Control",
          primary: "Full Control",
          categories: ["Full Control"],
          escalation_rights: ["WRITE_DAC", "WRITE_OWNER"],
        }),
        canonical_rights: null,
        entries_supplied: 1,
        entries_evaluated: 1,
        order_dependent: false,
        owner_rights: null,
        granted_by: [],
        denied_by: [],
        superseded: [],
      },
    },
    graph: { nodes: [], edges: [] },
    paths: [],
    removal_targets: [],
    cycles: [],
    limits: {
      max_depth: 32,
      max_paths: 200,
      max_paths_per_trustee: 100,
      max_removal_targets: 64,
    },
    complete: true,
    truncation: [],
    warnings: [],
    ...overrides,
  };
}

/* --------------------------------------------------------------- the four scenarios */

const ALICE_NODE = `principal:${ALICE_SID}`;
const TEAM_NODE = `group:${TEAM_SID}`;
const NTFS_ACE_TEAM = `ntfs-ace:${RESOURCE_KEY}:0000`;
const NTFS_ACE_ALICE = `ntfs-ace:${RESOURCE_KEY}:0001`;
const RESOURCE_NODE = `resource:${RESOURCE_KEY}`;

/**
 * Access that arrives twice.
 *
 * Alice is named on the ACL directly *and* reaches a second entry through Finance-Team.
 * Every single removal therefore changes nothing, which is the state this screen must not
 * let anybody mistake for a fix.
 */
export function twoRoutes(): AccessExplanationResponse {
  const teamAce = applied({
    ace_key: "ntfs|team",
    position: 0,
    matched_key: TEAM_SID,
    trustee: TEAM,
    via_group: true,
    source: "inherited",
    inherited_from: "\\\\FS01\\Finance",
  });
  const aliceAce = applied({
    ace_key: "ntfs|alice",
    position: 1,
    matched_key: ALICE_SID,
    trustee: ALICE,
    via_group: false,
  });

  const base = explanation();
  return {
    ...base,
    effective: {
      ...base.effective,
      ntfs: {
        ...base.effective.ntfs,
        entries_supplied: 2,
        entries_evaluated: 2,
        granted_by: [teamAce, aliceAce],
      },
    },
    graph: {
      nodes: [
        node(ALICE_NODE, "principal", { key: ALICE_SID, sid: ALICE_SID, display_name: "Alice Smith" }),
        node(TEAM_NODE, "group", { key: TEAM_SID, sid: TEAM_SID, display_name: "Finance-Team" }),
        node(NTFS_ACE_TEAM, "ntfs_ace", { key: "ntfs|team", sid: TEAM_SID, layer: "ntfs", position: 0 }),
        node(NTFS_ACE_ALICE, "ntfs_ace", {
          key: "ntfs|alice",
          sid: ALICE_SID,
          layer: "ntfs",
          position: 1,
        }),
        node(RESOURCE_NODE, "resource", { key: RESOURCE_KEY, display_name: RESOURCE_PATH }),
      ],
      edges: [
        edge("m1", "membership", ALICE_NODE, TEAM_NODE, {
          membership_edge_key: `${TEAM_SID}->${ALICE_SID}|directory_group_member`,
        }),
        edge("t1", "trustee", TEAM_NODE, NTFS_ACE_TEAM, { ace_key: "ntfs|team" }),
        edge("t2", "trustee", ALICE_NODE, NTFS_ACE_ALICE, { ace_key: "ntfs|alice" }),
        edge("g1", "grant", NTFS_ACE_TEAM, RESOURCE_NODE, { ace_key: "ntfs|team" }),
        edge("g2", "grant", NTFS_ACE_ALICE, RESOURCE_NODE, { ace_key: "ntfs|alice" }),
      ],
    },
    paths: [
      path({
        id: "p0000",
        ace_key: "ntfs|team",
        ace_position: 0,
        inherited: true,
        chain: [ALICE, TEAM],
        nodes: [ALICE_NODE, TEAM_NODE, NTFS_ACE_TEAM, RESOURCE_NODE],
        edges: ["m1", "t1", "g1"],
      }),
      path({
        id: "p0001",
        ace_key: "ntfs|alice",
        ace_position: 1,
        via_group: false,
        chain: [ALICE],
        nodes: [ALICE_NODE, NTFS_ACE_ALICE, RESOURCE_NODE],
        edges: ["t2", "g2"],
      }),
    ],
    removal_targets: [
      removal({
        edge_id: "m1",
        changes_nothing: true,
        revokes_all_access: false,
        rights_removed: NO_RIGHTS,
        rights_after: rights(),
        paths_removed: ["p0000"],
        alternate_paths: ["p0001"],
      }),
      removal({
        edge_id: "t2",
        kind: "trustee",
        source: ALICE_NODE,
        target: NTFS_ACE_ALICE,
        changes_nothing: true,
        revokes_all_access: false,
        rights_removed: NO_RIGHTS,
        rights_after: rights(),
        paths_removed: ["p0001"],
        alternate_paths: ["p0000"],
      }),
    ],
  };
}

/** A Deny that bit. Removing it would grant access, not take it away. */
export function denied(): AccessExplanationResponse {
  const denyAce = applied({
    ace_key: "ntfs|deny-team",
    position: 0,
    ace_type: "deny",
    matched_key: TEAM_SID,
    trustee: TEAM,
    contributed: "0x001301BF",
  });
  const base = explanation();
  return {
    ...base,
    verdict: verdict({
      outcome: "denied",
      reason: "A Deny entry withheld every right an Allow would have given.",
      denials: [denyAce],
    }),
    effective: {
      ...base.effective,
      access: false,
      rights: NO_RIGHTS,
      limiting_layer: "ntfs",
      ntfs: {
        ...base.effective.ntfs,
        rights: NO_RIGHTS,
        granted_by: [],
        denied_by: [denyAce],
      },
    },
    graph: {
      nodes: [
        node(ALICE_NODE, "principal", { key: ALICE_SID, sid: ALICE_SID, display_name: "Alice Smith" }),
        node(TEAM_NODE, "group", { key: TEAM_SID, sid: TEAM_SID, display_name: "Finance-Team" }),
        node(NTFS_ACE_TEAM, "ntfs_ace", {
          key: "ntfs|deny-team",
          sid: TEAM_SID,
          layer: "ntfs",
          position: 0,
        }),
        node(RESOURCE_NODE, "resource", { key: RESOURCE_KEY, display_name: RESOURCE_PATH }),
      ],
      edges: [
        edge("m1", "membership", ALICE_NODE, TEAM_NODE),
        edge("t1", "trustee", TEAM_NODE, NTFS_ACE_TEAM, { ace_key: "ntfs|deny-team" }),
        edge("d1", "deny", NTFS_ACE_TEAM, RESOURCE_NODE, { ace_key: "ntfs|deny-team" }),
      ],
    },
    paths: [
      path({
        id: "p0000",
        relation: "deny",
        ace_key: "ntfs|deny-team",
        chain: [ALICE, TEAM],
        nodes: [ALICE_NODE, TEAM_NODE, NTFS_ACE_TEAM, RESOURCE_NODE],
        edges: ["m1", "t1", "d1"],
        effective_rights: rights(),
      }),
    ],
    removal_targets: [
      removal({
        edge_id: "m1",
        changes_nothing: false,
        revokes_all_access: false,
        rights_removed: NO_RIGHTS,
        rights_added: rights(),
        rights_after: rights(),
        paths_removed: ["p0000"],
      }),
    ],
  };
}

/** Nothing names this principal. The absence of a control, not a control. */
export function noGrant(): AccessExplanationResponse {
  const base = explanation();
  return {
    ...base,
    verdict: verdict({
      outcome: "no_grant",
      reason: "No entry on either ACL names this principal or any group it belongs to.",
    }),
    effective: {
      ...base.effective,
      access: false,
      rights: NO_RIGHTS,
      limiting_layer: "none",
      ntfs: { ...base.effective.ntfs, rights: NO_RIGHTS, granted_by: [] },
    },
    graph: {
      nodes: [
        node(ALICE_NODE, "principal", { key: ALICE_SID, sid: ALICE_SID, display_name: "Alice Smith" }),
        node(RESOURCE_NODE, "resource", { key: RESOURCE_KEY, display_name: RESOURCE_PATH }),
      ],
      edges: [],
    },
    paths: [],
    removal_targets: [],
  };
}

/** Nothing established, and what was not collected could be hiding a grant. */
export function indeterminate(): AccessExplanationResponse {
  const base = explanation();
  return {
    ...base,
    verdict: verdict({
      outcome: "indeterminate",
      certainty: "at_least",
      conclusive: false,
      may_understate: true,
      reason: "No access was established, and collection is incomplete in a direction that could hide a grant.",
    }),
    resource: resource({ observed: false, dacl_present: null }),
    effective: {
      ...base.effective,
      access: false,
      rights: NO_RIGHTS,
      certainty: "at_least",
      limiting_layer: "unknown",
      acl_provenance: "unobserved",
      ntfs: { ...base.effective.ntfs, rights: NO_RIGHTS, granted_by: [], entries_supplied: 0, entries_evaluated: 0 },
    },
    token: token({ membership_complete: false }),
    graph: {
      nodes: [
        node(ALICE_NODE, "principal", { key: ALICE_SID, sid: ALICE_SID, display_name: "Alice Smith" }),
        node(RESOURCE_NODE, "resource", { key: RESOURCE_KEY, display_name: RESOURCE_PATH }),
      ],
      edges: [],
    },
    paths: [],
    removal_targets: [],
    complete: false,
    truncation: ["max_depth", "max_paths"],
    warnings: [
      finding({
        condition: "unread_descriptor",
        message: "No run has read the security descriptor of this directory.",
        may_understate: true,
      }),
    ],
  };
}
