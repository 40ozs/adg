/**
 * Contract 1.1 bodies for the guided investigation (ADG-DEMO-02), built by hand.
 *
 * `apollo()` is the canonical demo's response in miniature, shaped field for field like the
 * one `GET /api/v1/access/explain` returns for Fatima Gordon against
 * `\\GP-ENG-FS01\Engineering\Projects\Apollo` after a Genesee Press reset (pinned against
 * the real engine in `backend/tests/db/test_genesee_press_demo.py::TestTheAccessInvestigation`):
 *
 * - NTFS: a direct, explicit Modify entry carries the access; an inherited Modify entry
 *   reached through five nested groups is redundant behind it.
 * - Share: ENG-Mobile's Change entry carries it; Fatima's own share entry is redundant.
 * - Removing either carrying route changes nothing.
 * - Two things are not confirmed: an orphaned SID of a former employee (stored as
 *   `unresolved`, last known as GPCORP\lhansen) and Proj-Zephyr-RW, whose members
 *   were never collected. Both can only widen the rights.
 *
 * Every SID and name is synthetic (Genesee Press). The variants change one thing each.
 */

import type { FindingView, PrincipalSummary } from "@/lib/contracts";
import type {
  AccessExplanationResponse,
  CausalPathView,
  HopEvidenceView,
  InvestigationView,
  OpenQuestionView,
} from "@/lib/derived";
import {
  applied,
  edge,
  explanation,
  finding,
  node,
  NO_RIGHTS,
  path,
  principal,
  removal,
  rights,
  share,
  systemMappingFinding,
  systemOverShare,
  verdict,
} from "./explanation-factories";

const DOMAIN = "S-1-5-21-3949361974-1583827165-633756269";
export const FATIMA_SID = `${DOMAIN}-1323`;
export const ORPHAN_SID = `${DOMAIN}-1108`;
export const ZEPHYR_SID = `${DOMAIN}-1251`;
export const APOLLO_PATH = "\\\\GP-ENG-FS01\\Engineering\\Projects\\Apollo";
const APOLLO_KEY = APOLLO_PATH.toLowerCase();

function group(rid: number, name: string): PrincipalSummary {
  return principal({
    key: `${DOMAIN}-${rid}`,
    sid: `${DOMAIN}-${rid}`,
    kind: "domain_group",
    is_group: true,
    display_name: name,
    enabled: null,
    group_scope: "global",
    group_type: "security",
  });
}

export const FATIMA = principal({
  key: FATIMA_SID,
  sid: FATIMA_SID,
  display_name: "Fatima Gordon",
  sam_account_name: "fgordon",
});
export const ENG_MOBILE = group(1443, "ENG-Mobile");
const FIVE = [
  ENG_MOBILE,
  group(1150, "Engineering-Frontend"),
  group(1112, "Engineering"),
  group(1176, "Engineering-FS"),
  group(1135, "Share_Engineering_Modify"),
];
export const ORPHAN = principal({
  key: ORPHAN_SID,
  sid: ORPHAN_SID,
  kind: "unresolved",
  is_group: false,
  display_name: null,
  enabled: null,
  unresolved_reason: "deleted",
  last_known_name: "GPCORP\\lhansen",
});
export const ZEPHYR = group(1251, "Proj-Zephyr-RW");

const SUBJECT = `principal:${FATIMA_SID}`;
const RESOURCE = `resource:${APOLLO_KEY}`;
const NTFS_DIRECT = `ntfs-ace:${APOLLO_KEY}:0001`;
const NTFS_INHERITED = `ntfs-ace:${APOLLO_KEY}:0005`;
const SMB_GROUP = "smb-ace:gp-eng-fs01|engineering:0003";
const SMB_DIRECT = "smb-ace:gp-eng-fs01|engineering:0017";
const groupNode = (member: PrincipalSummary): string => `group:${member.sid}`;

const G1 = "2026-09-03T03:12:35Z";
const G3 = "2026-10-01T03:12:35Z";

function observed(
  edgeId: string,
  record: NonNullable<HopEvidenceView["record"]>,
  collector: string,
): HopEvidenceView {
  return {
    edge_id: edgeId,
    basis: "observed",
    record,
    record_key: `${record}|${edgeId}`,
    source_key: `${record}|${edgeId}`,
    first_observed_at: G1,
    first_observed_run_id: "d2df0d45-5261-5806-8a31-c48f84d9a808",
    last_observed_at: G3,
    last_observed_run_id: "96daec88-f1a2-5aa7-b42f-f1c6ba225a03",
    collector,
    collection_method:
      collector === "ntfs"
        ? "System.IO.DirectoryInfo.GetAccessControl"
        : collector === "smb"
          ? "Get-SmbShare"
          : "LDAP",
    last_run_status: "succeeded",
  };
}

const UNOBSERVED_MESSAGE =
  "An entry names a group whose membership ADG has never collected, or a SID nothing has identified, so nobody can be ruled out of it.";

function unobserved(trustee: string): FindingView {
  return finding({
    condition: "trustee_membership_unobserved",
    message: UNOBSERVED_MESSAGE,
    may_overstate: false,
    may_understate: true,
    detail: { trustee_key: trustee },
  });
}

const ASSUMED = finding({
  detail: { assumption: "authenticated_user", access_path: "remote_smb", sids: ["S-1-1-0", "S-1-5-11", "S-1-5-2"] },
});

function question(
  item: FindingView,
  overrides: Partial<OpenQuestionView> = {},
): OpenQuestionView {
  return {
    condition: item.condition,
    message: item.message,
    state: "missing",
    impact: "extent",
    may_overstate: item.may_overstate,
    may_understate: item.may_understate,
    about: null,
    detail: item.detail ?? {},
    ...overrides,
  };
}

export function apolloInvestigation(): InvestigationView {
  return {
    presence: "present",
    primary_routes: [
      {
        layer: "ntfs",
        path_id: "p0000",
        route: "direct",
        placement: "explicit",
        membership_hops: 0,
        coverage: rights(),
        removal_edge_id: `trustee:${SUBJECT}->${NTFS_DIRECT}`,
        removal_effect: "changes_nothing",
        rights_after_removal: rights(),
        alternate_path_ids: [],
      },
      {
        layer: "smb_share",
        path_id: "p0002",
        route: "group",
        placement: "explicit",
        membership_hops: 1,
        coverage: rights(),
        removal_edge_id: "m1",
        removal_effect: "changes_nothing",
        rights_after_removal: rights(),
        alternate_path_ids: [],
      },
    ],
    open_questions: [
      question(ASSUMED, { state: "assumed", impact: "none" }),
      question(unobserved(ORPHAN_SID), { about: ORPHAN }),
      question(unobserved(ZEPHYR_SID), { about: ZEPHYR }),
    ],
    impact: "extent",
    affects_conclusion: false,
    next_steps: [
      { code: "resolve_trustee_identity", about: ORPHAN, path_id: null, condition: "trustee_membership_unobserved" },
      { code: "collect_group_membership", about: ZEPHYR, path_id: null, condition: "trustee_membership_unobserved" },
      { code: "review_redundant_entry", about: null, path_id: "p0000", condition: null },
    ],
    routes_complete: true,
  };
}

/** The canonical demo response, in miniature. */
export function apollo(): AccessExplanationResponse {
  const base = explanation();
  const membershipEdges = FIVE.map((member, index) =>
    edge(`m${index + 1}`, "membership", index === 0 ? SUBJECT : groupNode(FIVE[index - 1]), groupNode(member), {
      membership_edge_key: `${member.sid}->${index === 0 ? FATIMA_SID : FIVE[index - 1].sid}|directory_group_member`,
    }),
  );
  const graphEdges = [
    edge(`trustee:${SUBJECT}->${NTFS_DIRECT}`, "trustee", SUBJECT, NTFS_DIRECT, { ace_key: "ntfs|direct" }),
    edge(`grant:${NTFS_DIRECT}`, "grant", NTFS_DIRECT, RESOURCE, { ace_key: "ntfs|direct" }),
    ...membershipEdges,
    edge(`trustee:${groupNode(FIVE[4])}->${NTFS_INHERITED}`, "trustee", groupNode(FIVE[4]), NTFS_INHERITED, {
      ace_key: "ntfs|inherited",
    }),
    edge(`grant:${NTFS_INHERITED}`, "grant", NTFS_INHERITED, RESOURCE, { ace_key: "ntfs|inherited" }),
    edge(`trustee:${groupNode(ENG_MOBILE)}->${SMB_GROUP}`, "trustee", groupNode(ENG_MOBILE), SMB_GROUP, {
      ace_key: "smb|group",
    }),
    edge(`grant:${SMB_GROUP}`, "grant", SMB_GROUP, RESOURCE, { ace_key: "smb|group" }),
    edge(`trustee:${SUBJECT}->${SMB_DIRECT}`, "trustee", SUBJECT, SMB_DIRECT, { ace_key: "smb|direct" }),
    edge(`grant:${SMB_DIRECT}`, "grant", SMB_DIRECT, RESOURCE, { ace_key: "smb|direct" }),
  ];
  const evidence = graphEdges.map((item) =>
    item.kind === "membership"
      ? observed(item.id, "membership_edge", "active_directory")
      : item.ace_key?.startsWith("ntfs")
        ? observed(item.id, "ntfs_ace", "ntfs")
        : observed(item.id, "smb_share_ace", "smb"),
  );
  const paths: CausalPathView[] = [
    path({
      id: "p0000",
      ace_key: "ntfs|direct",
      ace_position: 1,
      via_group: false,
      chain: [FATIMA],
      nodes: [SUBJECT, NTFS_DIRECT, RESOURCE],
      edges: [`trustee:${SUBJECT}->${NTFS_DIRECT}`, `grant:${NTFS_DIRECT}`],
    }),
    path({
      id: "p0001",
      effect: "redundant",
      ace_key: "ntfs|inherited",
      ace_position: 5,
      inherited: true,
      layer_rights: NO_RIGHTS,
      effective_rights: NO_RIGHTS,
      chain: [FATIMA, ...FIVE],
      nodes: [SUBJECT, ...FIVE.map(groupNode), NTFS_INHERITED, RESOURCE],
      edges: [
        ...membershipEdges.map((item) => item.id),
        `trustee:${groupNode(FIVE[4])}->${NTFS_INHERITED}`,
        `grant:${NTFS_INHERITED}`,
      ],
    }),
    path({
      id: "p0002",
      layer: "smb_share",
      ace_key: "smb|group",
      ace_position: 3,
      chain: [FATIMA, ENG_MOBILE],
      nodes: [SUBJECT, groupNode(ENG_MOBILE), SMB_GROUP, RESOURCE],
      edges: ["m1", `trustee:${groupNode(ENG_MOBILE)}->${SMB_GROUP}`, `grant:${SMB_GROUP}`],
    }),
    path({
      id: "p0003",
      layer: "smb_share",
      effect: "redundant",
      ace_key: "smb|direct",
      ace_position: 17,
      via_group: false,
      layer_rights: NO_RIGHTS,
      effective_rights: NO_RIGHTS,
      chain: [FATIMA],
      nodes: [SUBJECT, SMB_DIRECT, RESOURCE],
      edges: [`trustee:${SUBJECT}->${SMB_DIRECT}`, `grant:${SMB_DIRECT}`],
    }),
  ];
  return {
    ...base,
    schema_version: "1.2",
    subject: FATIMA,
    resource: { ...base.resource, key: APOLLO_KEY, path: APOLLO_PATH, share_key: "gp-eng-fs01|engineering" },
    share: share({ key: "gp-eng-fs01|engineering", name: "Engineering", server_key: "gp-eng-fs01" }),
    verdict: verdict({ certainty: "at_least", may_understate: true }),
    effective: {
      ...base.effective,
      resource_key: APOLLO_KEY,
      certainty: "at_least",
      limiting_layer: "none",
      ntfs: {
        ...base.effective.ntfs,
        granted_by: [
          applied({ ace_key: "ntfs|direct", position: 1, matched_key: FATIMA_SID, trustee: FATIMA, via_group: false }),
        ],
        superseded: [
          applied({
            ace_key: "ntfs|inherited",
            position: 5,
            matched_key: FIVE[4].sid,
            trustee: FIVE[4],
            source: "inherited",
            inherited_from: "\\\\GP-ENG-FS01\\Engineering",
            contributed: "0x00000000",
          }),
        ],
      },
      share: {
        ...(base.effective.share ?? base.effective.ntfs),
        layer: "smb_share",
        rights: rights({ layer: "smb_share" }),
        granted_by: [
          applied({ layer: "smb_share", ace_key: "smb|group", position: 3, matched_key: ENG_MOBILE.sid, trustee: ENG_MOBILE }),
        ],
        superseded: [
          applied({
            layer: "smb_share",
            ace_key: "smb|direct",
            position: 17,
            matched_key: FATIMA_SID,
            trustee: FATIMA,
            via_group: false,
            contributed: "0x00000000",
          }),
        ],
      },
      findings: [ASSUMED, unobserved(ORPHAN_SID), unobserved(ZEPHYR_SID)],
    },
    graph: {
      nodes: [
        node(SUBJECT, "principal", { key: FATIMA_SID, sid: FATIMA_SID, display_name: "Fatima Gordon" }),
        ...FIVE.map((member) => node(groupNode(member), "group", { key: member.sid, sid: member.sid, display_name: member.display_name })),
        node(NTFS_DIRECT, "ntfs_ace", { key: "ntfs|direct", sid: FATIMA_SID, layer: "ntfs", position: 1 }),
        node(NTFS_INHERITED, "ntfs_ace", { key: "ntfs|inherited", sid: FIVE[4].sid, layer: "ntfs", position: 5 }),
        node(SMB_GROUP, "smb_ace", { key: "smb|group", sid: ENG_MOBILE.sid, layer: "smb_share", position: 3 }),
        node(SMB_DIRECT, "smb_ace", { key: "smb|direct", sid: FATIMA_SID, layer: "smb_share", position: 17 }),
        node(RESOURCE, "resource", { key: APOLLO_KEY }),
      ],
      edges: graphEdges,
    },
    paths,
    removal_targets: [
      removal({
        edge_id: `trustee:${SUBJECT}->${NTFS_DIRECT}`,
        kind: "trustee",
        source: SUBJECT,
        target: NTFS_DIRECT,
        changes_nothing: true,
        revokes_all_access: false,
        rights_removed: NO_RIGHTS,
        rights_after: rights(),
        paths_removed: ["p0000"],
        alternate_paths: ["p0002"],
      }),
      removal({
        edge_id: "m1",
        changes_nothing: true,
        revokes_all_access: false,
        rights_removed: NO_RIGHTS,
        rights_after: rights(),
        paths_removed: ["p0001", "p0002"],
        alternate_paths: ["p0000"],
      }),
    ],
    warnings: [ASSUMED, unobserved(ORPHAN_SID), unobserved(ZEPHYR_SID)],
    evidence,
    investigation: apolloInvestigation(),
  };
}

/** The same answer with the share ACL unread: an open question now reaches the conclusion. */
export function apolloWithUnreadShare(): AccessExplanationResponse {
  const body = apollo();
  const unread = finding({
    condition: "share_acl_not_observed",
    message: "No share ACL has been collected for this share. It could narrow these rights.",
    may_overstate: true,
    may_understate: false,
    detail: {},
  });
  const investigation = apolloInvestigation();
  return {
    ...body,
    verdict: verdict({ certainty: "uncertain", may_overstate: true, may_understate: true }),
    warnings: [...body.warnings, unread],
    investigation: {
      ...investigation,
      open_questions: [
        ...investigation.open_questions,
        question(unread, { state: "missing", impact: "presence" }),
      ],
      impact: "presence",
      affects_conclusion: true,
      next_steps: [
        { code: "rescan_resource", about: null, path_id: null, condition: "share_acl_not_observed" },
        ...investigation.next_steps,
      ],
    },
  };
}

/** A contract 1.0 body: no provenance, no investigation. Older servers send this. */
export function apolloFromAnOlderServer(): AccessExplanationResponse {
  const body: AccessExplanationResponse = { ...apollo(), schema_version: "1.0" };
  delete body.evidence;
  delete body.investigation;
  return body;
}

/** ADG-DEMO-01's SYSTEM over a share, with the summary the engine attaches. */
export function systemOverShareInvestigated(): AccessExplanationResponse {
  const body = systemOverShare();
  const mapping = systemMappingFinding("ambiguous");
  const system = body.subject;
  return {
    ...body,
    schema_version: "1.2",
    evidence: [],
    investigation: {
      presence: "undetermined",
      primary_routes: [],
      open_questions: [question(mapping, { state: "ambiguous", impact: "presence", about: system })],
      impact: "presence",
      affects_conclusion: true,
      next_steps: [
        { code: "settle_system_mapping", about: system, path_id: null, condition: mapping.condition },
      ],
      routes_complete: true,
    },
  };
}

/** A conflicting SYSTEM description, to show the fourth state is worded, not colored. */
export function systemConflictingInvestigated(): AccessExplanationResponse {
  const body = systemOverShareInvestigated();
  const mapping = systemMappingFinding("conflicting", "user");
  const investigation = body.investigation as InvestigationView;
  return {
    ...body,
    access_path: "local",
    warnings: [mapping],
    investigation: {
      ...investigation,
      open_questions: [question(mapping, { state: "conflicting", impact: "presence", about: body.subject })],
      next_steps: [{ code: "settle_system_mapping", about: body.subject, path_id: null, condition: mapping.condition }],
    },
  };
}
