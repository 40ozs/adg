/**
 * The wording an access review is read through.
 *
 * `lib/governance.ts` chooses words and ordering and decides nothing. So the tests here are
 * about the four renderings that would make a screen state something false while looking
 * perfectly fine:
 *
 * - a drift verdict of `unobserved` is never coloured or worded as a problem *or* as an
 *   all-clear — it is the absence of an answer;
 * - `removing_reviewed_entries_leaves_access === null` is never rendered as the reassuring
 *   answer, because that one has to be measured;
 * - a completion figure never appears without the exclusions beside it;
 * - a bulk selection this application would allow is still one the API can refuse, and the
 *   reasons it *does* give have to name the rule that failed.
 */

import { describe, expect, it } from "vitest";

import type {
  DriftView,
  ItemView,
  QueueEntryView,
  ReachView,
  ReviewerProgressView,
} from "@/lib/contracts";
import {
  DECISION_OPTIONS,
  bulkEligibility,
  bulkSubject,
  commentRequired,
  coverageCaveat,
  decisionLabel,
  driftHeadline,
  driftIsWorthShowing,
  driftTone,
  dueWording,
  queueHeadline,
  reachVerdict,
  reviewersByAttention,
} from "@/lib/governance";

function drift(overrides: Partial<DriftView> = {}): DriftView {
  return {
    verdict: "unchanged",
    has_drifted: false,
    summary: "Unchanged since the campaign was frozen.",
    compared_at: "2026-03-16T09:00:00Z",
    baseline_digest: "a".repeat(64),
    current_content_digest: "b".repeat(64),
    changes: [],
    current_grants: [],
    current_certainty: "observed",
    target_present: true,
    target_certainty: "observed",
    evidence_reissued: false,
    ...overrides,
  };
}

function reach(overrides: Partial<ReachView> = {}): ReachView {
  return {
    available: true,
    unavailable_reason: null,
    direct_paths: 1,
    group_paths: 0,
    routes: [],
    removals: [],
    removing_reviewed_entries_leaves_access: false,
    truncated: false,
    ...overrides,
  };
}

function item(overrides: Partial<ItemView> = {}): ItemView {
  return {
    item_id: "11111111-1111-1111-1111-111111111111",
    campaign_id: "22222222-2222-2222-2222-222222222222",
    focus: "resource",
    target_kind: "resource",
    target_key: "\\\\fs01\\finance",
    target_path: "\\\\fs01\\finance",
    principal_key: "S-1-5-21-1-2-3-1001",
    principal_sid: "S-1-5-21-1-2-3-1001",
    principal_display_name: "CONTOSO\\alice",
    grants: [],
    evidence_digest: "c".repeat(64),
    certainty: "observed",
    status: "pending",
    assignment_id: "33333333-3333-3333-3333-333333333333",
    current_decision_id: null,
    decided_at: null,
    created_at: "2026-03-09T09:00:00Z",
    ...overrides,
  };
}

function reviewer(overrides: Partial<ReviewerProgressView> = {}): ReviewerProgressView {
  return {
    assignment_id: "44444444-4444-4444-4444-444444444444",
    reviewer_subject: "alice",
    reviewer_display_name: "Alice",
    scope: null,
    due_at: null,
    assigned: 10,
    decided: 5,
    pending: 5,
    completion: 0.5,
    late_decisions: 0,
    overdue: false,
    ...overrides,
  };
}

function queueEntry(overrides: Partial<QueueEntryView> = {}): QueueEntryView {
  return {
    campaign_id: "55555555-5555-5555-5555-555555555555",
    name: "Finance recertification",
    focus: "resource",
    status: "active",
    baseline_at: "2026-03-02T09:05:00Z",
    due_at: null,
    assigned: 6,
    decided: 2,
    pending: 4,
    overdue: false,
    ...overrides,
  };
}

describe("the decision vocabulary", () => {
  it("offers approve, revoke, narrower rights, needs investigation and not-mine", () => {
    expect(DECISION_OPTIONS.map((option) => option.value)).toEqual([
      "certify",
      "revoke",
      "modify",
      "investigate",
      "abstain",
    ]);
  });

  it("keeps 'needs investigation' and 'not mine to judge' apart", () => {
    // Folded together, a queue of items awaiting follow-up would be indistinguishable from a
    // queue of misrouted ones — and those go to two different people.
    const investigate = DECISION_OPTIONS.find((option) => option.value === "investigate");
    const abstain = DECISION_OPTIONS.find((option) => option.value === "abstain");

    expect(investigate?.meaning).not.toEqual(abstain?.meaning);
    expect(abstain?.meaning).toContain("who should have been asked");
    expect(investigate?.meaning).toContain("needs looking into");
  });

  it("says plainly that a revoke changes nothing in Windows", () => {
    const revoke = DECISION_OPTIONS.find((option) => option.value === "revoke");

    expect(revoke?.meaning).toContain("does not perform it");
  });

  it("renders a decision this build does not know rather than dropping it", () => {
    expect(decisionLabel("some_future_answer")).toBe("some_future_answer");
  });
});

describe("whether a comment is required", () => {
  it("is optional for an approval under the standard rule", () => {
    expect(commentRequired("certify", "standard")).toBe(false);
  });

  it("is required for every other answer under the standard rule", () => {
    for (const decision of ["revoke", "modify", "abstain", "investigate"]) {
      expect(commentRequired(decision, "standard")).toBe(true);
    }
  });

  it("is required for an approval too when the campaign says always", () => {
    expect(commentRequired("certify", "always")).toBe(true);
  });

  it("is never made optional by a requirement this build does not recognize", () => {
    // The rule can only be raised. A setting nobody here knows must not be read as
    // permission to record an unexplained revocation.
    expect(commentRequired("revoke", "something_new")).toBe(true);
    expect(commentRequired("unknown_decision", "standard")).toBe(true);
  });
});

describe("drift wording", () => {
  it("colours a modified grant as a problem and an unchanged one as fine", () => {
    expect(driftTone("modified")).toBe("bad");
    expect(driftTone("unchanged")).toBe("ok");
  });

  it("does not colour 'nobody has looked' as a problem", () => {
    // It is the absence of an answer rather than bad news, and colouring it as a problem
    // would train reviewers to ignore the colour.
    expect(driftTone("unobserved")).toBe("info");
  });

  it("does not colour a removal as good news", () => {
    // It is often a carried-out revocation and ADG never established that. Rendering it as
    // an all-clear invites closing an item on a cause nobody measured.
    expect(driftTone("removed")).toBe("warn");
  });

  it("distinguishes a deleted target from a withdrawn grant", () => {
    expect(driftHeadline(drift({ verdict: "removed", target_present: true }))).toBe(
      "This grant no longer exists",
    );
    expect(driftHeadline(drift({ verdict: "removed", target_present: false }))).toBe(
      "The target no longer exists",
    );
  });

  it("says ADG cannot answer rather than saying nothing changed", () => {
    expect(driftHeadline(drift({ verdict: "unobserved" }))).toContain("cannot say");
  });

  it("mentions a rewrite that changed nothing", () => {
    expect(driftHeadline(drift({ evidence_reissued: true }))).toContain("rewritten");
  });

  it("shows the campaign banner only when something actually moved", () => {
    expect(driftIsWorthShowing({ modified: 0, removed: 0 })).toBe(false);
    expect(driftIsWorthShowing({ modified: 0, removed: 1 })).toBe(true);
    expect(driftIsWorthShowing({ modified: 2, removed: 0 })).toBe(true);
  });
});

describe("what a revocation would achieve", () => {
  it("says so loudly when removing the entry would change nothing", () => {
    const verdict = reachVerdict(
      reach({ removing_reviewed_entries_leaves_access: true, group_paths: 2 }),
    );

    expect(verdict.tone).toBe("bad");
    expect(verdict.headline).toBe("Removing this entry would not end their access");
    expect(verdict.detail).toContain("2 other paths");
  });

  it("says so when removing the entry really would end the access", () => {
    const verdict = reachVerdict(reach({ removing_reviewed_entries_leaves_access: false }));

    expect(verdict.tone).toBe("ok");
    expect(verdict.headline).toContain("would end their access");
  });

  it("never renders 'not established' as the reassuring answer", () => {
    // The single most important rule in this module. Null means no explanation could be
    // produced; reading it as "removing it works" would let a reviewer record a revocation
    // in a belief nothing supports.
    const verdict = reachVerdict(reach({ removing_reviewed_entries_leaves_access: null }));

    expect(verdict.tone).not.toBe("ok");
    expect(verdict.headline).not.toContain("would end their access");
    expect(verdict.detail).toContain("unverified");
  });

  it("passes on the API's reason when no explanation could be produced at all", () => {
    const verdict = reachVerdict(
      reach({ available: false, unavailable_reason: "ADG holds no directory for this target." }),
    );

    expect(verdict.detail).toBe("ADG holds no directory for this target.");
  });

  it("uses the singular for one other path", () => {
    const verdict = reachVerdict(
      reach({ removing_reviewed_entries_leaves_access: true, group_paths: 1 }),
    );

    expect(verdict.detail).toContain("1 other path.");
  });
});

describe("what a batch may cover", () => {
  it("allows one principal across many targets", () => {
    const result = bulkEligibility([
      item({ target_key: "a" }),
      item({ target_key: "b", item_id: "other" }),
    ]);

    expect(result.eligible).toBe(true);
  });

  it("allows one target across many principals", () => {
    const result = bulkEligibility([
      item({ principal_key: "alice" }),
      item({ principal_key: "bob", item_id: "other" }),
    ]);

    expect(result.eligible).toBe(true);
  });

  it("refuses many principals across many targets, and says why", () => {
    const result = bulkEligibility([
      item({ principal_key: "alice", target_key: "a" }),
      item({ principal_key: "bob", target_key: "b", item_id: "other" }),
    ]);

    expect(result.eligible).toBe(false);
    expect(result.reason).toContain("not one question");
  });

  it("refuses share entries mixed with file-system entries", () => {
    const result = bulkEligibility([
      item({ target_kind: "share" }),
      item({ target_kind: "resource", item_id: "other" }),
    ]);

    expect(result.eligible).toBe(false);
    expect(result.reason).toContain("different kinds of access-control list");
  });

  it("refuses a batch containing an item that is already decided", () => {
    const result = bulkEligibility([item(), item({ item_id: "other", status: "decided" })]);

    expect(result.eligible).toBe(false);
    expect(result.reason).toContain("supersedes a specific attestation");
  });

  it("refuses an empty selection", () => {
    expect(bulkEligibility([]).eligible).toBe(false);
  });

  it("does not guess at drift, which needs a live comparison", () => {
    // Deliberately absent: a disabled button with a wrong explanation is worse than one the
    // API refuses with the real reason. The items here carry nothing about drift and the
    // selection is allowed; the server still refuses it if any of them moved.
    const result = bulkEligibility([item(), item({ item_id: "other", target_key: "b" })]);

    expect(result.eligible).toBe(true);
  });

  it("describes what the batch is about in words", () => {
    expect(
      bulkSubject([item({ target_key: "a" }), item({ item_id: "two", target_key: "b" })]),
    ).toBe("CONTOSO\\alice on 2 targets");
    expect(
      bulkSubject([
        item({ principal_key: "alice", principal_display_name: "Alice" }),
        item({ item_id: "two", principal_key: "bob", principal_display_name: "Bob" }),
      ]),
    ).toBe("2 principals on \\\\fs01\\finance");
  });
});

describe("coverage", () => {
  it("says what a campaign did not ask about, beside its completion", () => {
    const caveat = coverageCaveat({ inherited: 412, builtin_trustee: 38 });

    expect(caveat).toContain("412 inherited entries");
    expect(caveat).toContain("38 entries naming well-known trustees");
    expect(caveat).toContain("not of the estate");
  });

  it("is silent when nothing was excluded", () => {
    expect(coverageCaveat({})).toBeNull();
    expect(coverageCaveat({ inherited: 0 })).toBeNull();
  });

  it("names an exclusion reason this build does not recognize rather than dropping it", () => {
    expect(coverageCaveat({ some_new_reason: 3 })).toContain("3 entries excluded as some_new_reason");
  });
});

describe("reviewer progress", () => {
  it("puts the overdue first, then the least far through", () => {
    const ordered = reviewersByAttention([
      reviewer({ reviewer_subject: "carol", completion: 0.9 }),
      reviewer({ reviewer_subject: "bob", completion: 0.1 }),
      reviewer({ reviewer_subject: "alice", completion: 1, overdue: true }),
    ]);

    expect(ordered.map((entry) => entry.reviewer_subject)).toEqual(["alice", "bob", "carol"]);
  });

  it("does not recompute overdue from the due date", () => {
    // The backend decides it, against the assignment's own deadline where it has one. A
    // client recomputing from `due_at` would disagree the moment those differ.
    const ordered = reviewersByAttention([
      reviewer({ reviewer_subject: "a", due_at: "2020-01-01T00:00:00Z", overdue: false }),
      reviewer({ reviewer_subject: "b", due_at: null, overdue: true }),
    ]);

    expect(ordered[0].reviewer_subject).toBe("b");
  });
});

describe("the queue", () => {
  it("leads with the fact that a campaign is overdue", () => {
    expect(queueHeadline(queueEntry({ overdue: true, pending: 3 }))).toBe(
      "Overdue — 3 items left",
    );
  });

  it("says when there is nothing left", () => {
    expect(queueHeadline(queueEntry({ pending: 0 }))).toBe("Nothing left to answer");
  });

  it("renders a deadline as a date rather than as a countdown", () => {
    // A deadline is a date somebody agreed to. "In 3 days" changes meaning depending on when
    // the page was rendered, and a cached page would quietly be wrong.
    expect(dueWording("2026-03-20T17:00:00Z", false)).toBe("Due 2026-03-20");
    expect(dueWording("2026-03-20T17:00:00Z", true)).toBe("Was due 2026-03-20");
    expect(dueWording(null, false)).toBe("No deadline");
  });
});
