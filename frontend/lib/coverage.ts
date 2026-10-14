/**
 * Wording for collection coverage, risk-total readings and comparison outcomes (ADG-DEMO-03).
 *
 * Every judgement here was made by the API: the per-scope state, the age of the evidence, the
 * verdict, whether a total is exhaustive, how a comparison ended and what can be done about
 * it. This module only words them, and words every state with a word *and* a symbol beside
 * its color, so nothing depends on color alone.
 *
 * Two rules from the backend are worth repeating where the wording is chosen:
 *
 * - A scope that was **collected and found nothing** is the only "checked, nothing there".
 *   A failed or unavailable scope is "nobody looked", and its words never say "none".
 * - Only a **complete** comparison is an answer. An incomplete one is labelled with how far it
 *   got; a timed-out one presents no list at all.
 */

import type {
  ChangeComparisonResponse,
  ComparisonOutcome,
  ComparisonRecoveryAction,
  CoverageSummary,
  CoverageVerdict,
  EvidenceAge,
  RiskSummaryResponse,
  ScopeCoverage,
  ScopeState,
  TotalsReading,
} from "@/lib/contracts";
import type { BasisView } from "@/lib/derived";
import { formatUtc } from "@/lib/operations";

export type Tone = "ok" | "warn" | "bad" | "info";

/** A state as the page renders it: a word, a symbol, and the tone that colors it. */
export interface StateWords {
  word: string;
  symbol: string;
  tone: Tone;
}

const SCOPE_STATES: Record<ScopeState, StateWords> = {
  collected: { word: "Collected", symbol: "✓", tone: "ok" },
  partial: { word: "Partial", symbol: "◐", tone: "warn" },
  unavailable: { word: "Unavailable", symbol: "⊘", tone: "bad" },
  failed: { word: "Failed", symbol: "✕", tone: "bad" },
  running: { word: "Running", symbol: "…", tone: "warn" },
};

export function scopeStateWords(state: ScopeState): StateWords {
  return SCOPE_STATES[state] ?? { word: state, symbol: "?", tone: "warn" };
}

const VERDICTS: Record<CoverageVerdict, StateWords> = {
  complete: { word: "Complete", symbol: "✓", tone: "ok" },
  partial: { word: "Partial", symbol: "◐", tone: "warn" },
  incomplete: { word: "Incomplete", symbol: "⚠", tone: "bad" },
  none: { word: "Nothing collected", symbol: "∅", tone: "warn" },
};

export function verdictWords(verdict: CoverageVerdict): StateWords {
  return VERDICTS[verdict] ?? { word: verdict, symbol: "?", tone: "warn" };
}

/** What ADG holds for a scope, in words a reviewer can act on. */
export function evidenceWords(scope: Pick<ScopeCoverage, "evidence" | "evidence_collected_at">): string {
  return evidenceAgeWords(scope.evidence, scope.evidence_collected_at);
}

export function evidenceAgeWords(evidence: EvidenceAge, collectedAt: string | null): string {
  switch (evidence) {
    case "current":
      return collectedAt === null ? "Current" : `Current, collected ${formatDay(collectedAt)}`;
    case "stale":
      return `Older evidence, from the scan of ${formatDay(collectedAt)}`;
    case "none":
      return "Nothing collected";
    default:
      return evidence;
  }
}

/** The snapshot a number describes, in one line. */
export function snapshotWords(snapshot: BasisView): string {
  if (snapshot.is_empty) {
    return "No collection on record.";
  }
  return (
    `Collected state ${snapshot.token.slice(0, 12)} · ${snapshot.runs} collector runs · ` +
    `last collector write ${formatUtc(snapshot.latest_activity_at)}`
  );
}

/** The scopes worth naming beside a total: everything that is not collected completely. */
export function incompleteScopes(scopes: readonly ScopeCoverage[]): ScopeCoverage[] {
  return scopes.filter((scope) => scope.state !== "collected");
}

export interface Notice {
  tone: "warning" | "info" | "ok";
  headline: string;
  explanation: string;
}

const READINGS: Record<TotalsReading, { tone: Notice["tone"]; headline: string }> = {
  not_evaluated: { tone: "warning", headline: "The rules have never been evaluated" },
  partial: { tone: "warning", headline: "These counts are not totals" },
  incomplete: { tone: "warning", headline: "These counts are not exhaustive" },
  stale: { tone: "warning", headline: "These counts describe an earlier snapshot" },
  complete: { tone: "ok", headline: "These counts are totals" },
};

/**
 * How the counts on the risk page may be read, from the API's interpretation.
 *
 * `null` when the server is older than ADG-DEMO-03 and sent none; the page then falls back to
 * the evaluation-only notice it always had.
 */
export function totalsNotice(summary: RiskSummaryResponse): Notice | null {
  const interpretation = summary.interpretation ?? null;
  if (interpretation === null) {
    return null;
  }
  const reading = READINGS[interpretation.reading] ?? READINGS.incomplete;
  const explanation =
    interpretation.reading === "complete"
      ? "A full evaluation read every scope ADG holds, every scope's latest scan was complete, " +
        "and collection has not moved since: these counts are totals for the snapshot below."
      : interpretation.reasons.join(" ");
  return { tone: reading.tone, headline: reading.headline, explanation };
}

/** Whether a reading may be presented as a total. Only `complete` may. */
export function isExhaustive(summary: RiskSummaryResponse): boolean {
  return summary.interpretation?.exhaustive === true;
}

/** The one line that sits beside the severity counts. */
export function coverageLine(collection: CoverageSummary): string {
  return `Coverage: ${collection.headline}`;
}

// ------------------------------------------------------------------ comparisons

/**
 * How a comparison ended. A server older than ADG-DEMO-03 sends only `truncated`, which is
 * then the only signal, and a truncated answer is never read as complete.
 */
export function comparisonOutcome(comparison: ChangeComparisonResponse): ComparisonOutcome {
  if (comparison.outcome !== undefined) {
    return comparison.outcome;
  }
  return comparison.truncated ? "incomplete" : "complete";
}

const OUTCOMES: Record<ComparisonOutcome, StateWords> = {
  complete: { word: "Complete", symbol: "✓", tone: "ok" },
  incomplete: { word: "Incomplete", symbol: "◐", tone: "warn" },
  timed_out: { word: "Timed out", symbol: "⏱", tone: "bad" },
  cancelled: { word: "Cancelled", symbol: "✕", tone: "bad" },
};

export function outcomeWords(outcome: ComparisonOutcome): StateWords {
  return OUTCOMES[outcome] ?? { word: outcome, symbol: "?", tone: "warn" };
}

const KIND_WORDS: Record<string, string> = {
  principal: "identities",
  membership_edge: "group memberships",
  server: "servers",
  smb_share: "shares",
  smb_ace: "share permission entries",
  ntfs_resource: "folders",
  ntfs_ace: "folder permission entries",
};

export function kindWords(kind: string): string {
  return KIND_WORDS[kind] ?? kind;
}

/** The headline and the sentence a comparison's outcome panel opens with. */
export function outcomeNotice(comparison: ChangeComparisonResponse): Notice {
  const outcome = comparisonOutcome(comparison);
  const window = comparison.window_ms;
  const windowWords = window === undefined ? "its interactive window" : `its ${seconds(window)} window`;
  switch (outcome) {
    case "complete":
      return {
        tone: "ok",
        headline: comparison.recovered
          ? "Complete — this comparison recovered the earlier attempt"
          : "Complete comparison",
        explanation:
          "Every object covering either instant inside the scope was compared." +
          (comparison.elapsed_ms !== undefined ? ` It took ${comparison.elapsed_ms} ms.` : ""),
      };
    case "incomplete": {
      const through = comparison.compared_through;
      return {
        tone: "warning",
        headline: "Incomplete — this is not the whole comparison",
        explanation:
          (comparison.objects_per_side !== undefined
            ? `More objects lie inside this scope than one comparison reads (${comparison.objects_per_side.toLocaleString("en-US")} per instant). `
            : "More objects lie inside this scope than one comparison reads. ") +
          (through
            ? `Both instants were compared through ${kindWords(through.kind)} up to ${through.key}, and nothing after it was compared. `
            : "") +
          "The changes below are real, but a change past that point is not absent, it is unexamined.",
      };
    }
    case "timed_out":
      return {
        tone: "warning",
        headline: "Timed out — no comparison is presented",
        explanation:
          `The comparison stopped when it passed ${windowWords}` +
          (comparison.stopped_in ? ` (while it was at the ${comparison.stopped_in} step)` : "") +
          ". Nothing it had read is shown as an answer, because an interrupted comparison is not " +
          "one. Choose a recovery below.",
      };
    case "cancelled":
      return {
        tone: "warning",
        headline: "Cancelled",
        explanation: "The comparison was abandoned before it finished. Nothing is presented.",
      };
    default:
      return { tone: "warning", headline: String(outcome), explanation: "" };
  }
}

/** What was read and how far it got, for the diagnostics list. Words only, no judgement. */
export function comparisonDiagnostics(comparison: ChangeComparisonResponse): string[] {
  const lines: string[] = [];
  if (comparison.comparison_id) {
    lines.push(`Comparison ${comparison.comparison_id}`);
  }
  if (comparison.retry_of) {
    lines.push(`Retry of ${comparison.retry_of}`);
  }
  if (comparison.window_ms !== undefined && comparison.elapsed_ms !== undefined) {
    lines.push(`Window ${seconds(comparison.window_ms)}; ran ${comparison.elapsed_ms} ms`);
  }
  if (comparison.objects_read_from !== undefined && comparison.objects_read_to !== undefined) {
    lines.push(
      `Objects read: ${comparison.objects_read_from.toLocaleString("en-US")} at the earlier instant, ` +
        `${comparison.objects_read_to.toLocaleString("en-US")} at the later`,
    );
  }
  if (comparison.differences_found !== undefined) {
    lines.push(`Objects that differed: ${comparison.differences_found.toLocaleString("en-US")}`);
  }
  return lines;
}

/** The label of one recovery action, with the sizes that make it fit when there are any. */
export function recoveryLabel(action: ComparisonRecoveryAction): string {
  if (action.objects_at_from !== null && action.objects_at_to !== null) {
    return (
      `${action.label} (${action.objects_at_from.toLocaleString("en-US")} → ` +
      `${action.objects_at_to.toLocaleString("en-US")} objects)`
    );
  }
  return action.label;
}

function seconds(milliseconds: number): string {
  return milliseconds >= 1000 ? `${milliseconds / 1000}-second` : `${milliseconds} ms`;
}

function formatDay(value: string | null): string {
  if (value === null) {
    return "an unknown date";
  }
  return formatUtc(value).slice(0, 10);
}
