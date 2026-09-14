/**
 * Reading the collector operations report: every decision the status page makes, as pure
 * functions.
 *
 * The page itself renders; it decides nothing. That split matters more here than elsewhere,
 * because this screen is the one an operator consults when something is wrong, and the
 * wording of "wrong" is the product. A sentence that says "failed" where it should say
 * "succeeded, but three days ago" sends somebody to fix a collector that is working.
 *
 * Nothing here re-derives a verdict. `completeness`, `note`, `shortfall`, `stale_success`
 * and `health` all come from the API, which computes them in
 * `app/domain/operations.py`. What this module adds is presentation: tone, ordering,
 * grouping and the two judgements that are genuinely about the screen — which counts are
 * worth calling out as zero, and how to word a timestamp that is old.
 */

import type {
  CollectionOperations,
  CollectorErrorGroup,
  Completeness,
  ObjectCounts,
  RunOutcome,
  ScopeOperations,
} from "@/lib/contracts";

/** How a completeness verdict should read, and how loudly. */
export interface Tone {
  /** The CSS status class, matching the rest of the application. */
  className: "status-ok" | "status-warn" | "status-bad" | "muted";
  label: string;
}

const TONES: Record<Completeness, Tone> = {
  complete: { className: "status-ok", label: "complete" },
  partial: { className: "status-warn", label: "incomplete" },
  none: { className: "status-bad", label: "nothing collected" },
  in_progress: { className: "muted", label: "running" },
};

export function completenessTone(value: Completeness): Tone {
  return TONES[value];
}

/**
 * Timestamps are rendered in UTC, always.
 *
 * A scan run's time is compared against a Windows event log and a collector log, both of
 * which an operator reads in a fixed zone. Rendering in the browser's zone would also make
 * the server and client markup disagree and produce a hydration mismatch.
 */
export function formatUtc(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return `${parsed.toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

/**
 * How old a run is, in words, relative to a reference instant.
 *
 * The reference is a parameter rather than `Date.now()` so that this is testable and so
 * that the page passes one instant to every row: two rows computed from two different
 * "now"s can disagree by a minute and look like a bug.
 *
 * Returns `null` for anything under an hour — an operator does not need to be told that a
 * run from twenty minutes ago is recent, and a line that says so is a line in the way.
 */
export function ageInWords(value: string | null, now: Date): string | null {
  if (value === null) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  const hours = Math.floor((now.getTime() - parsed.getTime()) / 3_600_000);
  if (hours < 1) {
    return null;
  }
  if (hours < 48) {
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/**
 * The sentence under a scope's name.
 *
 * The API's own `note` is used verbatim when it has one — it is the authoritative wording
 * and it already distinguishes "never ran" from "failed, and here is how old your data is".
 * The only case this adds is the healthy one, where the API says nothing because there is
 * nothing wrong, and a blank cell reads as missing rather than fine.
 */
export function scopeSentence(scope: ScopeOperations, now: Date): string {
  if (scope.note !== null) {
    return scope.note;
  }
  const age = ageInWords(scope.latest.completed_at ?? scope.latest.started_at, now);
  const when = age === null ? "just now" : age;
  return `Last read ${when}, with nothing reported missing.`;
}

/** Scope counts by completeness, for the one-line header. */
export interface ScopeTally {
  complete: number;
  partial: number;
  none: number;
  in_progress: number;
  total: number;
  neverSucceeded: number;
}

export function tallyScopes(scopes: readonly ScopeOperations[]): ScopeTally {
  const tally: ScopeTally = {
    complete: 0,
    partial: 0,
    none: 0,
    in_progress: 0,
    total: scopes.length,
    neverSucceeded: 0,
  };
  for (const scope of scopes) {
    tally[scope.completeness] += 1;
    if (!scope.has_ever_succeeded) {
      tally.neverSucceeded += 1;
    }
  }
  return tally;
}

/**
 * The header sentence: how much of the estate is actually readable.
 *
 * Phrased as a fraction of scopes rather than as a health word, because "3 of 8 scopes are
 * incomplete" is actionable and "incomplete" on its own is not.
 */
export function coverageSentence(tally: ScopeTally): string {
  if (tally.total === 0) {
    return "No collector has ever reported. Every list in ADG is empty because nothing ran.";
  }
  if (tally.complete === tally.total) {
    return `All ${tally.total} collection scopes are current and complete.`;
  }
  const parts: string[] = [`${tally.complete} of ${tally.total} scopes are complete`];
  if (tally.partial) {
    parts.push(`${tally.partial} incomplete`);
  }
  if (tally.none) {
    parts.push(`${tally.none} with nothing usable`);
  }
  if (tally.in_progress) {
    parts.push(`${tally.in_progress} still running`);
  }
  const never =
    tally.neverSucceeded > 0
      ? ` ${tally.neverSucceeded} scope(s) have never completed a run at all.`
      : "";
  return `${parts.join(", ")}.${never}`;
}

/** One row of the object-count panel. */
export interface CountRow {
  key: keyof ObjectCounts;
  label: string;
  value: number;
  /** Set when a zero here means a collector did not do its job. */
  concern: string | null;
}

const COUNT_LABELS: ReadonlyArray<{ key: keyof ObjectCounts; label: string; empty: string }> = [
  {
    key: "principals",
    label: "Principals",
    empty: "No identity has been collected, so no ACL entry can be attributed to anybody.",
  },
  {
    key: "membership_edges",
    label: "Memberships",
    empty: "No membership is known, so every group looks empty and access is under-reported.",
  },
  { key: "servers", label: "Servers", empty: "No server has been collected." },
  { key: "shares", label: "Shares", empty: "No share has been collected." },
  {
    key: "share_aces",
    label: "Share ACL entries",
    empty: "No share ACL was read; the share layer cannot limit anything in an answer.",
  },
  {
    key: "directories",
    label: "Directories",
    empty: "No file-system descriptor was read, so no effective access can be computed.",
  },
  {
    key: "ntfs_aces",
    label: "NTFS ACL entries",
    empty: "No NTFS entry was read, so every directory looks like it grants nobody access.",
  },
  { key: "scan_runs", label: "Scan runs", empty: "Nothing has ever run." },
];

/**
 * The counts, with a sentence attached to each zero.
 *
 * A zero is the number this panel exists for. A collector that ran cleanly and wrote no
 * rows reports `succeeded` and looks fine everywhere else in the product; the only place
 * that shows it is a count of what actually landed.
 */
export function countRows(counts: ObjectCounts): CountRow[] {
  return COUNT_LABELS.map(({ key, label, empty }) => ({
    key,
    label,
    value: counts[key],
    concern: counts[key] === 0 ? empty : null,
  }));
}

/** Error groups, with the widespread ones first: they are the ones that are not one object. */
export function orderedErrors(errors: readonly CollectorErrorGroup[]): CollectorErrorGroup[] {
  return [...errors].sort((left, right) => {
    if (left.is_widespread !== right.is_widespread) {
      return left.is_widespread ? -1 : 1;
    }
    if (left.count !== right.count) {
      return right.count - left.count;
    }
    return left.code.localeCompare(right.code);
  });
}

/**
 * What a run delivered against what it claimed, as a short string.
 *
 * Both halves are shown even when they agree. "88" alone invites the reader to assume it is
 * what was asked for; "88 of 88" says so.
 */
export function delivery(run: RunOutcome): string {
  const observations =
    run.observations_reported === null
      ? `${run.observations_applied}`
      : `${run.observations_applied} of ${run.observations_reported}`;
  const batches =
    run.batches_reported === null
      ? `${run.batches_received}`
      : `${run.batches_received} of ${run.batches_reported}`;
  return `${observations} observations in ${batches} batches`;
}

/**
 * Whether this report should be read as an alert rather than as a status.
 *
 * Driven by the API's `health`, not by counting rows here: the coverage banner every other
 * page shows is computed from the same field, and two screens disagreeing about whether the
 * estate is trustworthy is worse than either being wrong.
 */
export function isAlarming(report: CollectionOperations): boolean {
  return report.health === "failed" || report.health === "incomplete";
}
