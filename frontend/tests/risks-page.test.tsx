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
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
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
import { currentViewer } from "@/lib/auth/current";
import RisksPage from "@/app/risks/page";

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
