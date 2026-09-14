/**
 * Putting an engine answer into words without rounding it off.
 *
 * `access: false` is not "no access". It is "no access was established", and what that is
 * worth depends entirely on the certainty beside it:
 *
 * | access | certainty  | what it means                                              |
 * | ------ | ---------- | ---------------------------------------------------------- |
 * | true   | `certain`  | these rights, measured                                      |
 * | true   | `at_most`  | at most these rights; something unread could narrow them    |
 * | true   | `at_least` | at least these rights; something unread could widen them    |
 * | false  | `at_least` | nothing established — **not** a finding of no access        |
 * | any    | `uncertain`| the answer is not usable as an answer                       |
 *
 * The false/`at_least` row is the one that matters. A directory whose descriptor no run
 * has read produces exactly that, and rendering it as "No" in a table is how a gap in
 * collection becomes a clean bill of health.
 *
 * Nothing here computes access. It reads the certainty the engine attached and chooses the
 * wording; the engine itself is checked against Windows' own `AuthzAccessCheck`.
 */

import type { AccessCertainty, LimitingLayer, RightsView } from "@/lib/contracts";

/** The shape both access listings share: a verdict, a mask, and a certainty. */
export interface AccessRow {
  access: boolean;
  certainty: AccessCertainty;
  rights: RightsView;
}

export interface AccessVerdict {
  /** The word in the cell. */
  word: string;
  tone: "ok" | "warn" | "bad" | "info";
  /** The sentence beside or beneath it. Never omitted for a bounded answer. */
  explanation: string;
}

export function accessVerdict(access: boolean, certainty: AccessCertainty): AccessVerdict {
  if (certainty === "uncertain") {
    return {
      word: "Unknown",
      tone: "warn",
      explanation:
        "Too much of what this answer depends on has not been collected. Treat it as no " +
        "answer rather than as a negative one.",
    };
  }

  if (access) {
    switch (certainty) {
      case "certain":
        return { word: "Yes", tone: "bad", explanation: "Measured against the ACLs ADG holds." };
      case "at_most":
        return {
          word: "Yes, at most",
          tone: "warn",
          explanation:
            "An upper bound: something unread could narrow these rights, so the real " +
            "access may be less than shown.",
        };
      case "at_least":
        return {
          word: "Yes, at least",
          tone: "bad",
          explanation:
            "A lower bound: something unread could widen these rights, so the real access " +
            "may be more than shown.",
        };
    }
  }

  switch (certainty) {
    case "certain":
      return { word: "No", tone: "ok", explanation: "Measured against the ACLs ADG holds." };
    case "at_most":
      return {
        word: "No",
        tone: "ok",
        explanation: "An upper bound of nothing is nothing: no access, subject to what was read.",
      };
    case "at_least":
      return {
        word: "None established",
        tone: "warn",
        explanation:
          "A lower bound of nothing. ADG could not establish any access here — this is not " +
          "a finding that there is none.",
      };
  }
}

/** Whether the verdict is safe to read as an answer at all. */
export function isAnswer(certainty: AccessCertainty, access: boolean): boolean {
  if (certainty === "certain") {
    return true;
  }
  if (certainty === "uncertain") {
    return false;
  }
  // A bound is an answer in the direction it bounds: "at most X" settles that it is no
  // more than X, and "at least X" settles that it is no less.
  return access ? true : certainty === "at_most";
}

/** Which ACL removed the rights the other one granted — the layer to go and fix. */
export function limitingLayerLabel(layer: LimitingLayer): string {
  switch (layer) {
    case "none":
      return "neither layer restricted this";
    case "smb_share":
      return "the share ACL is the narrower one";
    case "ntfs":
      return "the NTFS ACL is the narrower one";
    case "both":
      return "both layers restrict this";
    case "unknown":
      return "which layer restricts this was not established";
  }
}

/** Things about a rights mask a reader must not miss. */
export function rightsNotes(rights: RightsView): string[] {
  const notes: string[] = [];
  if (rights.indeterminate) {
    notes.push(
      "MAXIMUM_ALLOWED is present — Windows resolves these rights per open, so no fixed set exists",
    );
  }
  if (rights.escalation_rights.length > 0) {
    notes.push(`can self-grant: ${rights.escalation_rights.join(", ")}`);
  }
  if (!rights.is_exact && rights.extra_rights.length > 0) {
    notes.push(`beyond the label: ${rights.extra_rights.join(", ")}`);
  } else if (!rights.is_exact) {
    notes.push("the mask carries rights the label does not name");
  }
  if (rights.unrecognized_bits) {
    notes.push(`unrecognized mask bits ${rights.unrecognized_bits}`);
  }
  return notes;
}

export interface RightsSummary {
  /** Strongest category first, as the engine ordered them. Only rows that grant something. */
  rows: { category: string; count: number }[];
  /** Rows whose verdict is a bound or unusable rather than a measurement. */
  uncertain: number;
  /** Rows where no access was established. */
  none: number;
}

/**
 * What a page of access answers adds up to.
 *
 * Deliberately a summary of *the rows it was given* and nothing more. Every caller renders
 * it beside the count of rows on the page, because one page of a paged listing is not the
 * estate, and a summary that forgets to say so is how "3 shares grant write" gets reported
 * from page one of eleven.
 *
 * Rows with no access are counted separately rather than folded into a category, and a
 * bounded verdict is counted as uncertain even when it grants something — an upper bound of
 * "Modify" is not a measurement of "Modify".
 */
export function rightsSummary(rows: readonly AccessRow[]): RightsSummary {
  const counts = new Map<string, number>();
  let uncertain = 0;
  let none = 0;

  for (const row of rows) {
    if (row.certainty !== "certain") {
      uncertain += 1;
    }
    if (!row.access) {
      none += 1;
      continue;
    }
    const category = row.rights.primary;
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }

  return {
    rows: [...counts].map(([category, count]) => ({ category, count })),
    uncertain,
    none,
  };
}
