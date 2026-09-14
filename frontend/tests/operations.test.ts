/**
 * Reading the collector operations report.
 *
 * The functions under test decide nothing about collection — the API does that — but they
 * decide every word an operator reads, and the wording is the product on this screen. A
 * sentence that says "failed" where it should say "succeeded, three days ago" sends
 * somebody to fix a collector that works.
 *
 * Two things get the most attention here. `coverageSentence` is phrased as a fraction of
 * scopes rather than as a health word, because "3 of 8 scopes are incomplete" is actionable
 * and "incomplete" is not. And `countRows` attaches a sentence to every zero, because a zero
 * is the number the panel exists for: a collector that ran cleanly and stored nothing
 * reports success everywhere else in ADG.
 */

import { describe, expect, it } from "vitest";

import type {
  CollectionOperations,
  CollectorErrorGroup,
  ObjectCounts,
  RunOutcome,
  ScopeOperations,
} from "@/lib/contracts";
import {
  ageInWords,
  completenessTone,
  countRows,
  coverageSentence,
  delivery,
  formatUtc,
  isAlarming,
  orderedErrors,
  scopeSentence,
  tallyScopes,
} from "@/lib/operations";

const NOW = new Date("2026-09-14T12:00:00Z");

function run(overrides: Partial<RunOutcome> = {}): RunOutcome {
  return {
    run_id: "r1",
    collector: "ntfs",
    collector_host: "FS01",
    target: "FS01",
    status: "succeeded",
    started_at: "2026-09-14T08:00:00Z",
    completed_at: "2026-09-14T08:04:00Z",
    error_count: 0,
    batches_reported: 2,
    batches_received: 2,
    observations_reported: 100,
    observations_applied: 100,
    declared_scopes: 1,
    reconciled_scopes: 1,
    incremental: false,
    downgrade_reason: null,
    completeness: "complete",
    shortfall: null,
    ...overrides,
  };
}

function scope(overrides: Partial<ScopeOperations> = {}): ScopeOperations {
  const latest = overrides.latest ?? run();
  return {
    collector: latest.collector,
    target: latest.target,
    label: latest.target === null ? latest.collector : `${latest.collector} (${latest.target})`,
    completeness: latest.completeness,
    has_ever_succeeded: true,
    stale_success: false,
    note: null,
    latest,
    last_success: latest,
    last_failure: null,
    ...overrides,
  };
}

const EMPTY_COUNTS: ObjectCounts = {
  principals: 0,
  membership_edges: 0,
  servers: 0,
  shares: 0,
  share_aces: 0,
  directories: 0,
  ntfs_aces: 0,
  scan_runs: 0,
  total: 0,
};

const FULL_COUNTS: ObjectCounts = {
  principals: 41,
  membership_edges: 49,
  servers: 3,
  shares: 7,
  share_aces: 9,
  directories: 21,
  ntfs_aces: 108,
  scan_runs: 8,
  total: 238,
};

describe("how a completeness verdict reads", () => {
  it("gives each of the four states a distinct word and tone", () => {
    const labels = (["complete", "partial", "none", "in_progress"] as const).map((value) =>
      completenessTone(value),
    );

    expect(new Set(labels.map((tone) => tone.label)).size).toBe(4);
    expect(completenessTone("complete").className).toBe("status-ok");
    expect(completenessTone("none").className).toBe("status-bad");
  });

  it("never calls a scope with nothing collected 'empty'", () => {
    // The distinction the whole product rests on. "Nothing collected" is not "nothing is
    // there", and a status word that blurred them would undo it on the one page an
    // operator consults when something is wrong.
    expect(completenessTone("none").label).toBe("nothing collected");
  });

  it("does not describe a running scan as incomplete", () => {
    expect(completenessTone("in_progress").label).toBe("running");
  });
});

describe("timestamps", () => {
  it("renders in UTC, always", () => {
    // Compared against a Windows event log and a collector log, both of which an operator
    // reads in a fixed zone — and rendering in the browser's zone would also make the
    // server and client markup disagree.
    expect(formatUtc("2026-09-14T08:04:00Z")).toBe("2026-09-14 08:04:00 UTC");
  });

  it("shows an em dash for an absent time rather than 'Invalid Date'", () => {
    expect(formatUtc(null)).toBe("—");
  });

  it("returns an unparseable value unchanged rather than inventing one", () => {
    expect(formatUtc("not-a-time")).toBe("not-a-time");
  });
});

describe("how old a run is, in words", () => {
  it("says nothing at all for anything under an hour", () => {
    // An operator does not need to be told that a run from twenty minutes ago is recent,
    // and a line that says so is a line in the way.
    expect(ageInWords("2026-09-14T11:30:00Z", NOW)).toBeNull();
  });

  it("counts hours up to two days", () => {
    expect(ageInWords("2026-09-14T08:00:00Z", NOW)).toBe("4 hours ago");
    expect(ageInWords("2026-09-14T11:00:00Z", NOW)).toBe("1 hour ago");
  });

  it("switches to days beyond that", () => {
    expect(ageInWords("2026-09-10T12:00:00Z", NOW)).toBe("4 days ago");
  });

  it("is null for an absent or unparseable time", () => {
    expect(ageInWords(null, NOW)).toBeNull();
    expect(ageInWords("nonsense", NOW)).toBeNull();
  });
});

describe("the sentence under a scope", () => {
  it("uses the API's own note verbatim when there is one", () => {
    // The API's wording is authoritative: it already distinguishes "never ran" from
    // "failed, and here is how old your data is". Re-wording it here would create a second
    // place those two could be confused.
    const note = "The latest ntfs (FS03) run failed. The data on screen is from 2026-09-13.";

    expect(scopeSentence(scope({ note }), NOW)).toBe(note);
  });

  it("says something rather than nothing when the scope is healthy", () => {
    // The API says nothing when nothing is wrong, and a blank cell reads as missing data
    // rather than as fine.
    // The run completed at 08:04 and "now" is 12:00: three hours and fifty-six minutes,
    // which floors to three. Floored rather than rounded, deliberately — an age that reads
    // older than it is sends somebody to look at a scan that is fine.
    const sentence = scopeSentence(scope(), NOW);

    expect(sentence).toContain("3 hours ago");
    expect(sentence).toContain("nothing reported missing");
  });

  it("says 'just now' for a scope that has only this moment finished", () => {
    const fresh = run({ completed_at: "2026-09-14T11:59:00Z" });

    expect(scopeSentence(scope({ latest: fresh, last_success: fresh }), NOW)).toContain(
      "just now",
    );
  });
});

describe("the coverage headline", () => {
  it("says what has been collected as a fraction of scopes, not as a word", () => {
    const tally = tallyScopes([
      scope({ latest: run({ completeness: "complete" }) }),
      scope({ latest: run({ completeness: "partial", target: "FS02" }) }),
      scope({ latest: run({ completeness: "none", target: "FS03" }), has_ever_succeeded: false }),
    ]);

    const sentence = coverageSentence(tally);

    expect(sentence).toContain("1 of 3 scopes are complete");
    expect(sentence).toContain("1 incomplete");
    expect(sentence).toContain("1 with nothing usable");
  });

  it("calls out scopes that have never completed a run at all", () => {
    const tally = tallyScopes([
      scope({ latest: run({ completeness: "none" }), has_ever_succeeded: false }),
    ]);

    expect(coverageSentence(tally)).toContain("never completed a run");
  });

  it("does not hedge when everything is complete", () => {
    const tally = tallyScopes([scope(), scope({ latest: run({ target: "FS02" }) })]);

    expect(coverageSentence(tally)).toBe("All 2 collection scopes are current and complete.");
  });

  it("says nothing ran rather than that everything is fine, when nothing ran", () => {
    // Zero of zero scopes are incomplete, which is arithmetically true and the most
    // misleading sentence this page could print.
    const sentence = coverageSentence(tallyScopes([]));

    expect(sentence).toContain("No collector has ever reported");
    expect(sentence).not.toContain("complete");
  });

  it("counts a running scan separately from a broken one", () => {
    const tally = tallyScopes([scope({ latest: run({ completeness: "in_progress" }) })]);

    expect(tally.in_progress).toBe(1);
    expect(coverageSentence(tally)).toContain("still running");
  });
});

describe("the object counts", () => {
  it("attaches a sentence to every zero", () => {
    const rows = countRows(EMPTY_COUNTS);

    expect(rows.every((row) => row.concern !== null)).toBe(true);
    expect(rows.find((row) => row.key === "ntfs_aces")?.concern).toContain(
      "grants nobody access",
    );
  });

  it("leaves a populated count unannotated", () => {
    const rows = countRows(FULL_COUNTS);

    expect(rows.every((row) => row.concern === null)).toBe(true);
  });

  it("covers every field of the counts object", () => {
    // A field the API adds and this table does not list is a number nobody ever sees.
    // `total` is excluded because it is a derived headline, not a stored object count.
    const listed = new Set(countRows(FULL_COUNTS).map((row) => row.key));
    const counted = Object.keys(FULL_COUNTS).filter((key) => key !== "total");

    expect(listed).toEqual(new Set(counted));
  });

  it("explains why a zero membership count matters, not just that it is zero", () => {
    const row = countRows(EMPTY_COUNTS).find((item) => item.key === "membership_edges");

    expect(row?.concern).toContain("access is under-reported");
  });
});

describe("the error summary", () => {
  const group = (overrides: Partial<CollectorErrorGroup>): CollectorErrorGroup => ({
    code: "access_denied",
    count: 1,
    collectors: ["ntfs"],
    latest_occurred_at: null,
    sample_targets: [],
    is_widespread: false,
    ...overrides,
  });

  it("puts widespread codes first, however small their count", () => {
    // The same failure across two collectors is rarely one bad object, and it is the one an
    // operator should look at first even when something else is more numerous.
    const ordered = orderedErrors([
      group({ code: "path_too_long", count: 90 }),
      group({ code: "access_denied", count: 2, is_widespread: true }),
    ]);

    expect(ordered.map((item) => item.code)).toEqual(["access_denied", "path_too_long"]);
  });

  it("orders by count within each band", () => {
    const ordered = orderedErrors([
      group({ code: "b", count: 1 }),
      group({ code: "a", count: 9 }),
    ]);

    expect(ordered.map((item) => item.code)).toEqual(["a", "b"]);
  });

  it("does not mutate the array it was given", () => {
    const input = [group({ code: "b" }), group({ code: "a", is_widespread: true })];
    const before = input.map((item) => item.code);

    orderedErrors(input);

    expect(input.map((item) => item.code)).toEqual(before);
  });
});

describe("what a run delivered", () => {
  it("shows both halves even when they agree", () => {
    // "88" alone invites the reader to assume it is what was asked for.
    expect(delivery(run({ observations_applied: 88, observations_reported: 88 }))).toContain(
      "88 of 88 observations",
    );
  });

  it("makes a shortfall visible in the numbers", () => {
    expect(delivery(run({ observations_applied: 80, observations_reported: 100 }))).toContain(
      "80 of 100",
    );
  });

  it("reports a bare count when the run claimed no total", () => {
    const open = run({ observations_reported: null, batches_reported: null });

    expect(delivery(open)).toBe("100 observations in 2 batches");
  });
});

describe("whether the page reads as an alert", () => {
  const report = (health: CollectionOperations["health"]): CollectionOperations => ({
    health,
    summary: "",
    notes: [],
    scopes: [],
    counts: EMPTY_COUNTS,
    errors: [],
    total_errors: 0,
  });

  it("is driven by the API's own health, not by counting rows here", () => {
    // The coverage banner every other page shows is computed from the same field. Two
    // screens disagreeing about whether the estate is trustworthy is worse than either
    // being wrong.
    expect(isAlarming(report("failed"))).toBe(true);
    expect(isAlarming(report("incomplete"))).toBe(true);
    expect(isAlarming(report("healthy"))).toBe(false);
  });

  it("does not raise an alert merely because nothing has run", () => {
    // A starting state, not a failure. Dressing it up as one trains people to ignore the
    // banner.
    expect(isAlarming(report("no_data"))).toBe(false);
  });
});
