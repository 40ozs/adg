/**
 * The derived-answer contract, version 1.0.
 *
 * `lib/contracts.ts` describes what the API returns when it *lists* what a collector
 * observed. This file describes the other kind of response: an answer the backend
 * **derived** — an access check, the routes that produced it, and the measured effect of
 * removing each one.
 *
 * It is a separate module for the same reason the backend publishes its schemas under
 * `v1/derived/` rather than beside the collector payloads: the two directions version
 * independently, and a derived body carries a `schema_version` that an observation
 * listing does not. `tests/explanation-contract.test.ts` checks every type here against
 * `docs/contracts/v1/derived/access-explanation.schema.json`, which is generated from the
 * models the API actually serializes.
 *
 * The primitives these responses share with the listing endpoints — `RightsView`,
 * `PrincipalSummary`, `TokenView`, `FindingView`, `ResourceRef`, `ShareRef` — are imported
 * rather than restated. A second declaration of `RightsView` would be a second thing to
 * keep current, and the first one to fall behind would be invisible.
 *
 * **Nothing in this module or anything that consumes it computes access.** Every
 * conclusion below — whether a right survives, whether a Deny bit, whether a removal
 * changes anything — arrives already computed. See `docs/contracts/derived-responses.md`,
 * "The one rule".
 */

import type {
  AccessCertainty,
  FindingView,
  LimitingLayer,
  PrincipalSummary,
  ResourceRef,
  RightsView,
  ShareRef,
  TokenView,
} from "@/lib/contracts";

/**
 * The four answers, which is three more than a boolean.
 *
 * `effective.access === false` means `denied`, `no_grant` or `indeterminate`, and the
 * three lead to three different actions: leave the control alone, there is no control, and
 * go and collect more. Branch on this, never on the boolean.
 */
export type AccessOutcome = "granted" | "denied" | "no_grant" | "indeterminate";

/** What the entry at the end of a route says. Separate from what it is worth. */
export type PathRelation = "grant" | "deny";

/**
 * What a route is actually worth, which is not the same as what its entry says.
 *
 * `redundant` — an earlier entry had already settled every right this one names.
 * `constrained` — the other layer withholds all of it.
 *
 * Kept separate from `relation` because "a Deny that does not bite" is an ordinary state
 * of a real ACL and a single five-valued enum could not represent it.
 */
export type PathEffect = "contributes" | "redundant" | "constrained";

export type NodeKind =
  | "principal"
  | "group"
  | "assumed_trustee"
  | "ntfs_ace"
  | "smb_ace"
  | "resource"
  | "share";

export type EdgeKind =
  | "membership"
  | "assumed_membership"
  | "trustee"
  | "grant"
  | "deny"
  | "ownership";

/** Whether the ACL this answer rests on was read, reconstructed, or never seen. */
export type AclProvenance = "observed" | "derived" | "unobserved";

/** One ACE, as it applied to this subject. */
export interface AppliedAceView {
  layer: string;
  position: number;
  ace_type: string;
  /** Null for an ownership path: the rights come from owning the object, not from an entry. */
  ace_key: string | null;
  access_mask: string;
  contributed: string;
  flags: number;
  source: string;
  inherited_from: string | null;
  matched_key: string;
  trustee: PrincipalSummary;
  via_group: boolean;
}

/** One ACL, evaluated in order against the subject's token. */
export interface AclEvaluationView {
  layer: string;
  rights: RightsView;
  canonical_rights: RightsView | null;
  entries_supplied: number;
  entries_evaluated: number;
  order_dependent: boolean;
  owner_rights: RightsView | null;
  granted_by: AppliedAceView[];
  denied_by: AppliedAceView[];
  /** Entries that matched and took nothing away, because something earlier had settled it. */
  superseded: AppliedAceView[];
}

/** The access check itself: both layers, and the narrower of the two. */
export interface EffectiveAccessView {
  resource_key: string;
  share_key: string | null;
  access_path: string;
  access: boolean;
  rights: RightsView;
  certainty: AccessCertainty;
  limiting_layer: LimitingLayer;
  acl_provenance: AclProvenance;
  conditions: string[];
  findings: FindingView[];
  ntfs: AclEvaluationView;
  share: AclEvaluationView | null;
}

/**
 * The verdict a client branches on.
 *
 * `conclusive: false` forbids the negative conclusion outright: while it is false, "this
 * principal has no access" is not a statement this response supports, and a screen that
 * words it that way is reporting a false negative about a part of the estate nobody has
 * scanned.
 */
export interface VerdictView {
  outcome: AccessOutcome;
  certainty: AccessCertainty;
  conclusive: boolean;
  may_overstate: boolean;
  may_understate: boolean;
  reason: string;
  /** Deny entries that actually withheld rights. Both layers, in evaluation order. */
  denials: AppliedAceView[];
}

/** A node in the causal graph. Keyed by `id`; `key` is the storage key behind it. */
export interface ExplanationNodeView {
  id: string;
  kind: NodeKind;
  key: string;
  sid: string | null;
  display_name: string | null;
  /** ACE nodes only. */
  layer: string | null;
  /** ACE nodes only: index in the DACL as stored. */
  position: number | null;
}

export interface ExplanationEdgeView {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  /**
   * Whether an administrator could actually delete this relationship. False for an assumed
   * membership: there is no Everyone group to edit.
   */
  removable: boolean;
  membership_edge_key: string | null;
  ace_key: string | null;
}

export interface ExplanationGraphView {
  nodes: ExplanationNodeView[];
  edges: ExplanationEdgeView[];
}

/** One route from the subject to an entry that matched, and what it is worth. */
export interface CausalPathView {
  id: string;
  layer: string;
  relation: PathRelation;
  effect: PathEffect;
  /** The entry's own mask: what it is worth alone. */
  ace_rights: RightsView;
  /** What it settled at its own layer that nothing earlier had already settled. */
  layer_rights: RightsView;
  /** The part of `layer_rights` the other ACL withholds. */
  constrained_rights: RightsView;
  /** What it is worth to the final answer, after the layer crossing. */
  effective_rights: RightsView;
  ace_key: string | null;
  ace_position: number;
  inherited: boolean;
  via_group: boolean;
  /** True when the entry was reached through an assumed token SID, not an observed membership. */
  assumed: boolean;
  /** The membership chain, subject first and ACL trustee last. */
  chain: PrincipalSummary[];
  nodes: string[];
  edges: string[];
}

/** One removable edge, with the measured effect of deleting it. */
export interface RemovalTargetView {
  edge_id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  rights_after: RightsView;
  rights_removed: RightsView;
  /** Non-empty means the edge carried a Deny and removing it would *widen* access. */
  rights_added: RightsView;
  changes_nothing: boolean;
  /** False while any alternate path survives. */
  revokes_all_access: boolean;
  paths_removed: string[];
  /** Contributing paths that still deliver rights afterwards. */
  alternate_paths: string[];
}

export interface ExplanationLimitsView {
  max_depth: number;
  max_paths: number;
  max_paths_per_trustee: number;
  max_removal_targets: number;
}

/**
 * Which collected state an answer came from.
 *
 * Equal tokens mean identical stored facts, so two answers carrying one token differ only
 * in the question. `is_empty` means no collector has ever run, and every derived answer
 * over an empty estate reports no access — where it means *nobody looked*.
 */
export interface BasisView {
  token: string;
  runs: number;
  latest_run_id: string | null;
  latest_activity_at: string | null;
  observations_applied: number;
  batches_received: number;
  is_empty: boolean;
}

/** The whole derivation for one principal against one directory. */
export interface AccessExplanationResponse {
  schema_version: string;
  basis: BasisView;
  subject: PrincipalSummary;
  resource: ResourceRef;
  share: ShareRef | null;
  access_path: string;
  verdict: VerdictView;
  token: TokenView;
  effective: EffectiveAccessView;
  graph: ExplanationGraphView;
  paths: CausalPathView[];
  removal_targets: RemovalTargetView[];
  /** Membership cycles in the traversed subgraph. A cycle is a finding. */
  cycles: string[][];
  limits: ExplanationLimitsView;
  /**
   * False means the routes shown are a subset: at least one more exists, so nothing may be
   * concluded from the absence of one and no removal may be believed sufficient.
   */
  complete: boolean;
  truncation: string[];
  warnings: FindingView[];
}

/** A page of the same causal paths, in the same deterministic order. */
export interface AccessPathPageResponse {
  schema_version: string;
  basis: BasisView;
  subject: PrincipalSummary;
  resource: ResourceRef;
  share: ShareRef | null;
  access_path: string;
  verdict: VerdictView;
  items: CausalPathView[];
  page: { limit: number; has_more: boolean; next_cursor: string | null; total: number | null };
  /** Read this *and* `page.has_more`. The last page of a truncated enumeration has
   * `has_more: false` and `complete: false` together. */
  complete: boolean;
  truncation: string[];
  limits: ExplanationLimitsView;
}
