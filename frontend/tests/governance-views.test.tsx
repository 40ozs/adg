// @vitest-environment jsdom
/**
 * What the review screens actually put in front of a reviewer.
 *
 * Five assertions here are the reason these components exist rather than a table of fields.
 * Each would look perfectly reasonable if it were wrong, and each would make a screen state
 * something false:
 *
 * - the drift notice prints the **API's own sentence**, so "it was removed" and "nobody has
 *   looked" cannot be collapsed by a client that rebuilt the wording from the verdict;
 * - a campaign's completion never appears without the exclusions beside it;
 * - items assigned to nobody are called out as *stuck*, not folded into "pending";
 * - a grant also held through a group says removing the entry would not end the access;
 * - the decision form is withheld with a reason that sends the reviewer to the right person.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DecisionForm } from "@/components/DecisionForm";
import {
  CampaignProgress,
  DecisionHistory,
  DriftNotice,
  DriftSummary,
  FindingsPanel,
  ReachPanel,
  ReviewerProgressTable,
} from "@/components/Governance";
import type {
  CampaignStatusResponse,
  CampaignView,
  DecisionView,
  DriftView,
  ItemContextResponse,
  ItemView,
  ReachView,
  ReviewerProgressView,
  RightsView,
} from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const RIGHTS: RightsView = {
  mask: "0x001200a9",
  value: 0x1200a9,
  layer: "ntfs",
  label: "Read & Execute",
  primary: "read",
  categories: ["read"],
  is_exact: true,
  extra_rights: [],
  escalation_rights: [],
  unrecognized_bits: null,
  indeterminate: false,
};

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

function campaign(overrides: Partial<CampaignView> = {}): CampaignView {
  return {
    campaign_id: "c1",
    name: "Finance recertification",
    description: null,
    focus: "resource",
    status: "active",
    baseline_at: "2026-03-02T09:05:00Z",
    due_at: null,
    scopes: [{ kind: "share", key: "fs01|finance" }],
    options: { include_inherited: false, include_builtin: false, include_deny: true },
    comment_requirement: "standard",
    item_count: 47,
    excluded_counts: {},
    snapshot_digest: null,
    generated_at: "2026-03-09T09:00:00Z",
    activated_at: "2026-03-09T09:05:00Z",
    closed_at: null,
    closed_by_subject: null,
    created_by_subject: "gov-admin",
    created_at: "2026-03-09T08:00:00Z",
    ...overrides,
  };
}

function status(overrides: Partial<CampaignStatusResponse> = {}): CampaignStatusResponse {
  return {
    campaign: campaign(),
    total_items: 47,
    pending_items: 0,
    decided_items: 47,
    unassigned_items: 0,
    completion: 1,
    decisions_by_kind: { certify: 47 },
    overdue: false,
    reviewers: [],
    overdue_reviewers: 0,
    late_decisions: 0,
    audit_head: "d".repeat(64),
    ...overrides,
  };
}

function reviewer(overrides: Partial<ReviewerProgressView> = {}): ReviewerProgressView {
  return {
    assignment_id: "a1",
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

function item(overrides: Partial<ItemView> = {}): ItemView {
  return {
    item_id: "i1",
    campaign_id: "c1",
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
    assignment_id: "a1",
    current_decision_id: null,
    decided_at: null,
    created_at: "2026-03-09T09:00:00Z",
    ...overrides,
  };
}

function context(reach: Partial<ReachView>): ItemContextResponse {
  return {
    item: item(),
    campaign_id: "c1",
    comment_requirement: "standard",
    drift: drift(),
    baseline_access: {
      at: "2026-03-02T09:05:00Z",
      available: true,
      unavailable_reason: null,
      has_access: true,
      rights: RIGHTS,
      certainty: "observed",
      limiting_layer: "ntfs",
    },
    current_access: {
      at: "2026-03-16T09:00:00Z",
      available: true,
      unavailable_reason: null,
      has_access: true,
      rights: RIGHTS,
      certainty: "certain",
      limiting_layer: "ntfs",
    },
    reach: {
      available: true,
      unavailable_reason: null,
      direct_paths: 1,
      group_paths: 0,
      routes: [],
      removals: [],
      removing_reviewed_entries_leaves_access: false,
      truncated: false,
      ...reach,
    },
    resolved_resource_key: "\\\\fs01\\finance",
    findings: [],
    findings_truncated: false,
    changes: [],
    changes_truncated: false,
  };
}

describe("the drift notice", () => {
  it("prints the sentence the API sent rather than rebuilding it", () => {
    // The whole reason the summary travels on the wire. A client that reassembled it from
    // the verdict and the counts would be a second implementation of the one distinction
    // this product must not blur.
    const summary =
      "This grant no longer exists: the target has been read since the baseline and no " +
      "entry names this principal.";

    render(<DriftNotice drift={drift({ verdict: "removed", summary })} />);

    expect(screen.getByText(summary)).toBeTruthy();
  });

  it("says the target itself is gone when it is, rather than 'the grant was removed'", () => {
    render(
      <DriftNotice drift={drift({ verdict: "removed", target_present: false })} />,
    );

    expect(screen.getByRole("heading").textContent).toBe("The target no longer exists");
  });

  it("does not present an unanswerable comparison as an all-clear", () => {
    render(<DriftNotice drift={drift({ verdict: "unobserved" })} />);

    expect(screen.getByRole("heading").textContent).toContain("cannot say");
  });

  it("warns when the comparison is against a target nothing has rescanned", () => {
    render(<DriftNotice drift={drift({ target_certainty: "inferred" })} />);

    expect(
      screen.getByText(/compares the baseline against the last scan/i),
    ).toBeTruthy();
  });

  it("names what moved in words rather than in field names", () => {
    render(
      <DriftNotice
        drift={drift({
          verdict: "modified",
          changes: [
            {
              kind: "changed",
              ace_key: "entry-1",
              fields: ["access_mask"],
              field_labels: ["rights"],
              before: null,
              after: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByRole("cell", { name: "rights" })).toBeTruthy();
  });
});

describe("the campaign drift banner", () => {
  it("says how much of the campaign it actually compared", () => {
    // "Nothing has changed" must never be readable as more than "nothing in the part that
    // was checked".
    render(
      <DriftSummary
        counts={{ unchanged: 100, modified: 0, removed: 0, unobserved: 0 }}
        covered={100}
        total={4000}
        campaignId="c1"
      />,
    );

    expect(screen.getByText(/100 of 4,000 items compared/)).toBeTruthy();
  });
});

describe("campaign progress", () => {
  it("never shows a completion figure without what the campaign left out", () => {
    render(
      <CampaignProgress
        status={status({
          campaign: campaign({ excluded_counts: { inherited: 412, builtin_trustee: 38 } }),
        })}
      />,
    );

    expect(screen.getByText(/47 of 47 decided/)).toBeTruthy();
    expect(screen.getByText(/412 inherited entries/)).toBeTruthy();
    expect(screen.getByText(/not of the estate/)).toBeTruthy();
  });

  it("calls a campaign with unassigned items stuck rather than slow", () => {
    render(<CampaignProgress status={status({ unassigned_items: 3000, completion: 0 })} />);

    expect(screen.getByText(/can never be decided/)).toBeTruthy();
    expect(screen.getByText(/stuck, not slow/)).toBeTruthy();
  });

  it("reports late decisions separately from being overdue", () => {
    render(<CampaignProgress status={status({ late_decisions: 12, overdue: false })} />);

    expect(screen.getByText(/12 decisions were recorded after their/)).toBeTruthy();
  });
});

describe("the reviewer table", () => {
  it("says plainly when nobody has been asked", () => {
    render(<ReviewerProgressTable reviewers={[]} />);

    expect(screen.getByText(/Nobody has been asked to review this campaign/)).toBeTruthy();
  });

  it("marks who is overdue", () => {
    render(
      <ReviewerProgressTable
        reviewers={[reviewer({ overdue: true, due_at: "2026-03-10T00:00:00Z" })]}
      />,
    );

    const row = screen.getByRole("row", { name: /Alice/ });
    expect(within(row).getByText(/overdue/)).toBeTruthy();
    expect(within(row).getByText("Was due 2026-03-10")).toBeTruthy();
  });
});

describe("the reach panel", () => {
  it("warns loudly when revoking the entry would not end the access", () => {
    render(
      <ReachPanel
        context={context({ removing_reviewed_entries_leaves_access: true, group_paths: 2 })}
      />,
    );

    expect(
      screen.getByText(/Removing this entry would not end their access/),
    ).toBeTruthy();
  });

  it("confirms when it would", () => {
    render(<ReachPanel context={context({ removing_reviewed_entries_leaves_access: false })} />);

    expect(screen.getByText(/Removing this entry would end their access/)).toBeTruthy();
  });

  it("does not claim a revocation would work when nothing established it", () => {
    render(<ReachPanel context={context({ removing_reviewed_entries_leaves_access: null })} />);

    expect(screen.queryByText(/would end their access/)).toBeNull();
    expect(screen.getByText(/Treat a revocation here as unverified/)).toBeTruthy();
  });

  it("says a per-entry removal analysis is per entry", () => {
    render(
      <ReachPanel
        context={context({
          removals: [
            {
              ace_key: "entry-1",
              rights_removed: RIGHTS,
              rights_after: RIGHTS,
              revokes_all_access: false,
              changes_nothing: false,
              alternate_paths: 1,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText(/one entry at a time/)).toBeTruthy();
  });
});

describe("the findings panel", () => {
  it("says an empty list is what the engine holds, not that nothing is wrong", () => {
    render(<FindingsPanel findings={[]} truncated={false} />);

    expect(
      screen.getByText(/not a statement that nothing is wrong/),
    ).toBeTruthy();
  });

  it("says how a finding relates to this item", () => {
    render(
      <FindingsPanel
        truncated={false}
        findings={[
          {
            finding_key: "f1",
            rule_id: "everyone_full_control",
            status: "open",
            severity: "high",
            band: "broad",
            confidence: "measured",
            relation: "access",
            detected_at: "2026-03-10T09:00:00Z",
            first_detected_at: "2026-03-02T09:00:00Z",
            resource_key: "\\\\fs01\\finance",
            share_key: null,
            principal_key: "S-1-5-21-1-2-3-1001",
            detail: {},
          },
        ]}
      />,
    );

    expect(screen.getByText(/About this principal on this target/)).toBeTruthy();
  });
});

describe("the decision form", () => {
  it("is withheld from somebody who may read a review and not answer it", () => {
    render(
      <DecisionForm
        itemId="i1"
        commentRequirement="standard"
        readOnly={{ reason: "You hold governance:read but not governance:review." }}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/not governance:review/)).toBeTruthy();
  });

  it("offers all five answers with what each one means", () => {
    render(<DecisionForm itemId="i1" commentRequirement="standard" readOnly={null} />);

    for (const label of [
      "Approve",
      "Propose revoke",
      "Propose narrower rights",
      "Needs investigation",
      "Not mine to judge",
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy();
    }
  });

  it("shows the comment box before it is needed, marked optional for an approval", () => {
    // A box that appeared when you picked "revoke" would teach reviewers that explaining
    // themselves is an exception.
    render(<DecisionForm itemId="i1" commentRequirement="standard" readOnly={null} />);

    expect(screen.getByLabelText(/Comment \(optional\)/)).toBeTruthy();
  });

  it("marks the comment required when the campaign asks for one on every decision", () => {
    render(<DecisionForm itemId="i1" commentRequirement="always" readOnly={null} />);

    expect(screen.getByLabelText(/Comment \(required\)/)).toBeTruthy();
    expect(screen.getByText(/approvals included/)).toBeTruthy();
  });

  it("says plainly what a decision is about", () => {
    render(<DecisionForm itemId="i1" commentRequirement="standard" readOnly={null} />);

    expect(screen.getByText(/not about whatever the grant is now/)).toBeTruthy();
  });
});

describe("the decision history", () => {
  it("keeps a superseded decision and marks it", () => {
    const decisions: DecisionView[] = [
      {
        decision_id: "d1",
        item_id: "i1",
        campaign_id: "c1",
        decision: "certify",
        rationale: null,
        decided_by_subject: "alice",
        decided_by_display_name: "Alice",
        decided_at: "2026-03-03T09:00:00Z",
        decided_late: false,
        supersedes_decision_id: null,
        superseded_at: "2026-03-09T09:00:00Z",
        superseded_by_decision_id: "d2",
        current: false,
      },
      {
        decision_id: "d2",
        item_id: "i1",
        campaign_id: "c1",
        decision: "revoke",
        rationale: "Alice moved teams in February.",
        decided_by_subject: "alice",
        decided_by_display_name: "Alice",
        decided_at: "2026-03-09T09:00:00Z",
        decided_late: true,
        supersedes_decision_id: "d1",
        superseded_at: null,
        superseded_by_decision_id: null,
        current: true,
      },
    ];

    render(<DecisionHistory decisions={decisions} />);

    expect(screen.getByText("superseded")).toBeTruthy();
    expect(screen.getByText("late")).toBeTruthy();
    expect(screen.getByText("Alice moved teams in February.")).toBeTruthy();
  });
});
