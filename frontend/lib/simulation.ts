/**
 * What a proposal is on the client, and how a report is read.
 *
 * Pure: no fetch, no React, and no import that pins it to the server. Everything here is a
 * value in and a value out, which is what lets the proposal editor, the impact view and the
 * export share one understanding of a simulation and be tested without a browser.
 *
 * Three things live here, and each is here for a reason.
 *
 * **The request shapes.** `lib/contracts.ts` documents what the API *returns*; a proposal is
 * what it is *sent*, and it is constructed in three places — the editor, the seeded link from
 * an ACL row, and the tests. One definition rather than three.
 *
 * **Seeding, as query parameters.** A `Simulate` action from a membership row or an ACE row
 * is a plain link to `/simulations/new?...`, following the same rule the explanation launcher
 * follows: the resulting page is addressable, it can be pasted into a ticket, and it works
 * with no JavaScript in the link. A JSON blob in the URL would be none of those things.
 *
 * **Reading a report.** `impactHeadline` and `orderedDeltas` decide what a page leads with
 * and what it shows first. Deliberately *not* here: any arithmetic about rights. A browser
 * that intersected two masks would be a second implementation of the access check, and the
 * first time it disagreed with the engine nobody would know which was right. Every number on
 * the screen comes from the API.
 */

import type {
  SimulationCaveatView,
  SimulationDeltaView,
  SimulationReportView,
  SimulationSummaryView,
} from "@/lib/contracts";

// ------------------------------------------------------------------- the proposal

/** The nine things a proposal can say. The backend owns this vocabulary. */
export type SimulationChangeKind =
  | "add_member"
  | "remove_member"
  | "add_ntfs_ace"
  | "modify_ntfs_ace"
  | "remove_ntfs_ace"
  | "add_share_ace"
  | "modify_share_ace"
  | "remove_share_ace"
  | "set_inheritance";

export interface MembershipChangeBody {
  kind: "add_member" | "remove_member";
  group_key: string;
  member_key: string;
  edge_kind?: string;
  member_kind?: string | null;
}

export interface NtfsAceChangeBody {
  kind: "add_ntfs_ace" | "modify_ntfs_ace" | "remove_ntfs_ace";
  resource_key: string;
  ace_key?: string | null;
  trustee_sid?: string | null;
  ace_type?: "allow" | "deny" | null;
  access_mask?: number | null;
  ace_flags?: number | null;
  order_index?: number | null;
}

export interface ShareAceChangeBody {
  kind: "add_share_ace" | "modify_share_ace" | "remove_share_ace";
  share_key: string;
  ace_key?: string | null;
  trustee_sid?: string | null;
  ace_type?: "allow" | "deny" | null;
  access_mask?: number | null;
  permission?: "read" | "change" | "full" | null;
  order_index?: number | null;
}

export interface InheritanceChangeBody {
  kind: "set_inheritance";
  resource_key: string;
  protected: boolean;
  inherited_entries?: "convert_to_explicit" | "remove" | null;
}

export type SimulationChangeBody =
  | MembershipChangeBody
  | NtfsAceChangeBody
  | ShareAceChangeBody
  | InheritanceChangeBody;

export interface SimulationRequestBody {
  changes: SimulationChangeBody[];
  scope?: {
    kind?: "affected" | "pair" | "resource" | "subject";
    subject_key?: string | null;
    resource_key?: string | null;
    path?: "remote_smb" | "local";
    limit?: number;
  };
  bounds?: {
    max_principals?: number;
    max_resources?: number;
    max_pairs?: number;
    max_explanations?: number;
    time_budget_ms?: number;
  };
}

/** Where the proposal editor lives. One constant, so a seeded link cannot drift from it. */
export const SIMULATE_PATH = "/simulations/new";

/**
 * The link a `Simulate` action points at, seeded with one change.
 *
 * Discrete parameters rather than an encoded document, so the URL says what it will propose
 * and somebody can edit it by hand. Only the fields a seed needs travel; everything else the
 * editor fills in, and the editor validates nothing — the API's own domain constructors do,
 * and a second copy of those rules in a browser is a second copy that can be more lenient.
 */
export function simulateHref(seed: SimulationSeed): string {
  const params = new URLSearchParams();
  params.set("kind", seed.kind);
  for (const [key, value] of Object.entries(seed)) {
    if (key === "kind" || value === undefined || value === null || value === "") {
      continue;
    }
    params.set(key, typeof value === "number" ? String(value) : String(value));
  }
  return `${SIMULATE_PATH}?${params.toString()}`;
}

export interface SimulationSeed {
  kind: SimulationChangeKind;
  group_key?: string;
  member_key?: string;
  edge_kind?: string;
  resource_key?: string;
  share_key?: string;
  ace_key?: string;
  trustee_sid?: string;
  ace_type?: string;
  access_mask?: number;
  ace_flags?: number;
  permission?: string;
  /** Carried through so the editor can run a `pair` scope about the person who was on screen. */
  subject_key?: string;
}

/** A seed read back off the query string. Unknown kinds produce null rather than a guess. */
export function seedFromQuery(
  params: Record<string, string | string[] | undefined>,
): SimulationSeed | null {
  const kind = single(params.kind);
  if (kind === undefined || !CHANGE_KINDS.includes(kind as SimulationChangeKind)) {
    return null;
  }
  const seed: SimulationSeed = { kind: kind as SimulationChangeKind };
  for (const field of [
    "group_key",
    "member_key",
    "edge_kind",
    "resource_key",
    "share_key",
    "ace_key",
    "trustee_sid",
    "ace_type",
    "permission",
    "subject_key",
  ] as const) {
    const value = single(params[field]);
    if (value !== undefined && value !== "") {
      seed[field] = value;
    }
  }
  for (const field of ["access_mask", "ace_flags"] as const) {
    const value = single(params[field]);
    if (value !== undefined && value !== "" && Number.isFinite(Number(value))) {
      seed[field] = Number(value);
    }
  }
  return seed;
}

export const CHANGE_KINDS: readonly SimulationChangeKind[] = [
  "add_member",
  "remove_member",
  "add_ntfs_ace",
  "modify_ntfs_ace",
  "remove_ntfs_ace",
  "add_share_ace",
  "modify_share_ace",
  "remove_share_ace",
  "set_inheritance",
] as const;

/**
 * The change body a seed describes, ready to post.
 *
 * Fields the seed did not carry are simply absent, not defaulted to something plausible. An
 * editor that filled in a mask nobody typed would be proposing a change the operator did not
 * write, and the API would accept it.
 */
export function changeFromSeed(seed: SimulationSeed): SimulationChangeBody | null {
  switch (seed.kind) {
    case "add_member":
    case "remove_member":
      if (!seed.group_key || !seed.member_key) {
        return null;
      }
      return {
        kind: seed.kind,
        group_key: seed.group_key,
        member_key: seed.member_key,
        ...(seed.edge_kind ? { edge_kind: seed.edge_kind } : {}),
      };
    case "add_ntfs_ace":
    case "modify_ntfs_ace":
    case "remove_ntfs_ace":
      if (!seed.resource_key) {
        return null;
      }
      return {
        kind: seed.kind,
        resource_key: seed.resource_key,
        ...(seed.ace_key ? { ace_key: seed.ace_key } : {}),
        ...(seed.trustee_sid ? { trustee_sid: seed.trustee_sid } : {}),
        ...(seed.ace_type === "allow" || seed.ace_type === "deny"
          ? { ace_type: seed.ace_type }
          : {}),
        ...(seed.access_mask === undefined ? {} : { access_mask: seed.access_mask }),
        ...(seed.ace_flags === undefined ? {} : { ace_flags: seed.ace_flags }),
      };
    case "add_share_ace":
    case "modify_share_ace":
    case "remove_share_ace":
      if (!seed.share_key) {
        return null;
      }
      return {
        kind: seed.kind,
        share_key: seed.share_key,
        ...(seed.ace_key ? { ace_key: seed.ace_key } : {}),
        ...(seed.trustee_sid ? { trustee_sid: seed.trustee_sid } : {}),
        ...(seed.ace_type === "allow" || seed.ace_type === "deny"
          ? { ace_type: seed.ace_type }
          : {}),
        ...(seed.permission === "read" || seed.permission === "change" || seed.permission === "full"
          ? { permission: seed.permission }
          : {}),
      };
    case "set_inheritance":
      if (!seed.resource_key) {
        return null;
      }
      return { kind: "set_inheritance", resource_key: seed.resource_key, protected: true };
    default:
      return null;
  }
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

// --------------------------------------------------------------- reading a report

/** The caveat that must never be rendered as a small grey icon. */
export const LOSS_MAY_NOT_HOLD = "loss_may_not_hold";
export const ALTERNATE_PATH_RETAINED = "alternate_path_retains_access";

/** Caveats that qualify a *claim*, as opposed to describing the estate's coverage. */
const CLAIM_CAVEATS = new Set([LOSS_MAY_NOT_HOLD, "gain_may_not_hold"]);

export type ImpactTone = "loss" | "gain" | "mixed" | "none" | "inert";

export interface ImpactHeadline {
  tone: ImpactTone;
  headline: string;
  detail: string;
}

/**
 * What the impact panel leads with.
 *
 * `inert` is a separate tone from `none` on purpose, and it is the distinction the whole
 * applicability model exists for: a proposal that changes nothing because it is safe and a
 * proposal that changes nothing because its targets have gone produce the same empty list
 * and are completely different advice.
 */
export function impactHeadline(report: SimulationReportView): ImpactHeadline {
  const { summary } = report;
  if (report.inert) {
    return {
      tone: "inert",
      headline: "Nothing in this proposal applies.",
      detail:
        "Every change either is already in place or names something that is no longer there. " +
        "This is not the same as a change that is safe to make — see what became of each one.",
    };
  }
  const losing = summary.lost_access + summary.reduced;
  const gaining = summary.gained_access + summary.expanded;
  if (losing === 0 && gaining === 0 && summary.changed === 0) {
    return {
      tone: "none",
      headline: "This proposal changes nobody's access.",
      detail:
        summary.alternate_paths_retained > 0
          ? `Every principal it touches reaches the same directories another way. ` +
            `${summary.alternate_paths_retained} alternate ${plural(summary.alternate_paths_retained, "route", "routes")} ` +
            `${summary.alternate_paths_retained === 1 ? "survives" : "survive"} the change.`
          : "It was applied to the baseline and no evaluated pair moved.",
    };
  }
  if (losing > 0 && gaining === 0) {
    return {
      tone: "loss",
      headline: `${describeCount(summary.principals_losing.length, "principal")} would lose access or rights.`,
      detail: `${summary.lost_access} would hold nothing at all afterwards; ${summary.reduced} would hold less.`,
    };
  }
  if (gaining > 0 && losing === 0) {
    return {
      tone: "gain",
      headline: `${describeCount(summary.principals_gaining.length, "principal")} would gain access or rights.`,
      detail: `${summary.gained_access} would reach something they cannot reach today; ${summary.expanded} would hold more.`,
    };
  }
  return {
    tone: "mixed",
    headline: `${summary.principals_affected} ${plural(summary.principals_affected, "principal", "principals")} would be affected, in both directions.`,
    detail: `${gaining} gained, ${losing} lost, ${summary.changed} rewritten.`,
  };
}

/**
 * Deltas in the order a reader needs them: what changed first, and losses before gains.
 *
 * Unchanged pairs are kept rather than filtered. They are the evidence that the simulation
 * looked at somebody and found nothing, which is the difference between "Alice is fine" and
 * "Alice was never evaluated" — and the second is what an operator would infer from a list
 * that silently omitted her.
 */
export function orderedDeltas(deltas: readonly SimulationDeltaView[]): SimulationDeltaView[] {
  const rank = (delta: SimulationDeltaView): number => {
    switch (delta.direction) {
      case "lost_access":
        return 0;
      case "reduced":
        return 1;
      case "changed":
        return 2;
      case "gained_access":
        return 3;
      case "expanded":
        return 4;
      default:
        return 5;
    }
  };
  return [...deltas].sort(
    (left, right) =>
      rank(left) - rank(right) ||
      left.subject.key.localeCompare(right.subject.key) ||
      left.resource.resource_key.localeCompare(right.resource.resource_key),
  );
}

/** Whether anything on this report qualifies a claim rather than merely describing coverage. */
export function claimCaveats(report: SimulationReportView): SimulationCaveatView[] {
  const seen = new Map<string, SimulationCaveatView>();
  for (const delta of report.deltas) {
    for (const caveat of delta.caveats) {
      if (CLAIM_CAVEATS.has(caveat.code) && !seen.has(caveat.code)) {
        seen.set(caveat.code, caveat);
      }
    }
  }
  return [...seen.values()];
}

/** Resources that are both affected and marked sensitive or watched. */
export function flaggedResources(report: SimulationReportView): {
  sensitive: string[];
  watched: string[] | null;
} {
  return {
    sensitive: report.summary.sensitive_resources_affected,
    watched: report.summary.watched_resources_affected,
  };
}

// ------------------------------------------------------------------- the export

/**
 * The report as text, for a change ticket.
 *
 * Every caveat travels with it. An export that carried the conclusion and left the
 * qualification behind would be worse than no export: the sentence somebody pastes into a
 * ticket is the sentence the change gets signed off against.
 */
export function reportText(report: SimulationReportView, name?: string): string {
  const lines: string[] = [];
  lines.push(report.notice);
  lines.push("");
  if (name) {
    lines.push(`Proposal: ${name}`);
  }
  lines.push(`Proposal digest: ${report.overlay_hash}`);
  lines.push(
    `Measured against collection state ${report.baseline.token}` +
      (report.baseline.stale ? " (STALE: the estate has moved since)" : ""),
  );
  lines.push("");
  lines.push("What was proposed");
  for (const application of report.applications) {
    lines.push(`  - ${application.change.description}`);
    lines.push(`      ${application.outcome}: ${application.outcome_description}`);
  }
  lines.push("");
  const headline = impactHeadline(report);
  lines.push("Impact");
  lines.push(`  ${headline.headline}`);
  lines.push(`  ${headline.detail}`);
  lines.push(
    `  ${report.summary.evaluated} pairs evaluated; ${report.summary.resources_affected.length} directories affected.`,
  );
  if (report.summary.sensitive_resources_affected.length > 0) {
    lines.push(
      `  Sensitive directories affected: ${report.summary.sensitive_resources_affected.join(", ")}`,
    );
  }
  if (report.summary.watched_resources_affected?.length) {
    lines.push(
      `  Watched directories affected: ${report.summary.watched_resources_affected.join(", ")}`,
    );
  }
  lines.push("");
  lines.push("Per principal");
  for (const delta of orderedDeltas(report.deltas)) {
    lines.push(
      `  - ${delta.subject.display_name ?? delta.subject.key} on ${delta.resource.resource_key}: ` +
        `${delta.direction} (${delta.rights_before.mask} -> ${delta.rights_after.mask})`,
    );
    for (const caveat of delta.caveats) {
      lines.push(`      ! ${caveat.code}: ${caveat.description}`);
    }
    for (const route of delta.retained_routes) {
      lines.push(
        `      route retained: ${route.chain.map((item) => item.display_name ?? item.key).join(" -> ")} ` +
          `(${route.layer}, ${route.rights.mask})`,
      );
    }
  }
  if (!report.complete) {
    lines.push("");
    lines.push("INCOMPLETE — this is part of the answer, not the whole of it:");
    for (const reason of report.truncation) {
      lines.push(`  - ${reason.code}: ${reason.description}`);
    }
  }
  return lines.join("\n");
}

/** The report as JSON: the API's own response body, verbatim. */
export function reportJson(report: SimulationReportView): string {
  return JSON.stringify(report, null, 2);
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

function describeCount(count: number, noun: string): string {
  return `${count} ${plural(count, noun, `${noun}s`)}`;
}

/** What the summary says in one line, for a listing row. */
export function summaryLine(summary: SimulationSummaryView): string {
  if (summary.evaluated === 0) {
    return "No pairs evaluated.";
  }
  const parts: string[] = [];
  if (summary.lost_access) parts.push(`${summary.lost_access} lose access`);
  if (summary.reduced) parts.push(`${summary.reduced} reduced`);
  if (summary.gained_access) parts.push(`${summary.gained_access} gain access`);
  if (summary.expanded) parts.push(`${summary.expanded} expanded`);
  if (summary.changed) parts.push(`${summary.changed} rewritten`);
  if (parts.length === 0) {
    return `${summary.evaluated} evaluated, none changed.`;
  }
  return `${parts.join(", ")} (of ${summary.evaluated} evaluated).`;
}

// --------------------------------------------------- the stored, compact report

/**
 * The shape of the report document persisted beside a proposal.
 *
 * Deliberately **not** the same shape as `SimulationReportView`. What is stored is the
 * engine's own compact document — masks as hex, caveats as codes, principals as storage keys
 * — because the derivation is reproducible exactly from the overlay, the baseline token and
 * the scope, and a stored copy of the rendered version would be a second account of one
 * answer, ageing independently of the code that computes it.
 *
 * The consequence for a page is honest and worth stating on screen: a stored result names
 * principals by key, because the names were never part of the answer. Re-running the proposal
 * produces the rendered form.
 */
export interface StoredReportDelta {
  subject_key: string;
  resource_key: string;
  share_key: string | null;
  path: string;
  direction: string;
  rights_before: string;
  rights_after: string;
  rights_added: string;
  rights_removed: string;
  certainty_before: string;
  certainty_after: string;
  limiting_layer_after: string;
  caveats: string[];
  retained_path_count: number;
}

export interface StoredReportDocument {
  summary?: SimulationSummaryView;
  deltas?: StoredReportDelta[];
  applications?: { outcome: string; detail: Record<string, string> }[];
  truncation?: string[];
  complete?: boolean;
  inert?: boolean;
  cost?: { pairs_evaluated: number; elapsed_ms: number };
}

/**
 * Read a stored report document, defensively.
 *
 * It arrives as `Record<string, unknown>` because it is JSONB written by a possibly older
 * build. A missing field is rendered as missing rather than as zero: "this result recorded no
 * count" and "this result counted nothing" are different statements, and the second is the
 * dangerous one to invent.
 */
export function readStoredReport(document: Record<string, unknown>): StoredReportDocument {
  return document as StoredReportDocument;
}
