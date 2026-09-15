/**
 * Turning a risk report into the sentences a page shows.
 *
 * Pure functions over the API's own response, and the reason this is a module rather than
 * inline JSX is that the interesting decisions here are the wordings — and a wording that
 * can mislead somebody in an access review deserves a test of its own.
 *
 * Nothing here computes a severity, a confidence, or whether a finding is real. Every
 * judgment comes off the response; this file chooses which ones to put in front of a
 * reader and how to say them.
 *
 * ## The sentence this whole file exists for
 *
 * An empty findings table means one of three completely different things, and they look
 * identical:
 *
 * - the rules have never been evaluated, so nothing has looked;
 * - the last pass was incremental, so what is here is a fraction of the estate;
 * - a full pass covered everything and found nothing.
 *
 * Only the third is good news. {@link coverageNotice} is what keeps them apart, and it
 * returns a notice in **all three** cases — a caveat that appears only when things are bad
 * teaches a reader to skip it.
 */

import type {
  FindingSummaryView,
  RiskConfigurationView,
  RiskCoverageView,
  RiskRuleView,
} from "@/lib/contracts";

/** Severity order, most consequential first. The backend's ranking, not a second one. */
export const SEVERITIES = ["critical", "high", "medium", "low", "informational"] as const;

export type Severity = (typeof SEVERITIES)[number];

/** Which badge class a severity gets. Three tones, not five: a badge is a glance, not a scale. */
export function severityTone(severity: string): "bad" | "warn" | "ok" {
  if (severity === "critical" || severity === "high") {
    return "bad";
  }
  if (severity === "medium") {
    return "warn";
  }
  return "ok";
}

/**
 * Confidence, as a sentence rather than a word.
 *
 * `probable` and `possible` are the two that get misread. Left as bare words they look like
 * hedging; what they actually say is something specific about ADG's evidence, and a reader
 * deciding whether to act on a finding needs the specific version.
 */
export function confidenceWording(confidence: string): string {
  switch (confidence) {
    case "confirmed":
      return "Every fact behind this was read directly by a collector.";
    case "probable":
      return "A fact behind this was derived rather than read — most often a permission list projected from a parent directory.";
    case "possible":
      return "A fact behind this is partial — a truncated traversal, or a group nobody enumerated. It may be real and the evidence cannot settle it.";
    default:
      return confidence;
  }
}

export interface CoverageNotice {
  tone: "warning" | "info" | "ok";
  headline: string;
  explanation: string;
}

/**
 * What the counts on this page can and cannot be read as.
 *
 * Returned in every case, including the reassuring one. The three it distinguishes are the
 * three that look identical in an empty table.
 */
export function coverageNotice(coverage: RiskCoverageView): CoverageNotice {
  if (!coverage.has_ever_run) {
    return {
      tone: "warning",
      headline: "The rules have never been evaluated",
      explanation:
        "Nothing below means the estate is clean, because nothing has looked at it yet. " +
        "An evaluation is an operator action: run `python -m app.operations evaluate-risks`, " +
        "or enable evaluation on scan completion.",
    };
  }
  if (!coverage.complete) {
    const why =
      coverage.truncation !== null
        ? `The last pass ran out of room: ${coverage.truncation}`
        : `The last pass was ${coverage.trigger ?? "partial"}, so it examined only part of the estate.`;
    return {
      tone: "warning",
      headline: "These counts are not totals",
      explanation:
        `${why} What is here is what ADG currently holds, not what the estate contains, ` +
        "and nothing outside that pass was resolved. A full evaluation settles both.",
    };
  }
  return {
    tone: "ok",
    headline: "A full pass covered the estate",
    explanation:
      "The last evaluation read every directory, share and principal ADG holds and hit no " +
      "ceiling, so these counts are totals.",
  };
}

/**
 * What the rule configuration is hiding, when it is hiding something.
 *
 * `null` when nothing is disabled and something is marked sensitive. A rule that is off
 * reports nothing, and that silence is a decision somebody made rather than a clean result
 * (ADR-0024).
 */
export function configurationNotice(configuration: RiskConfigurationView): string | null {
  const notes: string[] = [];
  if (configuration.rules_disabled.length > 0) {
    notes.push(
      `${configuration.rules_disabled.length} rule(s) are turned off in this installation ` +
        `(${configuration.rules_disabled.join(", ")}). Anything they would have found is not here.`,
    );
  }
  if (!configuration.marks_anything_sensitive) {
    notes.push(
      "No resource has been declared sensitive, so the sensitive-resource rule reports " +
        "nothing. ADG will not guess which data is sensitive from a folder name.",
    );
  }
  return notes.length > 0 ? notes.join(" ") : null;
}

/**
 * How many findings the filter in use is not showing.
 *
 * The number that makes a filtered page honest. Without it, the default view — open
 * findings — is indistinguishable from a complete result.
 */
export function hiddenByStatus(
  statusCounts: Record<string, number>,
  shown: string[],
): number {
  const visible = new Set(shown);
  return Object.entries(statusCounts)
    .filter(([status]) => !visible.has(status))
    .reduce((total, [, count]) => total + count, 0);
}

/** Severity counts in rank order, zeros included, as tiles. */
export function severityTiles(counts: Record<string, number>): {
  severity: Severity;
  count: number;
  tone: "bad" | "warn" | "ok";
}[] {
  return SEVERITIES.map((severity) => ({
    severity,
    count: counts[severity] ?? 0,
    tone: severityTone(severity),
  }));
}

/** Rule counts, most findings first, then by rule id so two equal counts do not shuffle. */
export function ruleTallies(
  counts: Record<string, number>,
  rules: RiskRuleView[],
): { ruleId: string; title: string; count: number }[] {
  const titles = new Map(rules.map((rule) => [rule.rule_id, rule.title]));
  return Object.entries(counts)
    .map(([ruleId, count]) => ({ ruleId, count, title: titles.get(ruleId) ?? ruleId }))
    .sort((left, right) => right.count - left.count || left.ruleId.localeCompare(right.ruleId));
}

/** Where a finding is, in the terms a reader would go and look at. */
export function subjectLabel(finding: FindingSummaryView): string {
  const { resource_key, share_key, principal_key } = finding.subject;
  return resource_key ?? share_key ?? principal_key ?? "(no subject)";
}

/**
 * How long a finding has been true, and how stale the claim is.
 *
 * Two different numbers on purpose. "Open since March" is what somebody acts on;
 * "last confirmed three weeks ago" is how far that claim is actually warranted, and a
 * report that showed only the first would let a finding nothing has re-examined since the
 * spring read as current.
 */
export function ageWording(finding: FindingSummaryView, now: Date): string {
  const opened = days(new Date(finding.first_detected_at), now);
  const confirmed = days(new Date(finding.last_evaluated_at), now);
  const openedText = opened === 0 ? "first seen today" : `first seen ${opened} day(s) ago`;
  const confirmedText =
    confirmed === 0 ? "confirmed today" : `last confirmed ${confirmed} day(s) ago`;
  return `${openedText}, ${confirmedText}`;
}

function days(from: Date, to: Date): number {
  return Math.max(0, Math.floor((to.getTime() - from.getTime()) / 86_400_000));
}

/**
 * Whether a finding that has come back should be pointed out.
 *
 * A finding that opened once is a defect. One that has opened four times is a defect
 * somebody keeps re-introducing, and that is a different conversation — with a person
 * rather than with an access control list.
 */
export function recurrenceNotice(finding: FindingSummaryView): string | null {
  if (finding.occurrence_count <= 1) {
    return null;
  }
  return (
    `This has been seen ${finding.occurrence_count} times. A finding that keeps coming back ` +
    "is usually a process that re-creates it rather than a permission somebody forgot."
  );
}

/** A short, human rendering of an evidence record's kind. */
export function evidenceKindWording(kind: string): string {
  switch (kind) {
    case "ace":
      return "Access control entry";
    case "resource":
      return "Directory";
    case "share":
      return "Share";
    case "principal":
      return "Principal";
    case "membership":
      return "Group membership";
    case "configuration":
      return "Rule setting";
    default:
      return kind;
  }
}

/** A finding event, as a sentence. */
export function eventWording(eventType: string): string {
  switch (eventType) {
    case "opened":
      return "Opened";
    case "reaffirmed":
      return "Still true";
    case "evidence_changed":
      return "Still true, on different evidence";
    case "resolved":
      return "Resolved";
    case "reopened":
      return "Came back";
    default:
      return eventType;
  }
}
