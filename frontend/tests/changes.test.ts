/**
 * The wording and ordering the Changes page depends on.
 *
 * Every case here is one where the obvious rendering would make the page state something
 * false while looking perfectly fine — which is the only kind of frontend bug this product
 * cannot afford. Nothing here tests that a judgment is *correct*; the backend decides those
 * and the frontend carries them. These test that the frontend carries them honestly.
 */

import { describe, expect, it } from "vitest";

import type {
  AceEditView,
  ChangeSummaryResponse,
  ChangeView,
  ChangeWindowView,
} from "@/lib/contracts";
import {
  SEVERITY_ORDER,
  actionWording,
  editFor,
  exclusionNotice,
  groupChanges,
  headline,
  impactCaveat,
  impactWording,
  reconstructionCaveat,
  renderValue,
  severityRank,
  severityTone,
  truncationNotice,
  visibleDeltas,
  windowSpan,
  windowWording,
} from "@/lib/changes";

const WINDOW: ChangeWindowView = {
  after: "2026-03-04T09:00:00Z",
  at_or_before: "2026-03-06T09:00:00Z",
  is_exact: false,
  duration_seconds: 172_800,
};

function change(overrides: Partial<ChangeView> = {}): ChangeView {
  return {
    kind: "ntfs_ace",
    key: "\\\\fs01\\finance|S-1-1-0|allow|0x001200a9|0x03",
    action: "added",
    significance: "security",
    direction: "broadened",
    severity: "high",
    reasons: ["Everyone was granted access."],
    rule_ids: ["ace.allow.broad"],
    at: "2026-03-06T09:00:00Z",
    window: WINDOW,
    reconstructed: false,
    subject: {
      container_kind: "ntfs_resource",
      container_key: "\\\\fs01\\finance",
      related_kind: "principal",
      related_key: "S-1-1-0",
    },
    before: null,
    after: {
      present: true,
      valid_from: "2026-03-06T09:00:00Z",
      last_seen_at: "2026-03-06T09:00:00Z",
      valid_to: null,
      origin: "observed",
      opened_by_run_id: "11111111-2222-3333-4444-555555555555",
      last_seen_run_id: "11111111-2222-3333-4444-555555555555",
      state: { trustee_key: "S-1-1-0", access_mask: 1_179_817 },
    },
    deltas: [],
    edit: null,
    ...overrides,
  };
}

const EDIT: AceEditView = {
  index: 0,
  kind: "ntfs_ace",
  container_key: "\\\\fs01\\finance",
  trustee_key: "S-1-5-21-1-2-3-1101",
  ace_type: "allow",
  direction: "narrowed",
  severity: "low",
  summary: "The Allow entry for Finance-RW was rewritten from 0x001f01ff to 0x001200a9.",
  rights_before: null,
  rights_after: null,
};

function summary(overrides: Partial<ChangeSummaryResponse> = {}): ChangeSummaryResponse {
  return {
    window_from: "2026-03-04T09:00:00Z",
    window_to: "2026-03-06T09:00:00Z",
    total: 10,
    returned: 3,
    excluded: 7,
    highest_severity: "high",
    reconstructed: 0,
    truncated: false,
    by_action: {},
    by_significance: {},
    by_severity: {},
    by_kind: {},
    ...overrides,
  };
}

describe("severity is ordered by rank", () => {
  it("runs from informational to critical", () => {
    expect(SEVERITY_ORDER).toEqual(["info", "low", "medium", "high", "critical"]);
  });

  it("does not order alphabetically, which would put critical below info", () => {
    expect("critical" < "info").toBe(true);
    expect(severityRank("critical")).toBeGreaterThan(severityRank("info"));
  });

  it("gives critical and high the same tone as each other and not as low", () => {
    expect(severityTone("critical")).toBe("bad");
    expect(severityTone("high")).toBe("bad");
    expect(severityTone("low")).toBe("ok");
  });
});

describe("a first sighting is never worded as a creation", () => {
  it("is 'first seen', not 'added'", () => {
    const wording = actionWording("first_observed");
    expect(wording.label).toBe("First seen");
    expect(wording.label.toLowerCase()).not.toContain("add");
  });

  it("says what it actually records", () => {
    expect(actionWording("first_observed").explanation).toContain("not evidence");
  });

  it("words a removal as something somebody measured", () => {
    const wording = actionWording("removed");
    expect(wording.explanation).toContain("reconciled");
    expect(wording.explanation).toContain("did not find");
  });
});

describe("when a change happened is a window, never an instant", () => {
  it("names both ends", () => {
    const text = windowWording(WINDOW);
    expect(text).toContain(WINDOW.after);
    expect(text).toContain(WINDOW.at_or_before);
  });

  it("says so when two observations bracket it exactly", () => {
    const exact = { ...WINDOW, is_exact: true, duration_seconds: 0 };
    expect(windowWording(exact)).toContain("bracket it exactly");
    expect(windowSpan(exact)).toBe("exact");
  });

  it("admits when nothing bounds it rather than inventing a bound", () => {
    expect(windowWording(null)).toContain("unknown");
    expect(windowSpan(null)).toBe("unknown");
  });

  it("renders the width in a unit a person reads", () => {
    expect(windowSpan(WINDOW)).toBe("2d");
    expect(windowSpan({ ...WINDOW, duration_seconds: 90 })).toBe("2m");
    expect(windowSpan({ ...WINDOW, duration_seconds: 7200 })).toBe("2h");
  });
});

describe("an ACL edit reads as one edit", () => {
  const removal = change({ action: "removed", edit: 0, key: "old" });
  const addition = change({ action: "added", edit: 0, key: "new" });

  it("groups both halves together", () => {
    const groups = groupChanges([removal, addition], [EDIT]);
    expect(groups).toHaveLength(1);
    expect(groups[0].edit).toBe(EDIT);
    expect(groups[0].changes).toHaveLength(2);
  });

  it("uses the edit's own sentence as the headline", () => {
    expect(headline(removal, [EDIT])).toBe(EDIT.summary);
  });

  it("falls back to the change's own reason when it stands alone", () => {
    const alone = change({ edit: null });
    expect(headline(alone, [EDIT])).toBe("Everyone was granted access.");
  });

  it("preserves the order the response gave", () => {
    const solo = change({ key: "solo", edit: null, at: "2026-03-07T09:00:00Z" });
    const groups = groupChanges([solo, removal, addition], [EDIT]);
    expect(groups.map((group) => group.changes[0].key)).toEqual(["solo", "old"]);
  });

  it("returns no edit for a change that names one the response did not send", () => {
    expect(editFor(change({ edit: 7 }), [EDIT])).toBeNull();
  });
});

describe("the diff view", () => {
  it("drops provenance-only rows, which every re-observation carries", () => {
    const deltas = [
      { field: "source_key", before: "a", after: "b", significance: "noise" },
      { field: "enabled", before: true, after: false, significance: "security" },
    ];
    expect(visibleDeltas(deltas).map((delta) => delta.field)).toEqual(["enabled"]);
  });

  it("keeps a field ADG could not classify", () => {
    const deltas = [{ field: "new", before: null, after: 1, significance: "unclassified" }];
    expect(visibleDeltas(deltas)).toHaveLength(1);
  });

  it("renders a missing value as missing rather than as false", () => {
    expect(renderValue(null)).toBe("—");
    expect(renderValue(undefined)).toBe("—");
    expect(renderValue(false)).toBe("no");
  });

  it("renders an empty string visibly", () => {
    expect(renderValue("")).toBe('""');
  });
});

describe("a filtered page says what it hid", () => {
  it("names the number", () => {
    const notice = exclusionNotice(summary());
    expect(notice).toContain("7 of 10");
  });

  it("says nothing when nothing was hidden", () => {
    expect(exclusionNotice(summary({ excluded: 0, returned: 10 }))).toBeNull();
  });

  it("explains first sightings when they are what was hidden", () => {
    const notice = exclusionNotice(summary({ by_action: { first_observed: 7 } }));
    expect(notice).toContain("started looking");
  });

  it("warns when the counts are a floor rather than a total", () => {
    expect(truncationNotice(summary({ truncated: true }))).toContain("floor");
    expect(truncationNotice(summary())).toBeNull();
  });
});

describe("an impact verdict is never worded as reassurance", () => {
  it("says what is missing rather than that nothing happened", () => {
    expect(impactWording("needs_a_subject").explanation).toContain("Name a principal");
    expect(impactWording("unbounded").explanation).toContain("name a resource");
  });

  it("refuses to read an inconclusive 'neutral' as 'nothing changed'", () => {
    const caveat = impactCaveat("neutral", false);
    expect(caveat).toContain("not evidence that access did not change");
  });

  it("has nothing to add when both answers were conclusive", () => {
    expect(impactCaveat("neutral", true)).toBeNull();
    expect(impactCaveat("broadened", true)).toBeNull();
  });

  it("still qualifies a non-neutral answer that is not conclusive", () => {
    expect(impactCaveat("broadened", false)).toContain("understated");
  });
});

describe("a reconstructed change says so", () => {
  it("warns when either side came from the backfill", () => {
    expect(reconstructionCaveat(change({ reconstructed: true }))).toContain("reconstructed");
  });

  it("stays quiet otherwise", () => {
    expect(reconstructionCaveat(change())).toBeNull();
  });
});
