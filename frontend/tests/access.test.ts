/**
 * The wording of an engine answer.
 *
 * This is the module where a rounding error becomes a wrong audit finding. The table in
 * `lib/access.ts` has one row that matters more than the rest — `access: false` with
 * certainty `at_least`, which means "nothing was established" and not "there is nothing" —
 * and the test that pins it is the first one below.
 */

import { describe, expect, it } from "vitest";

import {
  accessVerdict,
  isAnswer,
  limitingLayerLabel,
  rightsNotes,
  rightsSummary,
} from "@/lib/access";
import type { AccessCertainty } from "@/lib/contracts";
import { rights } from "./factories";

describe("a verdict", () => {
  it("never words an unestablished answer as a negative one", () => {
    const verdict = accessVerdict(false, "at_least");

    expect(verdict.word).toBe("None established");
    expect(verdict.word).not.toBe("No");
    expect(verdict.explanation).toContain("not a finding that there is none");
  });

  it("words a measured negative as a plain No", () => {
    expect(accessVerdict(false, "certain").word).toBe("No");
    expect(accessVerdict(false, "at_most").word).toBe("No");
  });

  it("marks a bounded positive as a bound", () => {
    expect(accessVerdict(true, "at_most").word).toBe("Yes, at most");
    expect(accessVerdict(true, "at_least").word).toBe("Yes, at least");
  });

  it("refuses to be an answer at all when the engine says uncertain", () => {
    const verdict = accessVerdict(true, "uncertain");

    expect(verdict.word).toBe("Unknown");
    expect(verdict.explanation).toContain("no answer rather than as a negative one");
  });

  it("always carries an explanation, for every combination", () => {
    const certainties: AccessCertainty[] = ["certain", "at_most", "at_least", "uncertain"];
    for (const certainty of certainties) {
      for (const access of [true, false]) {
        expect(accessVerdict(access, certainty).explanation.length, `${access}/${certainty}`)
          .toBeGreaterThan(0);
      }
    }
  });
});

describe("whether a verdict settles anything", () => {
  it("settles a measurement in both directions", () => {
    expect(isAnswer("certain", true)).toBe(true);
    expect(isAnswer("certain", false)).toBe(true);
  });

  it("settles a bound only in the direction it bounds", () => {
    // "At most nothing" is nothing. "At least nothing" is not an answer.
    expect(isAnswer("at_most", false)).toBe(true);
    expect(isAnswer("at_least", false)).toBe(false);
    expect(isAnswer("at_least", true)).toBe(true);
  });

  it("settles nothing when the engine is uncertain", () => {
    expect(isAnswer("uncertain", true)).toBe(false);
    expect(isAnswer("uncertain", false)).toBe(false);
  });
});

describe("which layer is the narrower one", () => {
  it("has a phrase for every value the API can send", () => {
    for (const layer of ["none", "smb_share", "ntfs", "both", "unknown"] as const) {
      expect(limitingLayerLabel(layer), layer).toMatch(/\S/);
    }
  });
});

describe("notes about a mask", () => {
  it("surfaces the ability to self-grant", () => {
    expect(rightsNotes(rights({ escalation_rights: ["WRITE_DAC"] }))).toContain(
      "can self-grant: WRITE_DAC",
    );
  });

  it("says a MAXIMUM_ALLOWED mask has no fixed rights set", () => {
    expect(rightsNotes(rights({ indeterminate: true }))[0]).toContain("MAXIMUM_ALLOWED");
  });

  it("says when the label does not cover the whole mask", () => {
    expect(rightsNotes(rights({ is_exact: false, extra_rights: ["DELETE"] }))).toContain(
      "beyond the label: DELETE",
    );
    expect(rightsNotes(rights({ is_exact: false }))).toContain(
      "the mask carries rights the label does not name",
    );
  });

  it("says nothing about an exact, ordinary mask", () => {
    expect(rightsNotes(rights())).toEqual([]);
  });
});

describe("summarizing a page of answers", () => {
  const row = (over: { access?: boolean; certainty?: AccessCertainty; primary?: string }) => ({
    access: over.access ?? true,
    certainty: over.certainty ?? ("certain" as AccessCertainty),
    rights: rights({ primary: over.primary ?? "read" }),
  });

  it("counts granted rows by their strongest category", () => {
    const summary = rightsSummary([
      row({ primary: "read" }),
      row({ primary: "read" }),
      row({ primary: "modify" }),
    ]);

    expect(summary.rows).toEqual([
      { category: "read", count: 2 },
      { category: "modify", count: 1 },
    ]);
  });

  it("keeps rows with no access out of the categories", () => {
    const summary = rightsSummary([row({ access: false }), row({ primary: "read" })]);

    expect(summary.rows).toEqual([{ category: "read", count: 1 }]);
    expect(summary.none).toBe(1);
  });

  it("counts a bounded grant as uncertain even though it grants", () => {
    // An upper bound of "Modify" is not a measurement of "Modify".
    const summary = rightsSummary([row({ certainty: "at_most", primary: "modify" })]);

    expect(summary.uncertain).toBe(1);
    expect(summary.rows).toEqual([{ category: "modify", count: 1 }]);
  });

  it("summarizes an empty page as nothing rather than as zero of something", () => {
    expect(rightsSummary([])).toEqual({ rows: [], uncertain: 0, none: 0 });
  });
});
