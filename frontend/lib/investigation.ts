/**
 * The guided access investigation (ADG-DEMO-02), worded.
 *
 * `GET /api/v1/access/explain` answers "why does this principal have this access?"
 * completely: every route, both ACLs, the measured effect of every removal. Contract 1.1
 * adds two things to it — `evidence`, the provenance of every edge, and `investigation`,
 * the engine's own summary of the answer as the seven questions an investigator asks — and
 * this module turns those into sentences, in the order the investigation page reads them.
 *
 * **It decides nothing.** Which route carries the access, whether a route is direct or
 * inherited, what removing it would do, whether an open question can change the
 * conclusion, and what to look at next all arrive computed (see
 * `docs/contracts/derived-responses.md`, "The one rule"). What happens here is naming,
 * ordering and formatting: a principal by its label, an instant as UTC, a code as a
 * sentence. A function in this file that compared two masks or decided which path mattered
 * would be a second engine, and the first one to be wrong.
 */

import type { PrincipalSummary, RightsView } from "@/lib/contracts";
import type {
  AccessExplanationResponse,
  AccessPresence,
  AppliedAceView,
  CausalPathView,
  ConclusionImpact,
  EdgeKind,
  EntryPlacement,
  EvidenceState,
  ExplanationEdgeView,
  ExplanationNodeView,
  HopBasis,
  HopEvidenceView,
  InvestigationView,
  NextStepView,
  OpenQuestionView,
  PrimaryRouteView,
  RemovalEffect,
  RouteKind,
} from "@/lib/derived";
import {
  aceFor,
  certaintyNote,
  explainHref,
  explanationWording,
  nodeLabel,
  relationLabel,
  SYSTEM_MAPPING_NEXT_STEP,
  systemMapping,
  type Tone,
} from "@/lib/explanation";
import { isUnresolved, principalHref, principalLabel } from "@/lib/identity";

/** Where the investigation page lives. */
export const INVESTIGATE_PATH = "/access/investigate";

/** A link to the guided investigation of one pair. Same parameters as the explanation. */
export function investigateHref(
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
  return `${INVESTIGATE_PATH}?${params.toString()}`;
}

/** The anchors of the page's sections, in reading order. The walkthrough links to them. */
export const SECTION = {
  question: "question",
  observed: "observed-access",
  routes: "access-routes",
  openQuestions: "open-questions",
  summary: "investigation-summary",
  nextSteps: "next-steps",
  /** ADG-DEMO-03: the collection behind the answer (`AnswerCoveragePanel`). */
  coverage: "collection-coverage",
} as const;

/* ------------------------------------------------------------------ the question */

/** "Why does Fatima Gordon have access to \\GP-ENG-FS01\…\Apollo?" */
export function investigationQuestion(explanation: AccessExplanationResponse): string {
  const resource = explanation.resource.path ?? explanation.resource.key;
  return `Why does ${principalLabel(explanation.subject)} have access to ${resource}?`;
}

export function accessPathLabel(accessPath: string): string {
  return accessPath === "local"
    ? "On the server itself (NTFS ACL only)"
    : "Over the network (share ACL and NTFS ACL)";
}

const LAYER_LABEL: Record<string, string> = {
  ntfs: "Folder permissions (NTFS ACL)",
  smb_share: "Share permissions (SMB share ACL)",
};

export function layerLabel(layer: string): string {
  return LAYER_LABEL[layer] ?? layer;
}

/**
 * One ACL's rights, as the observed-access panel prints them.
 *
 * The engine labels an empty mask "No access". On an inconclusive answer that label would
 * restate the conclusion the verdict withheld — the contradiction ADG-DEMO-01 removed from
 * the verdict panel and the text export — so an empty layer there reads "none established".
 */
export function layerRightsWording(rights: RightsView, conclusive: boolean): string {
  if (rights.value === 0 && !conclusive) {
    return "none established";
  }
  return rights.label;
}

/* ------------------------------------------------------------ question 1: presence */

export interface PresenceWording {
  word: string;
  tone: Tone;
  sentence: string;
}

export function presenceWording(
  presence: AccessPresence,
  explanation: AccessExplanationResponse,
): PresenceWording {
  const rights = explanation.effective.rights.label;
  switch (presence) {
    case "present":
      return {
        word: "Access present",
        tone: "bad",
        sentence: `Yes. ${principalLabel(explanation.subject)} holds ${rights} here. ${certaintyNote(explanation.verdict.certainty)}`,
      };
    case "absent":
      return {
        word: "No access",
        tone: "ok",
        sentence: `No. ${explanationWording(explanation).headline}`,
      };
    case "undetermined":
      return {
        word: "Not determined",
        tone: "warn",
        sentence:
          "Not determined. No right was established and the evidence is incomplete in a " +
          "direction that could hide one, so this is not a finding of no access.",
      };
  }
}

/* --------------------------------------------------- questions 2-4: the routes */

const ROUTE_KIND_LABEL: Record<RouteKind, string> = {
  direct: "Direct entry",
  group: "Through group membership",
  assumed: "Through an assumed token SID",
  ownership: "Owner rights",
};

export function routeKindLabel(route: RouteKind, hops: number): string {
  if (route === "group") {
    return hops === 1 ? "Through 1 group membership" : `Through ${hops} nested groups`;
  }
  return ROUTE_KIND_LABEL[route];
}

export function placementLabel(placement: EntryPlacement, layer: string): string {
  switch (placement) {
    case "explicit":
      return layer === "smb_share" ? "Set on the share" : "Set explicitly on this folder";
    case "inherited":
      return "Inherited from a parent folder";
    case "owner":
      return "Not an entry: implicit owner rights";
  }
}

/**
 * Direct versus derived, the distinction the investigation is about.
 *
 * Two axes, never collapsed into one word: how the entry reaches the principal (named
 * directly, or derived through memberships or an assumed SID), and where the entry was set
 * (on this object, or inherited from a parent). "Inherited" alone would be ambiguous between
 * them, and the remediation is different for each.
 */
export function routeClassification(path: CausalPathView): { reach: string; placement: string } {
  const reach = path.assumed
    ? "derived (assumed token SID)"
    : path.ace_position < 0
      ? "owner"
      : path.via_group
        ? `derived (${path.chain.length - 1} group${path.chain.length === 2 ? "" : "s"})`
        : "direct";
  const placement = path.ace_position < 0 ? "owner" : path.inherited ? "inherited" : "explicit";
  return { reach, placement };
}

const REMOVAL_WORDING: Record<RemovalEffect, string> = {
  revokes_access: "Removing it revokes the access entirely: this is the only route.",
  narrows_access: "Removing it narrows the access; other routes deliver the rest.",
  changes_nothing: "Removing it changes nothing: other routes deliver the same rights.",
  widens_access: "Removing it would widen access: the same edge also carries a Deny.",
};

export function removalSentence(route: PrimaryRouteView): string {
  if (route.removal_effect === null) {
    return route.route === "ownership"
      ? "Owner rights are not an edge; changing the owner is the remediation."
      : "The effect of removing it was not measured within the removal limit.";
  }
  return REMOVAL_WORDING[route.removal_effect];
}

export interface Hop {
  edge: ExplanationEdgeView;
  /** One sentence, in the direction the access travels. */
  sentence: string;
  /** What kind of relationship this is, in words. */
  relationship: string;
  /** The principal at the near end, when it is one: the subject or a group on the chain. */
  principal: PrincipalSummary | null;
  evidence: HopEvidenceView | null;
}

/**
 * Every hop of one route, in order: the memberships, the entry naming the last of them,
 * and the entry applying to the object. Built from the path's own edge list against the
 * graph, so it cannot show a hop the explanation does not contain.
 */
export function routeHops(explanation: AccessExplanationResponse, path: CausalPathView): Hop[] {
  const nodes = new Map(explanation.graph.nodes.map((node) => [node.id, node]));
  const edges = new Map(explanation.graph.edges.map((edge) => [edge.id, edge]));
  const evidence = new Map((explanation.evidence ?? []).map((item) => [item.edge_id, item]));
  const ace = aceFor(explanation, path.ace_key);
  const chainByNode = new Map<string, PrincipalSummary>();
  path.chain.forEach((principal, index) => {
    const id = path.nodes[index];
    if (id !== undefined) {
      chainByNode.set(id, principal);
    }
  });

  const hops: Hop[] = [];
  for (const edgeId of path.edges) {
    const edge = edges.get(edgeId);
    if (edge === undefined) {
      continue;
    }
    const from = nodes.get(edge.source);
    const to = nodes.get(edge.target);
    hops.push({
      edge,
      sentence: hopSentence(edge.kind, label(from, chainByNode), label(to, chainByNode), ace, path),
      relationship: RELATIONSHIP[edge.kind],
      principal: chainByNode.get(edge.source) ?? null,
      evidence: evidence.get(edge.id) ?? null,
    });
  }
  return hops;
}

const RELATIONSHIP: Record<EdgeKind, string> = {
  membership: "group membership",
  assumed_membership: "assumed token SID",
  trustee: "ACL entry",
  grant: "entry applies to the object",
  deny: "entry applies to the object",
  ownership: "ownership",
};

function label(
  node: ExplanationNodeView | undefined,
  chain: ReadonlyMap<string, PrincipalSummary>,
): string {
  if (node === undefined) {
    return "an unknown node";
  }
  const principal = chain.get(node.id);
  return principal === undefined ? nodeLabel(node) : principalLabel(principal);
}

function hopSentence(
  kind: EdgeKind,
  from: string,
  to: string,
  ace: AppliedAceView | null,
  path: CausalPathView,
): string {
  switch (kind) {
    case "membership":
      return `${from} is a member of ${to}`;
    case "assumed_membership":
      return `${from} carries ${to} in its token (assumed by Windows, not a membership)`;
    case "trustee":
      // The entry's rights as the engine labelled them: what the entry is worth alone.
      return `${to} names ${from}: ${relationLabel(path.relation)} ${path.ace_rights.label}`;
    case "grant":
    case "deny":
      if (ace?.source === "inherited") {
        return `${from} applies to this folder, inherited${ace.inherited_from ? ` from ${ace.inherited_from}` : " from a parent"}`;
      }
      return `${from} applies to this ${ace?.layer === "smb_share" ? "share" : "folder"}, set explicitly`;
    case "ownership":
      return `${from} owns ${to}`;
  }
}

/* -------------------------------------------------------------- the evidence */

const BASIS_LABEL: Record<HopBasis, string> = {
  observed: "Observed",
  assumed: "Assumed",
  projected: "Projected",
  unrecorded: "Not recorded",
};

const BASIS_DETAIL: Record<HopBasis, string> = {
  observed: "A collector reported this relationship; its provenance is below.",
  assumed:
    "Windows places this SID in every token of this kind. No collector reports it and no " +
    "row exists to edit.",
  projected:
    "This folder's own descriptor was never read, so the entry is projected from the " +
    "nearest parent that was.",
  unrecorded:
    "No stored row backs this hop in this answer. Treat it as unverified.",
};

export function basisLabel(basis: HopBasis | null | undefined): string {
  return basis ? BASIS_LABEL[basis] : "Not reported";
}

export function basisDetail(basis: HopBasis | null | undefined): string {
  return basis
    ? BASIS_DETAIL[basis]
    : "This server did not report provenance for this answer (contract 1.0).";
}

const COLLECTOR_LABEL: Record<string, string> = {
  active_directory: "Active Directory collector",
  local_groups: "Local groups collector",
  smb: "SMB share collector",
  ntfs: "NTFS collector",
};

export function collectorLabel(collector: string | null): string {
  if (collector === null) {
    return "unknown collector";
  }
  return COLLECTOR_LABEL[collector] ?? collector;
}

const RECORD_LABEL: Record<NonNullable<HopEvidenceView["record"]>, string> = {
  membership_edge: "membership row",
  ntfs_ace: "NTFS entry row",
  smb_share_ace: "share entry row",
  ntfs_resource: "folder descriptor row",
};

export function recordLabel(record: HopEvidenceView["record"]): string {
  return record === null ? "none" : RECORD_LABEL[record];
}

/**
 * An instant as the API sent it, rendered in UTC to the minute.
 *
 * Deliberately not the browser's locale: a collection time is a fact about the estate, and
 * two reviewers reading one answer must see the same instant.
 */
export function formatInstant(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(value);
  return match === null ? value : `${match[1]} ${match[2]} UTC`;
}

/* ------------------------------------------------- questions 5 and 6: the gaps */

const STATE_LABEL: Record<EvidenceState, string> = {
  confirmed: "Confirmed",
  ambiguous: "Ambiguous",
  missing: "Missing evidence",
  conflicting: "Conflicting evidence",
  assumed: "Assumed, not observed",
  derived: "Derived",
  context: "Context",
};

/**
 * A text symbol for each state, so the state never rests on color alone. Rendered beside
 * the label, hidden from assistive technology (the label already says it).
 */
const STATE_SYMBOL: Record<EvidenceState, string> = {
  confirmed: "✓",
  ambiguous: "≈",
  missing: "?",
  conflicting: "≠",
  assumed: "~",
  derived: "→",
  context: "i",
};

export function stateLabel(state: EvidenceState): string {
  return STATE_LABEL[state];
}

export function stateSymbol(state: EvidenceState): string {
  return STATE_SYMBOL[state];
}

export function stateTone(state: EvidenceState): Tone {
  switch (state) {
    case "confirmed":
      return "ok";
    case "missing":
    case "ambiguous":
      return "warn";
    case "conflicting":
      return "bad";
    default:
      return "info";
  }
}

/** Whether, and how, one open question reaches the conclusion. */
export function impactSentence(impact: ConclusionImpact, presence: AccessPresence): string {
  switch (impact) {
    case "none":
      return "Recorded for context. It cannot change this answer.";
    case "extent":
      return (
        "Does not affect whether access exists. The rights shown are a lower bound: this " +
        "could only widen them."
      );
    case "presence":
      return presence === "present"
        ? "Affects the conclusion: this could narrow the rights shown, possibly to none."
        : "Affects the conclusion: a grant could be hidden here, so “no access” cannot be concluded.";
  }
}

export function impactLabel(impact: ConclusionImpact): string {
  switch (impact) {
    case "none":
      return "Does not affect the answer";
    case "extent":
      return "Does not affect the conclusion";
    case "presence":
      return "Affects the conclusion";
  }
}

/** The open questions worth an investigator's time: everything that is not context. */
export function openQuestions(investigation: InvestigationView): OpenQuestionView[] {
  return investigation.open_questions.filter((item) => item.impact !== "none");
}

/** The questions recorded for context only — assumptions, protected DACLs. */
export function contextNotes(investigation: InvestigationView): OpenQuestionView[] {
  return investigation.open_questions.filter((item) => item.impact === "none");
}

/** Who an open question is about, honest about an identity nobody resolved. */
export function aboutLabel(about: PrincipalSummary | null): string | null {
  if (about === null) {
    return null;
  }
  const name = principalLabel(about);
  return isUnresolved(about) ? `${name} (unresolved SID ${about.sid})` : name;
}

/** Question 6, for the answer as a whole. */
export function conclusionSentence(
  investigation: InvestigationView,
  explanation: AccessExplanationResponse,
): string {
  const open = openQuestions(investigation).length;
  const questions = open === 1 ? "1 open question" : `${open} open questions`;
  switch (investigation.presence) {
    case "present":
      if (investigation.impact === "none") {
        return "Access is established and nothing open qualifies it: every input this answer depends on was collected.";
      }
      if (investigation.impact === "extent") {
        return (
          `Access is established through the routes shown. ${questions} remain${open === 1 ? "s" : ""}, ` +
          "and none of them can take that access away: they mean the rights shown are a lower bound."
        );
      }
      return (
        `Access was established, but ${questions} could narrow it, possibly to none. Treat ` +
        "the answer as provisional until they are settled."
      );
    case "undetermined":
      return (
        `ADG could not determine whether access exists: ${questions} could hide a grant. ` +
        "This is not a finding of no access."
      );
    case "absent":
      return investigation.impact === "none"
        ? `No access, and nothing open could hide a grant. ${explanationWording(explanation).guidance}`
        : `No access was found, but ${questions} could hide a grant.`;
  }
}

/* ---------------------------------------------------- question 7: what next */

export interface NextStepWording {
  text: string;
  href: string | null;
  linkLabel: string | null;
}

export function nextStepWording(
  step: NextStepView,
  explanation: AccessExplanationResponse,
  investigation: InvestigationView,
): NextStepWording {
  const subject = principalLabel(explanation.subject);
  const about = step.about === null ? null : aboutLabel(step.about);
  const aboutLink = step.about === null ? null : principalHref(step.about);
  const route = investigation.primary_routes.find((item) => item.path_id === step.path_id);
  const explain = `${explainHref(explanation.subject.key, explanation.resource.path ?? explanation.resource.key, { access_path: explanation.access_path })}`;

  switch (step.code) {
    case "resolve_trustee_identity": {
      const sid = step.about?.sid ?? "the unresolved SID";
      const lastKnown = step.about?.last_known_name;
      return {
        text:
          `Identify ${sid}${lastKnown ? `, last known as ${lastKnown}` : ""}: an ACL here names it ` +
          `and no collector could say what it is. Until it is identified, ${subject} cannot be ` +
          "ruled out of it. If it is an account that no longer exists, the orphaned entry is " +
          "the clean-up.",
        href: aboutLink,
        linkLabel: aboutLink === null ? null : "Inspect the SID",
      };
    }
    case "collect_group_membership":
      return {
        text:
          `Collect the membership of ${about ?? "the group"}, or confirm it is empty: ADG has ` +
          `never observed a member of it, so ${subject} cannot be ruled out of it.`,
        href: aboutLink,
        linkLabel: aboutLink === null ? null : "Inspect the group",
      };
    case "settle_system_mapping": {
      const mapping = systemMapping(explanation);
      const text =
        mapping !== null && mapping.state !== "confirmed"
          ? SYSTEM_MAPPING_NEXT_STEP[mapping.state]
          : "Settle which identity SYSTEM stands for here before relying on this answer.";
      return { text, href: null, linkLabel: null };
    }
    case "resolve_subject_identity":
      return {
        text: `Describe ${subject}: no collector has, so its memberships may be incomplete.`,
        href: null,
        linkLabel: null,
      };
    case "collect_subject_membership":
      return {
        text:
          `Re-collect ${subject}'s group memberships: the traversal that built the token hit ` +
          "a limit, so a group it belongs to may be missing.",
        href: null,
        linkLabel: null,
      };
    case "rescan_resource":
      return {
        text:
          "Re-scan this folder and its share: an ACL this answer depends on was not read, was " +
          "projected from a parent, or was read only in part.",
        href: null,
        linkLabel: null,
      };
    case "review_redundant_entry":
      return {
        text:
          `Review ${subject}'s direct entry on the ${route ? layerLabel(route.layer).toLowerCase() : "ACL"}: ` +
          "removing it was measured to change nothing, because other routes deliver the same " +
          "rights. It is a candidate for clean-up; simulate the removal before changing the ACL.",
        href: `${explain}#removals`,
        linkLabel: "Open the removal analysis",
      };
    case "review_sole_route":
      return {
        text:
          `Confirm the route on the ${route ? layerLabel(route.layer).toLowerCase() : "ACL"} is ` +
          "intended: it is the only one, so removing its first link revokes the access.",
        href: `${explain}#removals`,
        linkLabel: "Open the removal analysis",
      };
    case "review_finding": {
      const question = investigation.open_questions.find(
        (item) => item.condition === step.condition,
      );
      return {
        text: `Review: ${question?.message ?? step.condition ?? "an open question"}`,
        href: aboutLink,
        linkLabel: aboutLink === null ? null : "Inspect",
      };
    }
    case "inspect_explanation":
      return {
        text:
          "Nothing is open. The full explanation shows every route, both ACLs and the " +
          "measured effect of removing each relationship.",
        href: explain,
        linkLabel: "Open the full explanation",
      };
  }
}

/* ---------------------------------------------------------- the seven answers */

export interface SummaryAnswer {
  question: string;
  answer: string[];
}

/** One route, as the investigator reads it: chain, entry, rights. */
export function routeLine(explanation: AccessExplanationResponse, route: PrimaryRouteView): string {
  const path = explanation.paths.find((item) => item.id === route.path_id);
  if (path === undefined) {
    return `${layerLabel(route.layer)}: route ${route.path_id}`;
  }
  const chain = path.chain.map(principalLabel).join(" → ");
  const entry =
    route.route === "ownership"
      ? "owner rights"
      : `${path.layer === "smb_share" ? "share" : "NTFS"} entry #${path.ace_position} (${relationLabel(path.relation)} ${path.ace_rights.label})`;
  return `${layerLabel(route.layer)}: ${chain} → ${entry}`;
}

function evidenceLine(explanation: AccessExplanationResponse, routes: PrimaryRouteView[]): string[] {
  if (explanation.evidence === undefined) {
    return ["This server did not report provenance for this answer."];
  }
  const byEdge = new Map(explanation.evidence.map((item) => [item.edge_id, item]));
  const lines: string[] = [];
  for (const route of routes) {
    const path = explanation.paths.find((item) => item.id === route.path_id);
    if (path === undefined) {
      continue;
    }
    const hops = path.edges.map((edge) => byEdge.get(edge)).filter((item) => item !== undefined);
    const observed = hops.filter((item) => item.basis === "observed");
    const collectors = [...new Set(observed.map((item) => collectorLabel(item.collector)))];
    const latest = observed
      .map((item) => item.last_observed_at ?? "")
      .sort()
      .at(-1);
    const unobserved = hops.length - observed.length;
    lines.push(
      `${layerLabel(route.layer)}: ${observed.length} of ${hops.length} relationships observed` +
        (collectors.length > 0 ? ` (${collectors.join(", ")})` : "") +
        (latest ? `, last observed ${formatInstant(latest)}` : "") +
        (unobserved > 0 ? `; ${unobserved} not observed (assumed or projected)` : "") +
        ".",
    );
  }
  return lines;
}

/**
 * The seven answers, in the order an investigator asks them. Every answer is a rendering
 * of a field the engine computed.
 */
export function investigationSummary(
  explanation: AccessExplanationResponse,
  investigation: InvestigationView,
): SummaryAnswer[] {
  const routes = investigation.primary_routes;
  const gaps = openQuestions(investigation);
  const first = investigation.next_steps[0];
  return [
    {
      question: "Is access present?",
      answer: [presenceWording(investigation.presence, explanation).sentence],
    },
    {
      question: "What is the strongest supported path?",
      answer:
        routes.length === 0
          ? ["None: no route delivers a right that survives."]
          : routes.map((route) => routeLine(explanation, route)),
    },
    {
      question: "Is that path direct or inherited?",
      answer:
        routes.length === 0
          ? ["Not applicable: there is no route."]
          : routes.map(
              (route) =>
                `${layerLabel(route.layer)}: ${routeKindLabel(route.route, route.membership_hops)}; ${placementLabel(route.placement, route.layer).toLowerCase()}. ${removalSentence(route)}`,
            ),
    },
    {
      question: "What evidence supports it?",
      answer:
        routes.length === 0
          ? [`The verdict: ${explanation.verdict.reason}`]
          : evidenceLine(explanation, routes),
    },
    {
      question: "What evidence remains incomplete or ambiguous?",
      answer:
        gaps.length === 0
          ? ["Nothing that could change this answer."]
          : gaps.map((item) => {
              const who = aboutLabel(item.about);
              return `${stateLabel(item.state)}${who ? ` — ${who}` : ""}: ${item.message}`;
            }),
    },
    {
      question: "Does the uncertainty affect the final conclusion?",
      answer: [conclusionSentence(investigation, explanation)],
    },
    {
      question: "What should an investigator inspect next?",
      answer:
        first === undefined
          ? ["Nothing further."]
          : [nextStepWording(first, explanation, investigation).text],
    },
  ];
}

/* ---------------------------------------------------------- other routes */

/** Every route that is not a primary one, in the engine's order. */
export function otherRoutes(
  explanation: AccessExplanationResponse,
  investigation: InvestigationView,
): CausalPathView[] {
  const primary = new Set(investigation.primary_routes.map((route) => route.path_id));
  return explanation.paths.filter((path) => !primary.has(path.id));
}
