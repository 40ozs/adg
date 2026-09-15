/**
 * Turning a change into words, without re-deriving any of it.
 *
 * Every conclusion on the Changes page — the action, the significance, the direction, the
 * severity, the sentence explaining why — is computed by the backend and carried in the
 * response. This module chooses *wording* and *ordering*. It never decides whether a change
 * matters, and it never compares two masks: that is the one rule the derived-response
 * contract states for every client, and a change feed is the place it would be most
 * tempting to break.
 *
 * Three renderings here are load-bearing, in the sense that getting them wrong would make
 * the page say something false while looking fine:
 *
 * 1. **`first_observed` is "first seen", never "added."** It records that ADG had no prior
 *    view of the thing containing the object. An estate's first scan produces one per
 *    object; calling them additions would report the whole estate as having been created on
 *    a Tuesday.
 * 2. **The time shown is the window, not `at`.** `at` is the instant the resulting version
 *    opened — the moment somebody looked. Rendering it as the change time dates every
 *    incident to a scan schedule, which is exactly what ADR-0019 exists to prevent.
 * 3. **A removal is a *measured* absence.** It only exists because a scan that reconciled a
 *    scope looked and did not find the object. The wording says so rather than implying ADG
 *    watched it being deleted.
 */

import type {
  AceEditView,
  ChangeAction,
  ChangeDirection,
  ChangeSeverity,
  ChangeSignificance,
  ChangeSummaryResponse,
  ChangeView,
  ChangeWindowView,
  FieldDeltaView,
  ImpactVerdict,
} from "@/lib/contracts";

/** Least to most severe. The only ordering; alphabetically `critical` sorts below `info`. */
export const SEVERITY_ORDER: readonly ChangeSeverity[] = [
  "info",
  "low",
  "medium",
  "high",
  "critical",
] as const;

export function severityRank(severity: ChangeSeverity): number {
  const index = SEVERITY_ORDER.indexOf(severity);
  return index === -1 ? 0 : index;
}

/** A CSS status class for a severity. Visual only; the value is what a reader branches on. */
export function severityTone(severity: ChangeSeverity): "bad" | "warn" | "ok" {
  if (severity === "critical" || severity === "high") {
    return "bad";
  }
  if (severity === "medium") {
    return "warn";
  }
  return "ok";
}

/**
 * The verb for an action.
 *
 * `first_observed` is the whole point of this function existing. See the module docstring.
 */
export function actionWording(action: ChangeAction): { label: string; explanation: string } {
  switch (action) {
    case "added":
      return {
        label: "Added",
        explanation:
          "This appeared somewhere ADG was already reading, or came back after a scan had " +
          "found it gone. Something that was not there is there now.",
      };
    case "removed":
      return {
        label: "Removed",
        explanation:
          "A scan that reconciled a scope containing this object looked and did not find " +
          "it. A scan that simply did not mention it produces no removal at all.",
      };
    case "modified":
      return {
        label: "Changed",
        explanation: "The object's state changed. Its identity did not.",
      };
    case "first_observed":
      return {
        label: "First seen",
        explanation:
          "ADG had no prior view of what contains this object, so this is where the record " +
          "begins — not evidence that anything was created. Whatever it grants may have " +
          "been in place for years.",
      };
    default:
      return { label: action, explanation: "" };
  }
}

export function directionWording(direction: ChangeDirection): string {
  switch (direction) {
    case "broadened":
      return "Broadened";
    case "narrowed":
      return "Narrowed";
    case "mixed":
      return "Both ways";
    case "neutral":
      return "No change to access";
    case "undetermined":
      return "Direction unknown";
    default:
      return direction;
  }
}

export function significanceWording(significance: ChangeSignificance): string {
  switch (significance) {
    case "security":
      return "Permissions";
    case "metadata":
      return "Description";
    case "noise":
      return "No difference";
    case "undetermined":
      return "Unclassified";
    default:
      return significance;
  }
}

/** Human names for the seven object kinds. The wire value stays the thing to branch on. */
export function kindWording(kind: string): string {
  const names: Record<string, string> = {
    principal: "Identity",
    membership_edge: "Group membership",
    server: "Server",
    smb_share: "Share",
    smb_ace: "Share permission",
    ntfs_resource: "Directory",
    ntfs_ace: "File-system permission",
  };
  return names[kind] ?? kind;
}

/**
 * When a change happened, as a sentence that carries both ends.
 *
 * Never a single instant. When the two observations bracket the change exactly the window
 * collapses to a point and the wording says so; otherwise it names the interval, because
 * that is the whole of what is known.
 */
export function windowWording(window: ChangeWindowView | null): string {
  if (window === null) {
    return "No earlier observation bounds this, so when it happened is unknown.";
  }
  if (window.is_exact) {
    return `Between two observations that bracket it exactly: ${window.at_or_before}.`;
  }
  return `Some time after ${window.after} and by ${window.at_or_before}.`;
}

/** How wide the uncertainty is, in the largest unit that reads sensibly. */
export function windowSpan(window: ChangeWindowView | null): string {
  if (window === null) {
    return "unknown";
  }
  const seconds = window.duration_seconds;
  if (seconds <= 0) {
    return "exact";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  if (seconds < 86_400) {
    return `${Math.round(seconds / 3600)}h`;
  }
  return `${Math.round(seconds / 86_400)}d`;
}

/**
 * A one-line headline for a change, preferring the edit it belongs to.
 *
 * An ACL entry that was rewritten is stored as a removal and an addition, and the two rows
 * on their own read as unrelated events. When the response paired them, the pair's summary
 * is the true sentence and is what the list shows.
 */
export function headline(change: ChangeView, edits: readonly AceEditView[]): string {
  const edit = editFor(change, edits);
  if (edit !== null) {
    return edit.summary;
  }
  return change.reasons[0] ?? `${actionWording(change.action).label} ${change.key}`;
}

export function editFor(
  change: ChangeView,
  edits: readonly AceEditView[],
): AceEditView | null {
  if (change.edit === null) {
    return null;
  }
  return edits.find((edit) => edit.index === change.edit) ?? null;
}

/**
 * Group a page so that the two halves of one ACL edit sit together.
 *
 * Order is preserved otherwise: the response is newest first and this never re-sorts it,
 * because a list that silently reorders itself makes "what happened most recently" a
 * question the reader cannot answer by looking at the top.
 */
export interface ChangeGroup {
  /** The edit these changes belong to, or null for a change that stands alone. */
  edit: AceEditView | null;
  changes: ChangeView[];
}

export function groupChanges(
  changes: readonly ChangeView[],
  edits: readonly AceEditView[],
): ChangeGroup[] {
  const groups: ChangeGroup[] = [];
  const byEdit = new Map<number, ChangeGroup>();
  for (const change of changes) {
    const edit = editFor(change, edits);
    if (edit === null) {
      groups.push({ edit: null, changes: [change] });
      continue;
    }
    const existing = byEdit.get(edit.index);
    if (existing === undefined) {
      const group: ChangeGroup = { edit, changes: [change] };
      byEdit.set(edit.index, group);
      groups.push(group);
      continue;
    }
    existing.changes.push(change);
  }
  return groups;
}

/** Deltas a reader should see, with the ones that carry no information dropped. */
export function visibleDeltas(deltas: readonly FieldDeltaView[]): FieldDeltaView[] {
  return deltas.filter((delta) => delta.significance !== "noise");
}

/** A stored value rendered for a diff cell. `null` is "not recorded", never "false". */
export function renderValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value === "" ? '""' : value;
  }
  return JSON.stringify(value);
}

/**
 * What the summary says about a filtered page.
 *
 * `null` when the filter hid nothing, so a caller renders the banner only when there is
 * something to say. When it hid something, the sentence names the number — a page that is
 * clean because of a default must never be indistinguishable from a quiet week.
 */
export function exclusionNotice(summary: ChangeSummaryResponse): string | null {
  if (summary.excluded <= 0) {
    return null;
  }
  const first = summary.by_action.first_observed ?? 0;
  const sightings =
    first > 0
      ? ` ${first} of them are first sightings, which record that ADG started looking rather than that anything was created.`
      : "";
  return (
    `${summary.excluded} of ${summary.total} changes in this window are not shown, because ` +
    `of the filters in use.${sightings}`
  );
}

/** A banner for a summary that could not count the whole window. */
export function truncationNotice(summary: ChangeSummaryResponse): string | null {
  if (!summary.truncated) {
    return null;
  }
  return (
    "This window holds more changes than the server counts for one summary, so these " +
    "totals are a floor rather than a total. Narrow the window or name a scope."
  );
}

/**
 * What an impact verdict means for the reader.
 *
 * Anything but `resolved` means the engine was not handed a pair to resolve — **not** that
 * nothing happened. Wording them as reassurance would be the worst sentence on the page.
 */
export function impactWording(verdict: ImpactVerdict): { label: string; explanation: string } {
  switch (verdict) {
    case "resolved":
      return {
        label: "Resolved",
        explanation: "Effective access was computed either side of this change.",
      };
    case "needs_a_subject":
      return {
        label: "Needs a principal",
        explanation:
          "This change is about a resource, and effective access is always about somebody " +
          "as well. Name a principal to see what it could do before and after.",
      };
    case "needs_a_resource":
      return {
        label: "Needs a resource",
        explanation:
          "This change is about a principal and names no resource. Name one to resolve " +
          "what that principal could do to it.",
      };
    case "unbounded":
      return {
        label: "Reaches too much to resolve at once",
        explanation:
          "A membership change reaches every resource that group reaches. The groups the " +
          "principal reached either side are shown; name a resource for the rest.",
      };
    case "not_applicable":
      return {
        label: "No access question",
        explanation: "This change is about neither a principal nor a resource.",
      };
    default:
      return { label: verdict, explanation: "" };
  }
}

/**
 * The caveat to show beside an access delta, or null when none applies.
 *
 * `neutral` plus `conclusive: false` is the dangerous combination: it looks like "nothing
 * changed" and means "nothing could be established either side". ADR-0016 forbids the
 * negative reading while `conclusive` is false, so the banner says so explicitly.
 */
export function impactCaveat(direction: ChangeDirection, conclusive: boolean): string | null {
  if (conclusive) {
    return null;
  }
  if (direction === "neutral") {
    return (
      "Neither answer is conclusive, so this is not evidence that access did not change — " +
      "only that nothing could be established either side. Collection is incomplete in a " +
      "direction that could hide a grant."
    );
  }
  return (
    "At least one of these answers is not conclusive: collection is incomplete in a " +
    "direction that could hide a grant, so the difference may be understated."
  );
}

/** A caveat for a change either side of which came from the Phase 7 backfill. */
export function reconstructionCaveat(change: ChangeView): string | null {
  if (!change.reconstructed) {
    return null;
  }
  return (
    "One side of this change was reconstructed when history was introduced, from a record " +
    "that kept no evidence of intermediate states. Anything that happened inside that " +
    "interval is not visible here."
  );
}
