// @vitest-environment jsdom
/**
 * The risks page, from a stubbed API to the DOM.
 *
 * Its acceptance criteria are not about layout. They are the four things that keep an empty
 * report from reading as a clean estate, and the one that makes a finding checkable:
 *
 * * **an estate nobody has evaluated says so**, in the banner above the table, not in a
 *   tooltip — this is the single most dangerous sentence the product could accidentally say;
 * * **an incremental pass says its counts are not totals**, for the same reason;
 * * **the filter reports what it is hiding**, so a default view is not mistaken for a
 *   complete one;
 * * **a disabled rule is named**, because its silence is a decision rather than a result;
 * * **the evidence drawer shows the stored records**, not a rendering of them.
 *
 * The page is a React server component — an async function returning an element tree — so it
 * is awaited and rendered directly, with the API module stubbed at its boundary.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  FindingDetailView,
  FindingSummaryView,
  FindingsResponse,
  RiskRulesResponse,
  RiskSummaryResponse,
} from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    prefetch?: boolean;
  }) => (
    // Recorded, not dropped: a list of principal links must not prefetch every one.
    <a href={href} data-prefetch={prefetch === false ? "off" : undefined} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));

vi.mock("@/lib/api/adg", () => ({
  fetchRiskSummary: vi.fn(),
  fetchFindings: vi.fn(),
  fetchRiskRules: vi.fn(),
  fetchFinding: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { FindingTable } from "@/components/Risks";
import { currentViewer } from "@/lib/auth/current";
import RisksPage from "@/app/risks/page";
import { completeRiskSummary, riskSummary } from "./coverage-factories";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });

const SIGNED_IN = {
  status: "signed-in" as const,
  session: {
    accessToken: "token",
    expiresAt: "2099-01-01T00:00:00Z",
    mode: "development" as const,
  },
  principal: {
    subject: "auditor",
    display_name: "Auditor",
    email: null,
    source: "development" as const,
    development: true,
    roles: ["auditor"],
    capabilities: ["risks:read", "alerts:read"],
    inactive_roles: [],
    unrecognized_roles: [],
    expires_at: null,
  },
};

const RULE = {
  rule_id: "everyone_has_access",
  title: "Everyone can reach this resource",
  detects: "An entry granting a world SID.",
  matters_because: "Everybody who can reach the share can reach this.",
  remediation: {
    summary: "Remove the Everyone entry and grant the group that needs it.",
    steps: ["Identify who actually needs access.", "Replace the entry."],
    caution: "A service account may be relying on it.",
  },
  enabled: true,
  subject_kind: "resource",
  version: "1",
  severities: { full_control: "critical" },
  requires_configuration: false,
};

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
    evidence_count: 2,
    evidence_digest: "b".repeat(64),
    detail_url: "/api/v1/risks/findings/" + "a".repeat(64),
    ...overrides,
  };
}

function summary(overrides: Partial<RiskSummaryResponse> = {}): RiskSummaryResponse {
  return {
    coverage: {
      has_ever_run: true,
      evaluated_at: "2026-09-14T12:00:00Z",
      trigger: "full",
      complete: true,
      rules_run: ["everyone_has_access"],
      rules_skipped: [],
      truncation: null,
      evaluation_id: "11111111-1111-1111-1111-111111111111",
    },
    configuration: {
      version: "default",
      rules_enabled: 11,
      rules_disabled: [],
      marks_anything_sensitive: true,
    },
    severity_counts: { critical: 1, high: 0, medium: 0, low: 0, informational: 0 },
    rule_counts: { everyone_has_access: 1 },
    status_counts: { open: 1, resolved: 0 },
    total: 1,
    ...overrides,
  };
}

function page(items: FindingSummaryView[] = [finding()]): FindingsResponse {
  return {
    filters: { status: ["open"] },
    coverage: summary().coverage,
    configuration: summary().configuration,
    severity_counts: summary().severity_counts,
    rule_counts: summary().rule_counts,
    status_counts: summary().status_counts,
    items,
    page: { limit: 100, has_more: false, next_cursor: null, total: items.length },
  };
}

const RULES: RiskRulesResponse = {
  rules: [RULE],
  configuration: summary().configuration,
};

async function renderPage(params: Record<string, string | string[]> = {}): Promise<void> {
  render(await RisksPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.mocked(currentViewer).mockResolvedValue(SIGNED_IN);
  vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(summary()));
  vi.mocked(adg.fetchFindings).mockResolvedValue(ok(page()));
  vi.mocked(adg.fetchRiskRules).mockResolvedValue(ok(RULES));
  vi.mocked(adg.fetchFinding).mockReset();
});

describe("what the page says about its own coverage", () => {
  it("says the rules have never been evaluated, rather than showing an empty clean report", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(
        summary({
          coverage: {
            has_ever_run: false,
            evaluated_at: null,
            trigger: null,
            complete: false,
            rules_run: [],
            rules_skipped: [],
            truncation: null,
            evaluation_id: null,
          },
          severity_counts: { critical: 0, high: 0, medium: 0, low: 0, informational: 0 },
          rule_counts: {},
          status_counts: { open: 0, resolved: 0 },
          total: 0,
        }),
      ),
    );
    vi.mocked(adg.fetchFindings).mockResolvedValue(ok(page([])));

    await renderPage();

    expect(screen.getByText(/rules have never been evaluated/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing has looked at it yet/i)).toBeInTheDocument();
  });

  it("says an incremental pass produced counts that are not totals", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(
        summary({
          coverage: { ...summary().coverage, complete: false, trigger: "incremental" },
        }),
      ),
    );

    await renderPage();

    expect(screen.getByText(/These counts are not totals/i)).toBeInTheDocument();
  });

  it("names the rules that did not run", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(
        summary({
          coverage: {
            ...summary().coverage,
            complete: false,
            rules_skipped: ["stale_direct_ace"],
          },
        }),
      ),
    );

    await renderPage();

    expect(screen.getByText(/stale_direct_ace/)).toBeInTheDocument();
  });

  it("says so even when the news is good", async () => {
    await renderPage();

    expect(screen.getByText(/A full pass covered the estate/i)).toBeInTheDocument();
  });
});

describe("what the page says about its own filters", () => {
  it("reports how many findings the status filter is hiding", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(summary({ status_counts: { open: 1, resolved: 340 } })),
    );

    await renderPage();

    expect(screen.getByText(/340 finding\(s\) are not shown/)).toBeInTheDocument();
  });

  it("says when a rule is turned off", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(
        summary({
          configuration: {
            ...summary().configuration,
            rules_disabled: ["stale_direct_ace"],
          },
        }),
      ),
    );

    await renderPage();

    expect(screen.getByText(/turned off in this installation/)).toBeInTheDocument();
  });

  it("says when nothing is marked sensitive", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(
      ok(
        summary({
          configuration: { ...summary().configuration, marks_anything_sensitive: false },
        }),
      ),
    );

    await renderPage();

    expect(screen.getByText(/will not guess which data is sensitive/)).toBeInTheDocument();
  });

  it("does not call an empty filtered result a clean estate", async () => {
    vi.mocked(adg.fetchFindings).mockResolvedValue(ok(page([])));

    await renderPage();

    expect(screen.getByText(/not the same as a clean estate/i)).toBeInTheDocument();
  });

  it("shows every severity in the counts, including the zeros", async () => {
    // A tile list that omitted the empty ones would make a report with no high findings
    // look like a report that has no high category.
    await renderPage();

    const strip = screen.getByLabelText("Severity counts");
    for (const severity of ["critical", "high", "medium", "low", "informational"]) {
      expect(within(strip).getByText(severity)).toBeInTheDocument();
    }
  });
});

describe("the findings table", () => {
  it("shows the severity, the place and how stale the claim is", async () => {
    await renderPage();

    const row = screen.getByRole("row", { name: /Everyone can reach this resource/ });
    expect(within(row).getByText("critical")).toBeInTheDocument();
    expect(within(row).getByText("\\\\fs01\\finance")).toBeInTheDocument();
    expect(within(row).getByText(/last confirmed/)).toBeInTheDocument();
  });

  it("links each row to its evidence, carrying the reader's page with it", async () => {
    await renderPage({ trail: "abc" });

    const link = screen.getByRole("link", { name: /2 record\(s\)/ });
    expect(link.getAttribute("href")).toContain(`finding=${"a".repeat(64)}`);
    // Without the trail, opening the drawer on page three would silently return the reader
    // to page one and the row they clicked would be off screen.
    expect(link.getAttribute("href")).toContain("trail=abc");
  });
});

describe("sorting the findings table", () => {
  // Chosen so that an alphabetical sort gives a different order from the rank.
  const ROWS = [
    finding({ key: "1".repeat(64), title: "One", severity: "medium", confidence: "possible" }),
    finding({ key: "2".repeat(64), title: "Two", severity: "informational", confidence: "confirmed" }),
    finding({ key: "3".repeat(64), title: "Three", severity: "critical", confidence: "probable" }),
  ];
  const titles = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[1].textContent?.replace(/everyone_has_access$/, ""));

  it("orders severity and confidence by rank, not alphabetically", async () => {
    const user = userEvent.setup();
    render(<FindingTable findings={ROWS} drawerHref={() => "/risks"} openKey={null} />);

    await user.click(screen.getByRole("button", { name: /^Severity/ }));
    expect(titles()).toEqual(["Three", "One", "Two"]); // critical, medium, informational
    expect(screen.getByRole("columnheader", { name: /^Severity/ })).toHaveAttribute("aria-sort", "ascending");

    await user.click(screen.getByRole("button", { name: /^Confidence/ }));
    expect(titles()).toEqual(["Two", "Three", "One"]); // confirmed, probable, possible
    await user.click(screen.getByRole("button", { name: /^Confidence/ }));
    expect(titles()).toEqual(["One", "Three", "Two"]);
    expect(screen.getByRole("columnheader", { name: /^Confidence/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("says sorting orders this page, not the estate", () => {
    render(<FindingTable findings={ROWS} drawerHref={() => "/risks"} openKey={null} />);
    expect(screen.getByText(/Sorting orders the rows on this page\./)).toBeInTheDocument();
  });
});

describe("the evidence drawer", () => {
  const detail: FindingDetailView = {
    finding: finding(),
    rule: RULE,
    evidence: [
      {
        kind: "ace",
        key: "ntfs_ace|finance|everyone",
        record: { trustee_sid: "S-1-1-0", access_mask: 2032127, ace_type: "allow" },
      },
    ],
    events: [
      {
        event_type: "opened",
        occurred_at: "2026-09-01T00:00:00Z",
        evaluation_id: "11111111-1111-1111-1111-111111111111",
        rule_version: "1",
        severity: "critical",
        confidence: "confirmed",
        evidence_digest: "b".repeat(64),
        previous_evidence_digest: null,
      },
    ],
    reproduces: true,
    reproduction_note: "This finding re-derives from the evidence stored with it.",
  };

  it("shows the stored records themselves rather than a summary of them", async () => {
    // "Everyone -> Modify" is a sentence; it cannot be rebuilt into facts, and an auditor
    // asked to accept a finding on a sentence is being asked to take ADG's word for it.
    vi.mocked(adg.fetchFinding).mockResolvedValue(ok(detail));

    await renderPage({ finding: "a".repeat(64) });

    const drawer = screen.getByLabelText("Evidence");
    expect(within(drawer).getByText("S-1-1-0")).toBeInTheDocument();
    expect(within(drawer).getByText("2032127")).toBeInTheDocument();
  });

  it("says whether the finding still re-derives from its own evidence", async () => {
    vi.mocked(adg.fetchFinding).mockResolvedValue(ok(detail));

    await renderPage({ finding: "a".repeat(64) });

    expect(screen.getByText(/re-derives from the evidence stored with it/)).toBeInTheDocument();
  });

  it("shows the remediation caution as prominently as the steps", async () => {
    // The most common way an access review does damage is removing a grant a service
    // account was quietly relying on. A drawer that said what to do and not what to check
    // first is an outage waiting for a maintenance window.
    vi.mocked(adg.fetchFinding).mockResolvedValue(ok(detail));

    await renderPage({ finding: "a".repeat(64) });

    expect(screen.getByText(/Before you change anything/)).toBeInTheDocument();
    expect(screen.getByText(/service account may be relying on it/)).toBeInTheDocument();
  });

  it("leaves the list readable when the drawer cannot be fetched", async () => {
    vi.mocked(adg.fetchFinding).mockResolvedValue({
      ok: false,
      failure: {
        kind: "not_found",
        status: 404,
        message: "That finding is not there.",
        detail: "No finding.",
      },
    });

    await renderPage({ finding: "a".repeat(64) });

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(
      screen.getByRole("row", { name: /Everyone can reach this resource/ }),
    ).toBeInTheDocument();
  });
});

describe("collection coverage beside the totals (ADG-DEMO-03)", () => {
  it("does not call the counts totals while a source is unavailable", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(riskSummary()));

    await renderPage();

    const heading = screen.getByRole("heading", { name: "These counts are not exhaustive" });
    // Announced: the reading sits in a status region, not only in the visual layout.
    expect(heading.closest("[role=status]")).not.toBeNull();
    expect(screen.queryByText(/A full pass covered the estate/i)).toBeNull();
    expect(screen.queryByText(/these counts are totals/i)).toBeNull();
  });

  it("shows the coverage line, the verdict in words, and the snapshot beside the counts", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(riskSummary()));

    await renderPage();
    const line = screen.getByRole("region", { name: "Collection behind these counts" });

    expect(line.textContent).toContain(
      "Coverage: 98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence",
    );
    expect(within(line).getByText("Incomplete")).toBeInTheDocument();
    expect(within(line).getByText("Not exhaustive:")).toBeInTheDocument();
    expect(line.textContent).toContain("Collected state 3f2a9c41d07e · 300 collector runs");
  });

  it("keeps the counts beside it: a failed source did not turn its findings into zero", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(riskSummary()));

    await renderPage();
    const held = screen.getByRole("list", { name: "Findings held from older evidence" });

    expect(held.textContent).toContain("ntfs (GP-FS-03)");
    expect(held.textContent).toContain("189 open finding(s)");
    expect(held.textContent).toContain("Older evidence, from the scan of 2026-09-17");
    // A partial scope's evidence is current; it is a coverage concern, not held evidence.
    expect(held.textContent).not.toContain("GP-MIG-FS01");
  });

  it("names the incomplete scopes one disclosure away", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(riskSummary()));

    await renderPage();
    const line = screen.getByRole("region", { name: "Collection behind these counts" });
    const disclosure = within(line).getByText("Which scopes are not complete (2)");

    expect(disclosure.tagName).toBe("SUMMARY");
    expect(line.textContent).toContain("ntfs (GP-FS-03): the latest scan could not reach its target");
    expect(within(line).getByRole("link", { name: /collector status page/ })).toHaveAttribute(
      "href",
      "/collectors",
    );
  });

  it("calls the counts totals only when evaluation and collection are both complete", async () => {
    vi.mocked(adg.fetchRiskSummary).mockResolvedValue(ok(completeRiskSummary()));

    await renderPage();

    expect(screen.getByRole("heading", { name: "These counts are totals" })).toBeInTheDocument();
    const line = screen.getByRole("region", { name: "Collection behind these counts" });
    expect(within(line).getByText("Exhaustive:")).toBeInTheDocument();
    expect(within(line).getByText("Complete")).toBeInTheDocument();
  });

  it("falls back to the evaluation-only banner for a server older than the coverage block", async () => {
    await renderPage();

    expect(screen.getByText(/A full pass covered the estate/i)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Collection behind these counts" })).toBeNull();
  });
});
