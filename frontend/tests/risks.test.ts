/**
 * The wordings a risk report shows, and the one they exist to prevent.
 *
 * An empty findings table means one of three completely different things and they look
 * identical. Only the third is good news:
 *
 * - the rules have never been evaluated, so nothing has looked;
 * - the last pass was incremental, so what is there is a fraction of the estate;
 * - a full pass covered everything and found nothing.
 *
 * Most of this file is about keeping those apart.
 */

import { describe, expect, it } from "vitest";

import type {
  FindingSummaryView,
  RiskConfigurationView,
  RiskCoverageView,
  RiskRuleView,
} from "@/lib/contracts";
import {
  ageWording,
  confidenceWording,
  configurationNotice,
  coverageNotice,
  hiddenByStatus,
  recurrenceNotice,
  ruleTallies,
  severityTiles,
  severityTone,
  subjectLabel,
} from "@/lib/risks";

function coverage(overrides: Partial<RiskCoverageView> = {}): RiskCoverageView {
  return {
    has_ever_run: true,
    evaluated_at: "2026-09-14T12:00:00Z",
    trigger: "full",
    complete: true,
    rules_run: ["everyone_has_access"],
    rules_skipped: [],
    truncation: null,
    evaluation_id: "11111111-1111-1111-1111-111111111111",
    ...overrides,
  };
}

function finding(overrides: Partial<FindingSummaryView> = {}): FindingSummaryView {
  return {
    key: "a".repeat(64),
    rule_id: "everyone_has_access",
    title: "Everyone can reach this resource",
    status: "open",
    severity: "critical",
    confidence: "confirmed",
    band: "full_control",
    qualifiers: [],
    subject: {
      resource_key: "\\\\fs01\\finance",
      share_key: null,
      principal_key: null,
      discriminator: null,
      kind: "resource",
    },
    detail: {},
    first_detected_at: "2026-09-01T00:00:00Z",
    detected_at: "2026-09-01T00:00:00Z",
    last_evaluated_at: "2026-09-14T00:00:00Z",
    resolved_at: null,
    occurrence_count: 1,
    evidence_count: 3,
    evidence_digest: "b".repeat(64),
    detail_url: "/api/v1/risks/findings/" + "a".repeat(64),
    ...overrides,
  };
}

describe("the coverage notice", () => {
  it("says plainly when nothing has ever looked", () => {
    const notice = coverageNotice(coverage({ has_ever_run: false, complete: false }));

    expect(notice.tone).toBe("warning");
    expect(notice.headline).toMatch(/never been evaluated/);
    // The sentence that matters: a reader must not take an empty table for a clean estate.
    expect(notice.explanation).toMatch(/nothing has looked/);
  });

  it("says an incremental pass produced counts that are not totals", () => {
    const notice = coverageNotice(coverage({ complete: false, trigger: "incremental" }));

    expect(notice.tone).toBe("warning");
    expect(notice.headline).toMatch(/not totals/);
    expect(notice.explanation).toMatch(/incremental/);
  });

  it("quotes what a truncated pass ran out of room for", () => {
    const notice = coverageNotice(
      coverage({ complete: false, truncation: "resource ceiling of 5000 reached" }),
    );

    expect(notice.explanation).toMatch(/5000/);
  });

  it("is returned even when the news is good", () => {
    // A caveat that appears only when things are bad teaches a reader to skip it, and the
    // one time it matters they will.
    const notice = coverageNotice(coverage());

    expect(notice.tone).toBe("ok");
    expect(notice.explanation).toMatch(/totals/);
  });
});

describe("the configuration notice", () => {
  const base: RiskConfigurationView = {
    version: "default",
    rules_enabled: 11,
    rules_disabled: [],
    marks_anything_sensitive: true,
  };

  it("is silent when nothing is being hidden", () => {
    expect(configurationNotice(base)).toBeNull();
  });

  it("names the rules that are off", () => {
    const notice = configurationNotice({ ...base, rules_disabled: ["stale_direct_ace"] });

    expect(notice).toMatch(/stale_direct_ace/);
    expect(notice).toMatch(/not here/);
  });

  it("says when nothing is marked sensitive, and that ADG will not guess", () => {
    const notice = configurationNotice({ ...base, marks_anything_sensitive: false });

    expect(notice).toMatch(/will not guess/);
  });
});

describe("what the filter is hiding", () => {
  it("counts the statuses not being shown", () => {
    expect(hiddenByStatus({ open: 12, resolved: 340 }, ["open"])).toBe(340);
  });

  it("is zero when everything is shown", () => {
    expect(hiddenByStatus({ open: 12, resolved: 340 }, ["open", "resolved"])).toBe(0);
  });
});

describe("the severity tiles", () => {
  it("include every severity in rank order, zeros and all", () => {
    // A tile list that omitted the empty ones would make a report with no critical findings
    // look like a report that has no critical category.
    const tiles = severityTiles({ high: 2 });

    expect(tiles.map((tile) => tile.severity)).toEqual([
      "critical",
      "high",
      "medium",
      "low",
      "informational",
    ]);
    expect(tiles[0].count).toBe(0);
    expect(tiles[1].count).toBe(2);
  });

  it("maps severity onto three tones rather than five", () => {
    expect(severityTone("critical")).toBe("bad");
    expect(severityTone("high")).toBe("bad");
    expect(severityTone("medium")).toBe("warn");
    expect(severityTone("informational")).toBe("ok");
  });
});

describe("the rule tally", () => {
  const rules = [
    { rule_id: "b_rule", title: "B rule" },
    { rule_id: "a_rule", title: "A rule" },
  ] as RiskRuleView[];

  it("is ordered by count and then by rule id", () => {
    // The second key is not cosmetic: without it two equal counts shuffle between renders
    // and a reader comparing two screenshots sees a change that did not happen.
    const tallies = ruleTallies({ b_rule: 3, a_rule: 3, c_rule: 9 }, rules);

    expect(tallies.map((entry) => entry.ruleId)).toEqual(["c_rule", "a_rule", "b_rule"]);
  });

  it("falls back to the rule id when the catalog does not name it", () => {
    const tallies = ruleTallies({ c_rule: 1 }, rules);

    expect(tallies[0].title).toBe("c_rule");
  });
});

describe("confidence", () => {
  it("is a sentence about the evidence, not a hedge", () => {
    // Left as bare words, `probable` and `possible` read as uncertainty about the rule.
    // They are statements about ADG's inputs, and somebody deciding whether to act needs
    // the specific version.
    expect(confidenceWording("probable")).toMatch(/derived rather than read/);
    expect(confidenceWording("possible")).toMatch(/cannot settle it/);
    expect(confidenceWording("confirmed")).toMatch(/read directly/);
  });
});

describe("a finding row", () => {
  it("shows how long it has been true and how stale the claim is", () => {
    // Two numbers, on purpose. "Open since March" is what somebody acts on; "last confirmed
    // three weeks ago" is how far that is actually warranted.
    const wording = ageWording(finding(), new Date("2026-09-15T00:00:00Z"));

    expect(wording).toMatch(/first seen 14 day/);
    expect(wording).toMatch(/last confirmed 1 day/);
  });

  it("points out a finding that keeps coming back", () => {
    expect(recurrenceNotice(finding())).toBeNull();
    expect(recurrenceNotice(finding({ occurrence_count: 4 }))).toMatch(/4 times/);
    expect(recurrenceNotice(finding({ occurrence_count: 4 }))).toMatch(/re-creates it/);
  });

  it("names the place, whichever kind of key the subject carries", () => {
    expect(subjectLabel(finding())).toBe("\\\\fs01\\finance");
    expect(
      subjectLabel(
        finding({
          subject: {
            resource_key: null,
            share_key: "share|fs01|finance",
            principal_key: null,
            discriminator: null,
            kind: "resource",
          },
        }),
      ),
    ).toBe("share|fs01|finance");
  });
});
