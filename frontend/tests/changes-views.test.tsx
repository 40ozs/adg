// @vitest-environment jsdom
/**
 * What the Changes components actually put on screen.
 *
 * Four assertions here are the reason the components exist rather than a plain table:
 *
 * - a first sighting says "First seen" and never "Added";
 * - the time on a row is the window, with both ends, and not the instant a collector looked;
 * - a removal's "after" column says the object was measured absent rather than being blank;
 * - a filtered list says how many changes it is not showing.
 *
 * Each of the four would look perfectly fine if it were wrong, and each would make the page
 * state something false.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  BeforeAfter,
  ChangeCard,
  ChangeSummaryStrip,
  ChangeTimeline,
  DeltaTable,
  ImpactCaveat,
  SeverityBadge,
} from "@/components/Changes";
import type { AceEditView, ChangeSummaryResponse, ChangeView } from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const WEDNESDAY = "2026-03-04T09:00:00Z";
const FRIDAY = "2026-03-06T09:00:00Z";

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
    at: FRIDAY,
    window: {
      after: WEDNESDAY,
      at_or_before: FRIDAY,
      is_exact: false,
      duration_seconds: 172_800,
    },
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
      valid_from: FRIDAY,
      last_seen_at: FRIDAY,
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

const HREFS = {
  impactHref: (item: ChangeView) => `/changes/impact?key=${item.key}`,
  timelineHref: (item: ChangeView) => `/changes?key=${item.key}`,
};

describe("a change on screen", () => {
  it("shows the window with both ends rather than the scan instant alone", () => {
    render(<ChangeCard change={change()} edits={[]} {...HREFS} />);
    const window = screen.getByText(/Some time after/);
    expect(window.textContent).toContain(WEDNESDAY);
    expect(window.textContent).toContain(FRIDAY);
  });

  it("labels the scan instant as when somebody looked", () => {
    render(<ChangeCard change={change()} edits={[]} {...HREFS} />);
    expect(screen.getByText(/when a collector looked/)).toBeTruthy();
  });

  it("never calls a first sighting an addition", () => {
    render(
      <ChangeCard
        change={change({ action: "first_observed", window: null })}
        edits={[]}
        {...HREFS}
      />,
    );
    expect(screen.getByRole("heading", { level: 3 }).textContent).toContain("First seen");
    expect(screen.getByText(/not evidence that anything was created/)).toBeTruthy();
  });

  it("says a removal was measured rather than assumed", () => {
    render(
      <ChangeCard
        change={change({ action: "removed", before: change().after, after: { ...change().after, present: false, state: null } })}
        edits={[]}
        {...HREFS}
      />,
    );
    expect(screen.getByText(/reconciled a scope/)).toBeTruthy();
  });

  it("offers the why-access-changed link", () => {
    render(<ChangeCard change={change()} edits={[]} {...HREFS} />);
    expect(screen.getByRole("link", { name: /Why access changed/ })).toBeTruthy();
  });

  it("omits that link when the caller has no impact view to offer", () => {
    render(<ChangeCard change={change()} edits={[]} impactHref={null} timelineHref={HREFS.timelineHref} />);
    expect(screen.queryByRole("link", { name: /Why access changed/ })).toBeNull();
  });

  it("warns when either side was reconstructed by the backfill", () => {
    render(<ChangeCard change={change({ reconstructed: true })} edits={[]} {...HREFS} />);
    expect(screen.getByText(/reconstructed when history was introduced/)).toBeTruthy();
  });
});

describe("the before and after table", () => {
  it("says the object was found gone rather than leaving the cell blank", () => {
    const removed = change({
      action: "removed",
      before: change().after,
      after: { ...change().after, present: false, state: null },
    });
    render(<BeforeAfter change={removed} />);
    expect(screen.getAllByText(/a scan looked and did not find it/).length).toBeGreaterThan(0);
  });

  it("says 'not recorded' for a side ADG never held", () => {
    render(<BeforeAfter change={change()} />);
    expect(screen.getAllByText("not recorded").length).toBeGreaterThan(0);
  });
});

describe("the diff view", () => {
  it("drops provenance and keeps what moved", () => {
    render(
      <DeltaTable
        deltas={[
          { field: "source_key", before: "a", after: "b", significance: "noise" },
          { field: "enabled", before: true, after: false, significance: "security" },
        ]}
      />,
    );
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "enabled" })).toBeTruthy();
    expect(within(table).queryByRole("rowheader", { name: "source_key" })).toBeNull();
  });

  it("renders nothing at all when only provenance moved", () => {
    const { container } = render(
      <DeltaTable deltas={[{ field: "source_key", before: "a", after: "b", significance: "noise" }]} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("the timeline", () => {
  it("shows the two halves of an ACL edit under one heading", () => {
    const removal = change({ action: "removed", edit: 0, key: "old" });
    const addition = change({ action: "added", edit: 0, key: "new" });
    render(<ChangeTimeline changes={[removal, addition]} edits={[EDIT]} {...HREFS} />);
    expect(screen.getByText(EDIT.summary)).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});

describe("the summary strip", () => {
  const summary: ChangeSummaryResponse = {
    window_from: WEDNESDAY,
    window_to: FRIDAY,
    total: 431,
    returned: 12,
    excluded: 419,
    highest_severity: "critical",
    reconstructed: 2,
    truncated: false,
    by_action: { added: 400, removed: 31 },
    by_significance: { security: 12 },
    by_severity: { critical: 1, info: 430 },
    by_kind: { ntfs_ace: 431 },
  };

  it("says how many changes the filter is hiding", () => {
    render(<ChangeSummaryStrip summary={summary} />);
    expect(screen.getByText(/419 of 431 changes in this window are not shown/)).toBeTruthy();
  });

  it("names the reconstructed ones separately", () => {
    render(<ChangeSummaryStrip summary={summary} />);
    expect(screen.getByText(/2 rest on a version reconstructed/)).toBeTruthy();
  });

  it("warns when its own counts are a floor", () => {
    render(<ChangeSummaryStrip summary={{ ...summary, truncated: true }} />);
    expect(screen.getByText(/floor rather than a total/)).toBeTruthy();
  });
});

describe("severity", () => {
  it("names the value rather than relying on color alone", () => {
    render(<SeverityBadge severity="critical" />);
    expect(screen.getByText("critical")).toBeTruthy();
  });
});

describe("the impact caveat", () => {
  it("refuses to let an inconclusive 'neutral' read as 'nothing changed'", () => {
    render(<ImpactCaveat direction="neutral" conclusive={false} />);
    expect(screen.getByText(/not evidence that access did not change/)).toBeTruthy();
  });

  it("stays out of the way when both answers were conclusive", () => {
    const { container } = render(<ImpactCaveat direction="neutral" conclusive />);
    expect(container.firstChild).toBeNull();
  });
});
