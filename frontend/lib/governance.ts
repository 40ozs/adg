/**
 * Turning a review into words, without re-deciding any of it.
 *
 * Everything a reviewer acts on — whether a grant drifted, whether removing an entry would
 * end somebody's access, whether a reviewer is overdue, what a rights mask means — is
 * decided by the backend and carried in the response. This module chooses wording, ordering
 * and tone. It never compares two masks, never computes a verdict, and never rewrites a
 * sentence the API sent: `drift.summary` is rendered as it arrives, because the distinction
 * it draws between "it was removed" and "nobody has looked" is exactly the one a second
 * implementation would blur.
 *
 * Three renderings here would make the page say something false if they were wrong.
 *
 * 1. **"Removed" is never rendered as "already fixed."** A removal is a measured absence —
 *    ADG read the target and found no such entry — and it may be a carried-out revocation
 *    or somebody deleting the wrong ACE. The wording names neither.
 * 2. **`removing_reviewed_entries_leaves_access === null` is not "no".** Null means no
 *    explanation could be produced. "Removing this ends their access" is the reassuring
 *    answer and is only ever shown when it was measured.
 * 3. **A selection this application thinks is not bulk-eligible is still refused by the
 *    server.** `bulkEligibility` exists so a reviewer is told why a button is disabled, not
 *    so the rule lives here; the API enforces it and returns 422 with its own message.
 */

import type {
  CampaignStatusResponse,
  DecisionKind,
  DriftView,
  ItemView,
  QueueEntryView,
  ReachView,
  ReviewerProgressView,
} from "@/lib/contracts";

/* ------------------------------------------------------------------------- decisions */

export interface DecisionOption {
  value: DecisionKind;
  /** What a reviewer is actually saying. Plain words, not the enum member. */
  label: string;
  /** One sentence: what this answer commits ADG, and the reviewer, to. */
  meaning: string;
  /** Whether the API will insist on a rationale whatever the campaign says. */
  alwaysNeedsComment: boolean;
}

/**
 * The five answers, in the order a reviewer considers them.
 *
 * `certify` first because it is the expected one; `revoke` second because it is the one with
 * consequences; the three that mean "not yet" last. `abstain` and `investigate` are
 * deliberately separate: an abstention says the wrong person was asked, and the fix is to
 * reassign; "needs investigation" says the right person was asked and cannot answer yet, and
 * the fix is somebody looking into it. Folded together, a queue of items awaiting follow-up
 * would be indistinguishable from a queue of misrouted ones.
 */
export const DECISION_OPTIONS: readonly DecisionOption[] = [
  {
    value: "certify",
    label: "Approve",
    meaning: "This access was appropriate at the baseline and should remain.",
    alwaysNeedsComment: false,
  },
  {
    value: "revoke",
    label: "Propose revoke",
    meaning:
      "This access should be removed. ADG records the proposal and does not perform it — " +
      "nothing changes in Windows because of this decision.",
    alwaysNeedsComment: true,
  },
  {
    value: "modify",
    label: "Propose narrower rights",
    meaning: "The principal should keep access at different rights. Say which in the comment.",
    alwaysNeedsComment: true,
  },
  {
    value: "investigate",
    label: "Needs investigation",
    meaning:
      "Something here is wrong and you cannot yet say what should happen to it. Say what " +
      "needs looking into.",
    alwaysNeedsComment: true,
  },
  {
    value: "abstain",
    label: "Not mine to judge",
    meaning:
      "You are not the right person to answer this one. Say who should have been asked; " +
      "the campaign owner reassigns it.",
    alwaysNeedsComment: true,
  },
] as const;

export function decisionOption(value: string): DecisionOption | null {
  return DECISION_OPTIONS.find((option) => option.value === value) ?? null;
}

/** The decision's plain-words label, or the raw value for one this build does not know. */
export function decisionLabel(value: string): string {
  return decisionOption(value)?.label ?? value;
}

/**
 * Whether a comment is required, given the campaign's setting.
 *
 * A mirror of the server's rule for the sake of telling the reviewer *before* they submit,
 * and it can only ever be at least as strict — `standard` is the floor the database itself
 * enforces, so there is no setting under which this returns false and the API accepts a
 * blank one for anything but `certify`.
 */
export function commentRequired(decision: string, requirement: string): boolean {
  if (requirement === "always") {
    return true;
  }
  return decisionOption(decision)?.alwaysNeedsComment ?? true;
}

/* ----------------------------------------------------------------------------- drift */

export type Tone = "ok" | "warn" | "bad" | "info";

/**
 * How a drift verdict should read.
 *
 * `unobserved` is `info`, not `warn`: it is the absence of an answer rather than bad news,
 * and colouring it as a problem would train reviewers to ignore the colour. `removed` is
 * `warn` rather than `ok` for the opposite reason — it is *often* a carried-out revocation,
 * and rendering it as good news would invite a reviewer to close an item on the strength of
 * a cause ADG never established.
 */
export function driftTone(verdict: string): Tone {
  switch (verdict) {
    case "modified":
      return "bad";
    case "removed":
      return "warn";
    case "unobserved":
      return "info";
    default:
      return "ok";
  }
}

export function driftHeadline(drift: DriftView): string {
  switch (drift.verdict) {
    case "modified":
      return "This grant has changed since the campaign was frozen";
    case "removed":
      return drift.target_present === false
        ? "The target no longer exists"
        : "This grant no longer exists";
    case "unobserved":
      return "ADG cannot say whether this still stands";
    default:
      return drift.evidence_reissued
        ? "Unchanged, though the entries were rewritten"
        : "Unchanged since the campaign was frozen";
  }
}

/** Whether the campaign-level drift banner should be shown at all. */
export function driftIsWorthShowing(counts: {
  modified: number;
  removed: number;
}): boolean {
  return counts.modified > 0 || counts.removed > 0;
}

/* ----------------------------------------------------------------------------- reach */

export interface ReachVerdict {
  tone: Tone;
  headline: string;
  detail: string;
}

/**
 * What to tell a reviewer about whether revoking this entry would achieve anything.
 *
 * The most consequential sentence on the review screen. A `revoke` recorded in the belief
 * that access ends, on a grant the principal also holds through a group, is an attestation
 * that says something untrue — so the case where it *would* be untrue is the loud one, and
 * the case where ADG does not know is never rendered as the reassuring answer.
 */
export function reachVerdict(reach: ReachView): ReachVerdict {
  if (!reach.available) {
    return {
      tone: "info",
      headline: "ADG cannot explain this access",
      detail:
        reach.unavailable_reason ??
        "No explanation could be produced for this pair, so what a revocation would achieve is unknown.",
    };
  }
  if (reach.removing_reviewed_entries_leaves_access === null) {
    return {
      tone: "info",
      headline: "What a revocation would achieve is not established",
      detail:
        "ADG could not attribute any current access to these entries, so it cannot say what " +
        "removing them would do. Treat a revocation here as unverified.",
    };
  }
  if (reach.removing_reviewed_entries_leaves_access) {
    return {
      tone: "bad",
      headline: "Removing this entry would not end their access",
      detail:
        `They also reach this target by ${countNoun(reach.group_paths, "other path", "other paths")}. ` +
        "Revoking here records a decision about this entry; the access continues through the " +
        "routes below, and removing it means changing those too.",
    };
  }
  return {
    tone: "ok",
    headline: "Removing this entry would end their access",
    detail:
      "ADG found no other route from this principal to this target. A revoke proposal here " +
      "describes a change that would actually take the access away.",
  };
}

function countNoun(value: number, singular: string, plural: string): string {
  return `${value} ${value === 1 ? singular : plural}`;
}

/* --------------------------------------------------------------------- bulk selection */

export interface BulkEligibility {
  eligible: boolean;
  /** Empty when eligible. Written for the reviewer, naming the rule that fails. */
  reason: string;
}

/**
 * Whether this application can already tell the API will refuse a selection.
 *
 * A courtesy, not a control — the rules live in `app/governance/service.py` and the API
 * returns 422 with its own message. Reproduced here only so a disabled button can say why,
 * and kept to exactly the rules a client can evaluate from the item rows it already has.
 * Drift and assignment are deliberately **not** checked here: the first needs a live
 * comparison and the second needs the database, and guessing at either would produce a
 * disabled button with a wrong explanation.
 */
export function bulkEligibility(items: readonly ItemView[]): BulkEligibility {
  if (items.length === 0) {
    return { eligible: false, reason: "Select some items first." };
  }
  const decided = items.filter((item) => item.status === "decided");
  if (decided.length > 0) {
    return {
      eligible: false,
      reason:
        `${countNoun(decided.length, "item", "items")} already carry a decision. Changing an ` +
        "answer supersedes a specific attestation and needs its own reason, so it is done one " +
        "at a time.",
    };
  }
  const kinds = new Set(items.map((item) => item.target_kind));
  if (kinds.size > 1) {
    return {
      eligible: false,
      reason:
        "These sit on different kinds of access-control list. Share permissions and " +
        "file-system permissions are removed in different places, often by different people.",
    };
  }
  const principals = new Set(items.map((item) => item.principal_key));
  const targets = new Set(items.map((item) => item.target_key));
  if (principals.size > 1 && targets.size > 1) {
    return {
      eligible: false,
      reason:
        `This covers ${principals.size} principals across ${targets.size} targets, which is ` +
        "not one question. Select one principal across many targets, or one target across " +
        "many principals.",
    };
  }
  return { eligible: true, reason: "" };
}

/**
 * How to describe what a batch is about, once it is eligible.
 *
 * Shown on the confirmation so that "you are about to certify 11 grants" is never the whole
 * of what a reviewer is told before they do it.
 */
export function bulkSubject(items: readonly ItemView[]): string {
  if (items.length === 0) {
    return "nothing";
  }
  const principals = new Set(items.map((item) => item.principal_key));
  const targets = new Set(items.map((item) => item.target_key));
  const first = items[0];
  if (principals.size === 1 && targets.size === 1) {
    return `${principalLabel(first)} on ${targetLabel(first)}`;
  }
  if (principals.size === 1) {
    return `${principalLabel(first)} on ${countNoun(targets.size, "target", "targets")}`;
  }
  return `${countNoun(principals.size, "principal", "principals")} on ${targetLabel(first)}`;
}

export function principalLabel(item: ItemView): string {
  return item.principal_display_name ?? item.principal_sid;
}

export function targetLabel(item: ItemView): string {
  return item.target_path ?? item.target_key;
}

/* -------------------------------------------------------------------------- progress */

/**
 * Reviewers most in need of attention first: overdue, then least far through.
 *
 * Sorted for display only. Every number in the row is the backend's, including `overdue`
 * itself — a client that recomputed it from `due_at` would disagree with the server the
 * moment somebody's assignment carried a different deadline from the campaign's.
 */
export function reviewersByAttention(
  reviewers: readonly ReviewerProgressView[],
): ReviewerProgressView[] {
  return [...reviewers].sort((left, right) => {
    if (left.overdue !== right.overdue) {
      return left.overdue ? -1 : 1;
    }
    if (left.completion !== right.completion) {
      return left.completion - right.completion;
    }
    return left.reviewer_subject.localeCompare(right.reviewer_subject);
  });
}

/**
 * The one line that must appear beside any "n of n certified" claim.
 *
 * A campaign that excluded inherited entries and built-in trustees reviewed less than the
 * estate holds, and a reader who is not told that will read full completion as coverage it
 * is not. Returns null only when nothing was excluded.
 */
export function coverageCaveat(excluded: Record<string, number>): string | null {
  const entries = Object.entries(excluded).filter(([, count]) => count > 0);
  if (entries.length === 0) {
    return null;
  }
  const parts = entries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([reason, count]) => `${count.toLocaleString()} ${exclusionWording(reason)}`);
  return `This campaign did not ask about ${joinWords(parts)}. Completion below is of what it asked, not of the estate.`;
}

function exclusionWording(reason: string): string {
  switch (reason) {
    case "inherited":
      return "inherited entries";
    case "builtin_trustee":
      return "entries naming well-known trustees";
    case "deny":
      return "deny entries";
    default:
      return `entries excluded as ${reason}`;
  }
}

function joinWords(parts: readonly string[]): string {
  if (parts.length <= 1) {
    return parts[0] ?? "";
  }
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** Whether a campaign's status page should lead with "this campaign is stuck". */
export function isStuck(status: CampaignStatusResponse): boolean {
  return status.unassigned_items > 0;
}

/* ----------------------------------------------------------------------------- queue */

export function queueHeadline(entry: QueueEntryView): string {
  if (entry.overdue) {
    return `Overdue — ${countNoun(entry.pending, "item", "items")} left`;
  }
  if (entry.pending === 0) {
    return "Nothing left to answer";
  }
  return `${countNoun(entry.pending, "item", "items")} to answer`;
}

/** A due date in words. Absolute, never "in 3 days": a deadline is a date somebody agreed. */
export function dueWording(due: string | null, overdue: boolean): string {
  if (due === null) {
    return "No deadline";
  }
  const rendered = new Date(due).toISOString().slice(0, 10);
  return overdue ? `Was due ${rendered}` : `Due ${rendered}`;
}
