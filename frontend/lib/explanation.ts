/**
 * Turning a derived access answer into something an administrator can read.
 *
 * Every function here is pure and every one of them is *rendering*. Nothing intersects a
 * mask, compares an ACE position, decides whether a Deny applies, or works out whether a
 * removal would help: all of that arrives computed, measured by the backend engine against
 * the same Windows semantics it is checked against. See
 * `docs/contracts/derived-responses.md`, "The one rule".
 *
 * What this module *does* decide is wording and shape, and two of those decisions carry
 * weight:
 *
 * 1. **`no_grant`, `denied` and `indeterminate` are worded as three different answers.**
 *    They are three different situations with three different remediations, and an
 *    auditor who reads "No" for all three stops looking in the one case where they should
 *    keep going.
 * 2. **A route that changes nothing when removed says so, loudly.** The whole failure mode
 *    this screen exists to prevent is somebody removing a group membership, watching the
 *    row disappear from one view, and believing access is gone while three other routes
 *    still deliver it.
 *
 * The graph layout is here rather than in the component for the same reason the rest is:
 * it is a deterministic function of the API's nodes and edges, so it can be asserted to
 * draw exactly the facts the table lists, and neither view can quietly drop one.
 */

import type { AccessCertainty, FindingView, PrincipalSummary } from "@/lib/contracts";
import type {
  AccessExplanationResponse,
  AppliedAceView,
  CausalPathView,
  EdgeKind,
  ExplanationEdgeView,
  ExplanationGraphView,
  ExplanationNodeView,
  NodeKind,
  PathEffect,
  RemovalTargetView,
  VerdictView,
} from "@/lib/derived";

export type Tone = "ok" | "warn" | "bad" | "info";

/** Where the explanation screen lives. One constant, so no page hand-writes the URL. */
export const EXPLAIN_PATH = "/access/explain";

/**
 * A link to the explanation for one pair.
 *
 * Query parameters rather than path segments, matching the API's own spelling and for the
 * same reason: a directory is named by its UNC path, and `%5C%5CFS01%5CFinance` in a URL
 * path segment is normalized or rejected by several proxies.
 */
export function explainHref(
  principal: string,
  resource: string,
  extra: Record<string, string | undefined> = {},
): string {
  const params = new URLSearchParams({ principal, resource });
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== "") {
      params.set(key, value);
    }
  }
  return `${EXPLAIN_PATH}?${params.toString()}`;
}

/* --------------------------------------------------------------------- the verdict */

export interface OutcomeWording {
  /** The word at the top of the page. */
  word: string;
  tone: Tone;
  /** What the engine found, in one sentence. */
  headline: string;
  /** What it does and does not license the reader to do. Never omitted. */
  guidance: string;
  /** False forbids the negative conclusion entirely. */
  conclusive: boolean;
}

/**
 * The four outcomes, worded as four answers.
 *
 * `tone: "bad"` for `granted` is not a judgement that access is wrong — it is that in an
 * auditing tool the finding is the access, and the eye should go to it. `denied` and
 * `no_grant` are `ok` because neither is a finding about this pair; `indeterminate` is a
 * warning because it is a finding about the *collection*.
 */
export function outcomeWording(verdict: VerdictView): OutcomeWording {
  switch (verdict.outcome) {
    case "granted":
      return {
        word: "Access",
        tone: "bad",
        headline: verdict.reason,
        guidance:
          "At least one right survives both the share ACL and the NTFS ACL. The routes " +
          "below are how, and each one carries what removing it would actually do.",
        conclusive: verdict.conclusive,
      };
    case "denied":
      return {
        word: "Denied",
        tone: "ok",
        headline: verdict.reason,
        guidance:
          "A Deny entry withheld rights an Allow would otherwise have given. Somebody put " +
          "that control here and may be relying on it — removing it grants access.",
        conclusive: verdict.conclusive,
      };
    case "no_grant":
      return {
        word: "No access",
        tone: "ok",
        headline: verdict.reason,
        guidance:
          "Nothing on either ACL reaches this principal. This is the absence of a control " +
          "rather than a control: there is no entry here to remove.",
        conclusive: verdict.conclusive,
      };
    case "indeterminate":
      return {
        word: "Unknown",
        tone: "warn",
        headline: verdict.reason,
        guidance:
          "No right was established, and what has not been collected could be hiding one. " +
          "This is not a finding of no access, and must not be counted as one.",
        conclusive: verdict.conclusive,
      };
  }
}

/**
 * The direction the evidence could be wrong.
 *
 * Orthogonal to the outcome and never folded into it: "granted, and an unread share ACL
 * could narrow it" is a real and common state, and it is still a finding.
 */
export function certaintyNote(certainty: AccessCertainty): string {
  switch (certainty) {
    case "certain":
      return "Every input this answer depends on was collected.";
    case "at_most":
      return "An upper bound: something unread could only narrow this.";
    case "at_least":
      return "A lower bound: something unread could only widen this. Keep looking.";
    case "uncertain":
      return "Gaps in both directions — the real rights could be wider or narrower.";
  }
}

/* ----------------------------------------------------------------------- the routes */

export function relationLabel(relation: CausalPathView["relation"]): string {
  return relation === "grant" ? "Allow" : "Deny";
}

/** What a route is worth, which is not what its entry says. */
export function effectWording(effect: PathEffect): { label: string; tone: Tone; detail: string } {
  switch (effect) {
    case "contributes":
      return {
        label: "Contributes",
        tone: "bad",
        detail: "This route changed the final answer.",
      };
    case "redundant":
      return {
        label: "Redundant",
        tone: "info",
        detail:
          "An earlier entry had already settled every right this one names, so removing " +
          "it alone changes nothing.",
      };
    case "constrained":
      return {
        label: "Constrained",
        tone: "info",
        detail:
          "The other layer withholds all of it. This entry grants on its own ACL and " +
          "delivers nothing.",
      };
  }
}

/** The routes that actually changed the answer. The ones a remediation has to address. */
export function contributingPaths(
  explanation: AccessExplanationResponse,
): readonly CausalPathView[] {
  return explanation.paths.filter((path) => path.effect === "contributes");
}

/** Routes ending in a Deny, whatever that Deny turned out to be worth. */
export function denyPaths(explanation: AccessExplanationResponse): readonly CausalPathView[] {
  return explanation.paths.filter((path) => path.relation === "deny");
}

/**
 * The chain a route travelled, as names.
 *
 * Subject first, ACL trustee last. A chain of one means the ACL names the subject itself,
 * and that is written out rather than left as an empty cell.
 */
export function chainLabels(chain: readonly PrincipalSummary[]): string[] {
  return chain.map((principal) => principal.display_name ?? principal.sam_account_name ?? principal.sid);
}

export interface PathStep {
  /** The edge kind, or `subject` for the synthetic first step. */
  kind: EdgeKind | "subject";
  edge: ExplanationEdgeView | null;
  from: ExplanationNodeView | null;
  to: ExplanationNodeView | null;
  label: string;
  /** Whether an administrator could delete this relationship at all. */
  removable: boolean;
  /** Why not, or what it is. Null when the label says everything. */
  note: string | null;
}

export interface PathDetail {
  path: CausalPathView;
  /** Every hop, in order: the subject, each membership edge, the trustee link, the entry. */
  steps: PathStep[];
  /** The exact ACE, joined by key. Null for an ownership route, which has no entry. */
  ace: AppliedAceView | null;
  /** Where an inherited entry was set. Null when the entry is explicit on this object. */
  inheritedFrom: string | null;
  /** Whether this route changed the final answer. */
  contributes: boolean;
  /** What it is worth, in a sentence. */
  worth: string;
}

/**
 * One route, opened up.
 *
 * The steps are built from the path's own `edges` list against the graph, so the inspector
 * cannot show a hop the diagram does not draw. The ACE is joined on `ace_key` across both
 * layers — a join on a stable identifier, not a recomputation — and is `null` for an
 * ownership route, where `ace_position` is `-1` and no entry confers the rights at all.
 */
export function describePath(
  explanation: AccessExplanationResponse,
  pathId: string,
): PathDetail | null {
  const path = explanation.paths.find((candidate) => candidate.id === pathId);
  if (path === undefined) {
    return null;
  }

  const nodesById = new Map(explanation.graph.nodes.map((node) => [node.id, node]));
  const edgesById = new Map(explanation.graph.edges.map((edge) => [edge.id, edge]));

  const first = path.nodes.length > 0 ? (nodesById.get(path.nodes[0]) ?? null) : null;
  const steps: PathStep[] = [
    {
      kind: "subject",
      edge: null,
      from: null,
      to: first,
      label: first === null ? "the subject" : nodeLabel(first),
      removable: false,
      note: "The principal the question is about.",
    },
  ];

  for (const edgeId of path.edges) {
    const edge = edgesById.get(edgeId);
    if (edge === undefined) {
      continue;
    }
    steps.push({
      kind: edge.kind,
      edge,
      from: nodesById.get(edge.source) ?? null,
      to: nodesById.get(edge.target) ?? null,
      label: edgeLabel(edge, nodesById),
      removable: edge.removable,
      note: edgeNote(edge),
    });
  }

  const ace = aceFor(explanation, path.ace_key);
  return {
    path,
    steps,
    ace,
    inheritedFrom: ace?.inherited_from ?? null,
    contributes: path.effect === "contributes",
    worth: pathWorth(path),
  };
}

/** What one route delivered to the final answer, in a sentence. */
export function pathWorth(path: CausalPathView): string {
  const effect = effectWording(path.effect);
  if (path.effect === "contributes") {
    return path.relation === "deny"
      ? `Withheld ${path.effective_rights.label} from the final answer.`
      : `Delivered ${path.effective_rights.label} to the final answer.`;
  }
  return effect.detail;
}

/**
 * The exact ACE behind a route.
 *
 * Searched across `granted_by`, `denied_by` and `superseded` on both layers, because a
 * route can end in an entry that matched and took nothing away — and an inspector that
 * silently found nothing for it would be hiding the most confusing entry on the ACL.
 */
export function aceFor(
  explanation: AccessExplanationResponse,
  aceKey: string | null,
): AppliedAceView | null {
  if (aceKey === null) {
    return null;
  }
  const evaluations = [explanation.effective.ntfs, explanation.effective.share];
  for (const evaluation of evaluations) {
    if (evaluation === null) {
      continue;
    }
    for (const applied of [...evaluation.granted_by, ...evaluation.denied_by, ...evaluation.superseded]) {
      if (applied.ace_key === aceKey) {
        return applied;
      }
    }
  }
  return null;
}

/* ------------------------------------------------------------------- the diagram */

export function nodeLabel(node: ExplanationNodeView): string {
  switch (node.kind) {
    case "ntfs_ace":
      return `NTFS entry #${node.position ?? 0}`;
    case "smb_ace":
      return `Share entry #${node.position ?? 0}`;
    case "resource":
    case "share":
      return node.display_name ?? node.key;
    case "assumed_trustee":
      return node.display_name ?? wellKnownName(node.sid) ?? node.key;
    default:
      return node.display_name ?? node.sid ?? node.key;
  }
}

/**
 * The names Windows puts in every token of a kind.
 *
 * Only the three ADG actually assumes. A lookup table of every well-known SID would be a
 * second identity resolver in a browser; these three exist because the response names them
 * as assumptions and an unlabelled `S-1-5-11` on a diagram explains nothing.
 */
function wellKnownName(sid: string | null): string | null {
  switch (sid) {
    case "S-1-1-0":
      return "Everyone";
    case "S-1-5-11":
      return "Authenticated Users";
    case "S-1-5-2":
      return "Network";
    default:
      return null;
  }
}

export function nodeKindLabel(kind: NodeKind): string {
  switch (kind) {
    case "principal":
      return "principal";
    case "group":
      return "group";
    case "assumed_trustee":
      return "assumed token SID";
    case "ntfs_ace":
      return "NTFS entry";
    case "smb_ace":
      return "share entry";
    case "resource":
      return "directory";
    case "share":
      return "share";
  }
}

export function edgeLabel(
  edge: ExplanationEdgeView,
  nodesById: ReadonlyMap<string, ExplanationNodeView>,
): string {
  const from = nodesById.get(edge.source);
  const to = nodesById.get(edge.target);
  const fromName = from === undefined ? edge.source : nodeLabel(from);
  const toName = to === undefined ? edge.target : nodeLabel(to);

  switch (edge.kind) {
    case "membership":
      return `${fromName} is a member of ${toName}`;
    case "assumed_membership":
      return `${fromName} is assumed to carry ${toName}`;
    case "trustee":
      return `${toName} names ${fromName}`;
    case "grant":
      return `${fromName} allows rights on ${toName}`;
    case "deny":
      return `${fromName} denies rights on ${toName}`;
    case "ownership":
      return `${fromName} owns ${toName}`;
  }
}

/** Why an edge cannot be removed, when it cannot. */
function edgeNote(edge: ExplanationEdgeView): string | null {
  if (edge.kind === "assumed_membership") {
    return "Assumed, not observed. There is no group to edit: Windows places this SID in the token.";
  }
  if (edge.kind === "ownership") {
    return "The rights come from owning the object. No entry confers them.";
  }
  if (!edge.removable) {
    return "Not a relationship an administrator can delete on its own.";
  }
  return null;
}

export interface LaidOutNode {
  node: ExplanationNodeView;
  column: number;
  row: number;
  x: number;
  y: number;
}

export interface LaidOutEdge {
  edge: ExplanationEdgeView;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface GraphLayout {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  width: number;
  height: number;
  /** One per occupied column, left to right. */
  columns: { index: number; label: string }[];
}

/** Geometry, exported so the component and its tests agree on one set of numbers. */
export const GRAPH_GEOMETRY = {
  nodeWidth: 188,
  nodeHeight: 38,
  columnGap: 76,
  rowGap: 18,
  padding: 16,
} as const;

/**
 * A deterministic left-to-right layering: subject, then groups by nesting depth, then the
 * entries that named them, then the object.
 *
 * Depth is the **longest** route from the subject rather than the shortest, so a group
 * reached both directly and through two hops is drawn to the right of the chain that
 * reaches it, and no edge points backwards. Relaxation is capped at one pass per node,
 * which is what makes a membership cycle terminate here instead of hanging the page — a
 * cycle is a real finding the API reports in `cycles`, not a rendering artifact to
 * tolerate.
 *
 * Ordering within a column is by label and then by id, so the same answer draws the same
 * diagram every time. A layout that moved between two renders of identical facts would
 * make a screenshot useless as evidence.
 */
export function layoutGraph(graph: ExplanationGraphView): GraphLayout {
  const membership = graph.edges.filter(
    (edge) => edge.kind === "membership" || edge.kind === "assumed_membership",
  );

  const depth = new Map<string, number>();
  for (const node of graph.nodes) {
    depth.set(node.id, node.kind === "principal" ? 0 : isTrusteeNode(node) ? 1 : 0);
  }

  const ordered = [...membership].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (let pass = 0; pass < graph.nodes.length; pass += 1) {
    let moved = false;
    for (const edge of ordered) {
      const source = depth.get(edge.source);
      const target = depth.get(edge.target);
      if (source === undefined || target === undefined) {
        continue;
      }
      if (target < source + 1) {
        depth.set(edge.target, source + 1);
        moved = true;
      }
    }
    if (!moved) {
      break;
    }
  }

  let deepestTrustee = 0;
  for (const node of graph.nodes) {
    if (isTrusteeNode(node)) {
      deepestTrustee = Math.max(deepestTrustee, depth.get(node.id) ?? 1);
    }
  }
  const aceColumn = deepestTrustee + 1;
  const objectColumn = aceColumn + 1;

  const columnOf = (node: ExplanationNodeView): number => {
    switch (node.kind) {
      case "principal":
        return 0;
      case "group":
      case "assumed_trustee":
        return Math.max(1, depth.get(node.id) ?? 1);
      case "ntfs_ace":
      case "smb_ace":
        return aceColumn;
      case "resource":
      case "share":
        return objectColumn;
    }
  };

  const byColumn = new Map<number, ExplanationNodeView[]>();
  for (const node of graph.nodes) {
    const column = columnOf(node);
    const bucket = byColumn.get(column);
    if (bucket === undefined) {
      byColumn.set(column, [node]);
    } else {
      bucket.push(node);
    }
  }

  const placed = new Map<string, LaidOutNode>();
  const { nodeWidth, nodeHeight, columnGap, rowGap, padding } = GRAPH_GEOMETRY;
  let tallest = 0;

  for (const [column, members] of [...byColumn].sort((a, b) => a[0] - b[0])) {
    members.sort((a, b) => {
      const left = nodeLabel(a);
      const right = nodeLabel(b);
      if (left !== right) {
        return left < right ? -1 : 1;
      }
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    members.forEach((node, row) => {
      placed.set(node.id, {
        node,
        column,
        row,
        x: padding + column * (nodeWidth + columnGap),
        y: padding + row * (nodeHeight + rowGap),
      });
    });
    tallest = Math.max(tallest, members.length);
  }

  const edges: LaidOutEdge[] = [];
  for (const edge of graph.edges) {
    const from = placed.get(edge.source);
    const to = placed.get(edge.target);
    if (from === undefined || to === undefined) {
      continue;
    }
    edges.push({
      edge,
      x1: from.x + nodeWidth,
      y1: from.y + nodeHeight / 2,
      x2: to.x,
      y2: to.y + nodeHeight / 2,
    });
  }

  const widest = Math.max(0, ...[...byColumn.keys()]);
  return {
    nodes: [...placed.values()].sort((a, b) =>
      a.column === b.column ? a.row - b.row : a.column - b.column,
    ),
    edges,
    width: padding * 2 + (widest + 1) * nodeWidth + widest * columnGap,
    height: padding * 2 + Math.max(1, tallest) * nodeHeight + Math.max(0, tallest - 1) * rowGap,
    columns: [...byColumn.keys()]
      .sort((a, b) => a - b)
      .map((index) => ({
        index,
        label:
          index === 0
            ? "Principal"
            : index === objectColumn
              ? "Object"
              : index === aceColumn
                ? "Entries"
                : `Groups (${index} hop${index === 1 ? "" : "s"})`,
      })),
  };
}

function isTrusteeNode(node: ExplanationNodeView): boolean {
  return node.kind === "group" || node.kind === "assumed_trustee";
}

/**
 * The diagram as rows.
 *
 * Not a summary of it and not a different selection from it — every edge the layout draws
 * is one row here, which is what lets `tests/explanation.test.ts` assert the two views
 * carry the same facts. Accessibility that is a paraphrase is accessibility that drifts.
 */
export interface StructureRow {
  edgeId: string;
  kind: EdgeKind;
  from: string;
  to: string;
  description: string;
  removable: boolean;
  /** Ids of the routes that travel this edge. */
  paths: string[];
}

export function structureRows(explanation: AccessExplanationResponse): StructureRow[] {
  const nodesById = new Map(explanation.graph.nodes.map((node) => [node.id, node]));
  const travelled = new Map<string, string[]>();
  for (const path of explanation.paths) {
    for (const edgeId of path.edges) {
      const existing = travelled.get(edgeId);
      if (existing === undefined) {
        travelled.set(edgeId, [path.id]);
      } else {
        existing.push(path.id);
      }
    }
  }

  return explanation.graph.edges.map((edge) => {
    const from = nodesById.get(edge.source);
    const to = nodesById.get(edge.target);
    return {
      edgeId: edge.id,
      kind: edge.kind,
      from: from === undefined ? edge.source : nodeLabel(from),
      to: to === undefined ? edge.target : nodeLabel(to),
      description: edgeLabel(edge, nodesById),
      removable: edge.removable,
      paths: travelled.get(edge.id) ?? [],
    };
  });
}

/* ------------------------------------------------------------------- what a fix does */

export interface RemovalNote {
  target: RemovalTargetView;
  headline: string;
  tone: Tone;
  detail: string;
  /** The routes that keep delivering rights afterwards. This is why a fix may not be one. */
  alternates: CausalPathView[];
  /** Whether this may be offered as a remediation at all. */
  offerAsFix: boolean;
}

/**
 * What deleting one relationship would actually do — measured, not predicted.
 *
 * The API re-ran the whole access check with the edge gone, so these are results rather
 * than a model of results. Three of the four cases are ones a naive screen gets wrong:
 *
 * * `rights_added` non-empty is a **warning**. The edge carried a Deny, and removing it
 *   widens access. Offering it as a fix would be offering the opposite of one.
 * * `changes_nothing` must never be presented as a remediation, and the alternates are
 *   shown instead — they are the reason it changes nothing.
 * * `revokes_all_access: false` with rights still removed is a partial fix, and saying
 *   "removes access" about it is how somebody closes a ticket on a share still open.
 */
export function describeRemoval(
  explanation: AccessExplanationResponse,
  target: RemovalTargetView,
): RemovalNote {
  const byId = new Map(explanation.paths.map((path) => [path.id, path]));
  const alternates = target.alternate_paths
    .map((id) => byId.get(id))
    .filter((path): path is CausalPathView => path !== undefined);

  if (target.rights_added.value > 0) {
    return {
      target,
      headline: "Would widen access",
      tone: "bad",
      detail:
        `This relationship carries a Deny. Removing it would grant ` +
        `${target.rights_added.label}, not take anything away.`,
      alternates,
      offerAsFix: false,
    };
  }

  if (target.changes_nothing) {
    return {
      target,
      headline: "Changes nothing",
      tone: "warn",
      detail:
        alternates.length > 0
          ? `Access survives this: ${alternates.length} other route` +
            `${alternates.length === 1 ? "" : "s"} still deliver` +
            `${alternates.length === 1 ? "s" : ""} the same rights.`
          : "Removing this leaves the effective rights exactly as they are.",
      alternates,
      offerAsFix: false,
    };
  }

  if (target.revokes_all_access) {
    return {
      target,
      headline: "Removes all access",
      tone: "ok",
      detail: `Removing this leaves ${target.rights_after.label}.`,
      alternates,
      offerAsFix: true,
    };
  }

  return {
    target,
    headline: "Narrows access",
    tone: "warn",
    detail:
      `Removes ${target.rights_removed.label} and leaves ${target.rights_after.label}. ` +
      `Access is not revoked.`,
    alternates,
    offerAsFix: true,
  };
}

/** Single removals that end all access here. Empty is the common case, not an error. */
export function sufficientRemovals(
  explanation: AccessExplanationResponse,
): readonly RemovalTargetView[] {
  return explanation.removal_targets.filter(
    (target) => target.revokes_all_access && !target.changes_nothing,
  );
}

/** Removals that look like fixes and are not. The headline warning of this screen. */
export function ineffectiveRemovals(
  explanation: AccessExplanationResponse,
): readonly RemovalTargetView[] {
  return explanation.removal_targets.filter((target) => target.changes_nothing);
}

/**
 * The sentence that has to be on the page whenever a removal would not be a fix.
 *
 * Returns null only when there is genuinely nothing to warn about. An incomplete
 * enumeration produces a warning on its own, because "no single removal suffices" and "we
 * did not enumerate every route" are different reasons to distrust a fix and an
 * administrator needs to know which one they are looking at.
 */
export function alternateRouteWarning(explanation: AccessExplanationResponse): string | null {
  if (!explanation.complete) {
    return (
      "Not every route was enumerated, so no removal below may be believed sufficient — " +
      "a route that was not listed can still deliver these rights."
    );
  }

  const ineffective = ineffectiveRemovals(explanation);
  if (ineffective.length > 0) {
    const sufficient = sufficientRemovals(explanation);
    if (sufficient.length === 0) {
      return (
        `Removing any single relationship below changes nothing: ${ineffective.length} of ` +
        `them were measured and access survived each one, because alternative routes remain. ` +
        `More than one change is needed here.`
      );
    }
    return (
      `${ineffective.length} of the relationships below change nothing when removed, ` +
      `because alternative routes deliver the same rights. They are marked, and are not fixes.`
    );
  }

  if (explanation.verdict.outcome === "granted" && explanation.removal_targets.length === 0) {
    return "No removable relationship was measured for this answer, so no single removal is known to help.";
  }

  return null;
}

/* ---------------------------------------------------------- what could be wrong */

export interface Caution {
  id: string;
  headline: string;
  detail: string;
  severity: "warning" | "info";
}

/**
 * Everything that qualifies this answer, in one list.
 *
 * Gathered here rather than scattered through the components so that the screen and the
 * text export cannot disagree about what the caveats were — an exported explanation that
 * drops the warning is worse than one that was never exported.
 */
export function cautions(explanation: AccessExplanationResponse): Caution[] {
  const notes: Caution[] = [];

  if (explanation.basis.is_empty) {
    notes.push({
      id: "empty-basis",
      headline: "Nothing has been collected",
      detail:
        "No collector run is on record. Every answer over an empty estate reports no " +
        "access, and here that means nobody has looked.",
      severity: "warning",
    });
  }

  if (!explanation.verdict.conclusive) {
    notes.push({
      id: "inconclusive",
      headline: "This answer does not support a negative conclusion",
      detail:
        "No right was established and collection is incomplete in a direction that could " +
        "hide one. Do not record this as 'no access'.",
      severity: "warning",
    });
  }

  if (!explanation.complete) {
    notes.push({
      id: "incomplete-paths",
      headline: "These are not all the routes",
      detail:
        "The enumeration hit a limit, so at least one more route to these rights may " +
        "exist. Nothing may be concluded from the absence of one." +
        (explanation.truncation.length > 0
          ? ` Limits reached: ${explanation.truncation.join(", ")}.`
          : ""),
      severity: "warning",
    });
  }

  if (!explanation.token.membership_complete) {
    notes.push({
      id: "incomplete-token",
      headline: "The access token is a lower bound",
      detail:
        "The membership traversal hit a limit, so the subject may belong to groups that " +
        "are not in the token this answer was computed against.",
      severity: "warning",
    });
  }

  if (!explanation.resource.observed) {
    notes.push({
      id: "unobserved-resource",
      headline: "No run has read this directory's descriptor",
      detail:
        "The NTFS side of this answer rests on what was inherited or assumed rather than " +
        "on entries anybody read here.",
      severity: "warning",
    });
  }

  if (explanation.effective.acl_provenance !== "observed") {
    notes.push({
      id: `provenance-${explanation.effective.acl_provenance}`,
      headline:
        explanation.effective.acl_provenance === "unobserved"
          ? "The ACL behind this answer was never read"
          : "The ACL behind this answer was reconstructed",
      detail:
        explanation.effective.acl_provenance === "unobserved"
          ? "No collector has read the descriptor this answer needs."
          : "The entries were derived from an ancestor's inheritable entries rather than read here.",
      severity: "warning",
    });
  }

  if (explanation.cycles.length > 0) {
    notes.push({
      id: "cycles",
      headline: `${explanation.cycles.length} membership cycle${explanation.cycles.length === 1 ? "" : "s"}`,
      detail:
        "A group is nested inside itself somewhere in this subgraph. That is a finding " +
        "about the directory, not a drawing artifact: " +
        explanation.cycles.map((cycle) => cycle.join(" → ")).join("; "),
      severity: "warning",
    });
  }

  for (const warning of explanation.warnings) {
    notes.push({
      id: `finding-${warning.condition}`,
      headline: findingHeadline(warning),
      detail: warning.message,
      severity: warning.may_overstate || warning.may_understate ? "warning" : "info",
    });
  }

  return notes;
}

function findingHeadline(finding: FindingView): string {
  if (finding.may_overstate && finding.may_understate) {
    return "Could make this answer wrong in either direction";
  }
  if (finding.may_overstate) {
    return "Could overstate access";
  }
  if (finding.may_understate) {
    return "Could understate access";
  }
  return "Recorded for context";
}

/* ------------------------------------------------------------------------- export */

/** The response itself, pretty-printed. The structured export is the API's own facts. */
export function explanationJson(explanation: AccessExplanationResponse): string {
  return JSON.stringify(explanation, null, 2);
}

/**
 * The explanation as text, for a ticket.
 *
 * Deterministic and derived only from the response — no clock, no locale formatting — so
 * that pasting the same answer twice produces the same text and a diff between two of them
 * is a difference in the estate.
 *
 * The caveats are last and are never omitted. An explanation that travels without them is
 * how "ADG says nobody has access" gets quoted about a directory nobody scanned.
 */
export function explanationText(explanation: AccessExplanationResponse): string {
  const wording = outcomeWording(explanation.verdict);
  const subject = explanation.subject.display_name ?? explanation.subject.sid;
  const resource = explanation.resource.path ?? explanation.resource.key;
  const lines: string[] = [];

  lines.push(`ADG access explanation`);
  lines.push(`Principal: ${subject} (${explanation.subject.sid})`);
  lines.push(`Directory: ${resource}`);
  lines.push(`Access path: ${explanation.access_path}`);
  lines.push(
    `As of: ${explanation.basis.latest_activity_at ?? "no collector run on record"}` +
      ` (basis ${explanation.basis.token})`,
  );
  lines.push("");

  lines.push(`VERDICT: ${wording.word.toUpperCase()} — ${wording.headline}`);
  lines.push(`Effective rights: ${explanation.effective.rights.label} (${explanation.effective.rights.mask})`);
  lines.push(`Certainty: ${explanation.verdict.certainty} — ${certaintyNote(explanation.verdict.certainty)}`);
  lines.push(`Conclusive: ${explanation.verdict.conclusive ? "yes" : "no"}`);
  lines.push(`Narrower layer: ${explanation.effective.limiting_layer}`);
  lines.push("");

  lines.push(`SHARE ACL: ${explanation.effective.share?.rights.label ?? "not applied on this access path"}`);
  lines.push(`NTFS ACL: ${explanation.effective.ntfs.rights.label} (${explanation.effective.ntfs.rights.mask})`);
  lines.push("");

  lines.push(`ROUTES (${explanation.paths.length}${explanation.complete ? "" : ", not all of them"}):`);
  if (explanation.paths.length === 0) {
    lines.push("  none — no entry on either ACL reached this principal");
  }
  for (const path of explanation.paths) {
    const chain = chainLabels(path.chain);
    lines.push(
      `  [${path.id}] ${relationLabel(path.relation)} on ${path.layer}` +
        ` entry #${path.ace_position} — ${effectWording(path.effect).label}`,
    );
    lines.push(`      via: ${chain.length > 0 ? chain.join(" -> ") : "named directly"}`);
    lines.push(`      worth: ${pathWorth(path)}`);
    if (path.assumed) {
      lines.push(`      reached through an assumed token SID, not an observed membership`);
    }
    if (path.inherited) {
      const ace = aceFor(explanation, path.ace_key);
      lines.push(`      inherited from: ${ace?.inherited_from ?? "an ancestor"}`);
    }
  }
  lines.push("");

  const warning = alternateRouteWarning(explanation);
  lines.push(`REMOVALS (measured, ${explanation.removal_targets.length}):`);
  if (warning !== null) {
    lines.push(`  ! ${warning}`);
  }
  for (const target of explanation.removal_targets) {
    const note = describeRemoval(explanation, target);
    lines.push(`  [${target.edge_id}]`);
    lines.push(`      ${note.headline}: ${note.detail}`);
    if (note.alternates.length > 0) {
      lines.push(`      alternate routes: ${note.alternates.map((path) => path.id).join(", ")}`);
    }
  }
  lines.push("");

  const notes = cautions(explanation);
  lines.push(`CAVEATS (${notes.length}):`);
  if (notes.length === 0) {
    lines.push("  none recorded");
  }
  for (const note of notes) {
    lines.push(`  - ${note.headline}: ${note.detail}`);
  }

  return lines.join("\n");
}
