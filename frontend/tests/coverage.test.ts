/**
 * Wording for coverage, risk-total readings and comparison outcomes (ADG-DEMO-03).
 *
 * Pure. Every state gets a word and a symbol, never a color alone; a failed or unavailable
 * scope is never worded as "nothing there"; and only a complete comparison is worded as one.
 * The client's own timeout is a typed failure, not an outage.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/lib/api/client";
import type { ScopeState } from "@/lib/contracts";
import {
  comparisonDiagnostics,
  comparisonOutcome,
  coverageLine,
  evidenceWords,
  incompleteScopes,
  isExhaustive,
  outcomeNotice,
  outcomeWords,
  recoveryLabel,
  scopeStateWords,
  snapshotWords,
  totalsNotice,
  verdictWords,
} from "@/lib/coverage";
import {
  FS03,
  MIG,
  SNAPSHOT,
  apolloCoverage,
  completeRiskSummary,
  coverageSummary,
  fromAnOlderServer,
  incomplete,
  recovered,
  riskSummary,
  timedOut,
} from "./coverage-factories";

describe("every state is a word and a symbol, not a color", () => {
  it.each<ScopeState>(["collected", "partial", "unavailable", "failed", "running"])(
    "%s",
    (state) => {
      const words = scopeStateWords(state);
      expect(words.word).not.toBe("");
      expect(words.symbol).not.toBe("");
      expect(["ok", "warn", "bad"]).toContain(words.tone);
    },
  );

  it("tells unavailable and failed apart, and both from collected", () => {
    const words = new Set(
      (["collected", "unavailable", "failed"] as const).map((state) => scopeStateWords(state).word),
    );
    expect(words.size).toBe(3);
  });

  it("words the verdicts", () => {
    expect(verdictWords("complete").word).toBe("Complete");
    expect(verdictWords("incomplete").word).toBe("Incomplete");
    expect(verdictWords("incomplete").tone).toBe("bad");
  });
});

describe("the evidence a scope holds", () => {
  it("names the scan an unavailable scope's evidence is from", () => {
    expect(evidenceWords(FS03)).toBe("Older evidence, from the scan of 2026-09-17");
  });

  it("says current evidence is current", () => {
    expect(evidenceWords(MIG)).toBe("Current, collected 2026-10-01");
  });

  it("never words a scope nothing was collected for as empty", () => {
    const words = evidenceWords({ evidence: "none", evidence_collected_at: null });
    expect(words).toBe("Nothing collected");
    expect(words.toLowerCase()).not.toContain("none found");
  });

  it("lists only the scopes that are not complete", () => {
    expect(incompleteScopes(apolloCoverage().scopes).map((scope) => scope.label)).toEqual([
      "ntfs (GP-FS-03)",
      "ntfs (GP-MIG-FS01)",
    ]);
  });

  it("states the snapshot", () => {
    expect(snapshotWords(SNAPSHOT)).toBe(
      "Collected state 3f2a9c41d07e · 300 collector runs · last collector write 2026-10-14 22:53:30 UTC",
    );
    expect(snapshotWords({ ...SNAPSHOT, is_empty: true, runs: 0 })).toBe("No collection on record.");
  });

  it("puts the scope counts beside a number, never a percentage", () => {
    const line = coverageLine(coverageSummary());
    expect(line).toBe(
      "Coverage: 98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence",
    );
    expect(line).not.toContain("%");
  });
});

describe("how risk totals may be read", () => {
  it("says incomplete collection makes the counts not exhaustive, with the reason", () => {
    const notice = totalsNotice(riskSummary());
    expect(notice?.headline).toBe("These counts are not exhaustive");
    expect(notice?.tone).toBe("warning");
    expect(notice?.explanation).toContain("1 unavailable");
    expect(isExhaustive(riskSummary())).toBe(false);
  });

  it("calls the counts totals only when the API says they are", () => {
    const notice = totalsNotice(completeRiskSummary());
    expect(notice?.headline).toBe("These counts are totals");
    expect(notice?.tone).toBe("ok");
    expect(isExhaustive(completeRiskSummary())).toBe(true);
  });

  it.each([
    ["stale", "These counts describe an earlier snapshot"],
    ["partial", "These counts are not totals"],
    ["not_evaluated", "The rules have never been evaluated"],
  ] as const)("words %s", (reading, headline) => {
    const summary = riskSummary();
    const notice = totalsNotice({
      ...summary,
      interpretation: { ...summary.interpretation!, reading, exhaustive: false },
    });
    expect(notice?.headline).toBe(headline);
  });

  it("defers to the evaluation-only notice for a server that sent no interpretation", () => {
    const summary = riskSummary({ interpretation: undefined, collection: undefined });
    expect(totalsNotice(summary)).toBeNull();
    expect(isExhaustive(summary)).toBe(false);
  });
});

describe("how a comparison ended", () => {
  it("reads the API's outcome", () => {
    expect(comparisonOutcome(timedOut())).toBe("timed_out");
    expect(comparisonOutcome(incomplete())).toBe("incomplete");
    expect(comparisonOutcome(recovered())).toBe("complete");
  });

  it("never reads an older server's truncated comparison as complete", () => {
    expect(comparisonOutcome(fromAnOlderServer(true))).toBe("incomplete");
    expect(comparisonOutcome(fromAnOlderServer(false))).toBe("complete");
  });

  it("says a timed-out comparison presents nothing, and why", () => {
    const notice = outcomeNotice(timedOut());
    expect(notice.headline).toBe("Timed out — no comparison is presented");
    expect(notice.explanation).toContain("1 ms window");
    expect(notice.explanation).toContain("read step");
  });

  it("says an incomplete comparison is not the whole comparison, and where it stopped", () => {
    const notice = outcomeNotice(incomplete());
    expect(notice.headline).toBe("Incomplete — this is not the whole comparison");
    expect(notice.explanation).toContain("5,000 per instant");
    expect(notice.explanation).toContain("group memberships");
    expect(notice.explanation).toContain("unexamined");
  });

  it("says a recovered comparison is complete and recovered", () => {
    const notice = outcomeNotice(recovered());
    expect(notice.headline).toBe("Complete — this comparison recovered the earlier attempt");
    expect(notice.tone).toBe("ok");
  });

  it("words each outcome with a symbol", () => {
    for (const outcome of ["complete", "incomplete", "timed_out", "cancelled"] as const) {
      expect(outcomeWords(outcome).symbol).not.toBe("");
    }
  });

  it("lists the diagnostics: the attempt, the window, what was read", () => {
    expect(comparisonDiagnostics(incomplete())).toEqual([
      "Comparison aaaaaaaa-0000-4000-8000-000000000002",
      "Retry of aaaaaaaa-0000-4000-8000-000000000001",
      "Window 4-second; ran 180 ms",
      "Objects read: 4,898 at the earlier instant, 5,000 at the later",
      "Objects that differed: 145",
    ]);
  });

  it("labels a narrowing with the sizes that make it fit", () => {
    const [action] = incomplete().recovery!;
    expect(recoveryLabel(action)).toBe("Compare only folder permission entries (3,947 → 4,124 objects)");
  });
});

describe("the client's own deadline", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is a typed timeout, not an unreachable API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("The operation timed out.", "TimeoutError")),
    );

    const result = await apiRequest("/api/v1/changes/compare", { timeoutMs: 10_000 });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.failure.kind).toBe("timeout");
      expect(result.failure.message).toBe("The ADG API did not answer within 10 seconds.");
    }
  });

  it("still reports a refused connection as unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const result = await apiRequest("/api/v1/changes/compare");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.failure.kind).toBe("unreachable");
    }
  });
});
