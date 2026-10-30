// @vitest-environment jsdom
/**
 * The Overview (ADG-UX-06), from a stubbed API to the DOM.
 *
 * The page is a starting point, and its failure mode is the one this product exists to avoid:
 * a count read as a total when nothing complete stands behind it. So most of what is asserted
 * here is about keeping "0" from meaning "clean" — an unevaluated estate, a failed collector,
 * a partial evaluation and a failed request all read as what they are — and about every count
 * being a link to the records behind it.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  ChangeSummaryResponse,
  ChangeView,
  ChangesResponse,
  CollectionStatus,
  QueueResponse,
  RiskSummaryResponse,
  RuntimeInfo,
} from "@/lib/contracts";
import { NAV_ITEMS } from "@/lib/nav";
import { completeRiskSummary, riskSummary } from "./coverage-factories";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));

vi.mock("@/lib/api/adg", () => ({
  fetchCollectionStatus: vi.fn(),
  fetchRuntime: vi.fn(),
  fetchRiskSummary: vi.fn(),
  fetchRiskRules: vi.fn(),
  fetchChangeSummary: vi.fn(),
  fetchChanges: vi.fn(),
  fetchReviewerQueue: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import OverviewPage from "@/app/page";
import { sectionHref } from "@/components/Overview";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });
const failed = <T,>(): ApiResult<T> => ({
  ok: false,
  failure: { kind: "unreachable", status: null, message: "The API did not answer." },
});

const EVERYTHING = [
  "risks:read",
  "changes:read",
  "governance:read",
  "collectors:read",
  "resources:read",
];

function viewer(capabilities: string[]) {
  return {
    status: "signed-in" as const,
    session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z", mode: "development" as const },
    principal: {
      subject: "ops",
      display_name: "Ops",
      email: null,
      source: "development" as const,
      development: true,
      roles: ["admin"],
      capabilities,
      inactive_roles: [],
      unrecognized_roles: [],
      expires_at: null,
    },
  };
}

const HEALTHY: CollectionStatus = {
  health: "healthy",
  summary: "Collection is current for: ntfs, directory.",
  concerns: [],
  collectors: [
    scope("directory", null, true),
    scope("ntfs", "FS01", true),
  ],
};

const FAILED: CollectionStatus = {
  health: "failed",
  summary: "A collector's most recent run failed.",
  concerns: ["The most recent ntfs (FS03) run failed; its scope is unobserved."],
  collectors: [scope("directory", null, true), scope("ntfs", "FS03", false)],
};

function scope(collector: string, target: string | null, trustworthy: boolean) {
  return {
    collector,
    status: trustworthy ? "succeeded" : "failed",
    started_at: "2026-10-30T05:00:00Z",
    completed_at: "2026-10-30T05:20:00Z",
    error_count: 0,
    target,
    downgrade_reason: null,
    trustworthy,
    concern: null,
  };
}

const PRODUCTION: RuntimeInfo = {
  runtime_mode: "production",
  data_source: "collectors",
  synthetic: false,
  dataset: null,
  notice: null,
};

const CHANGES: ChangeSummaryResponse = {
  window_from: "2026-10-23T00:00:00Z",
  window_to: "2026-10-30T00:00:00Z",
  total: 14,
  returned: 5,
  excluded: 9,
  highest_severity: "high",
  reconstructed: 0,
  truncated: false,
  by_action: { added: 3, removed: 2, first_observed: 9 },
  by_significance: { security: 5, metadata: 9 },
  by_severity: { high: 1, low: 4 },
  by_kind: { ntfs_ace: 5 },
};

function change(over: Partial<ChangeView> = {}): ChangeView {
  return {
    kind: "ntfs_ace",
    key: "\\\\fs01\\finance|S-1-1-0",
    action: "added",
    significance: "security",
    direction: "broadened",
    severity: "high",
    reasons: ["Everyone was granted access."],
    rule_ids: [],
    at: "2026-10-29T05:00:00Z",
    window: null,
    reconstructed: false,
    subject: { container_kind: null, container_key: null, related_kind: null, related_key: null },
    before: null,
    after: {
      present: true,
      valid_from: "2026-10-29T05:00:00Z",
      last_seen_at: "2026-10-29T05:00:00Z",
      valid_to: null,
      origin: "observed",
      opened_by_run_id: "r",
      last_seen_run_id: "r",
      state: null,
    },
    deltas: [],
    edit: null,
    ...over,
  };
}

const FEED: ChangesResponse = {
  filters: {
    window_from: CHANGES.window_from,
    window_to: CHANGES.window_to,
    scope_target: null,
    scope_key: null,
    kinds: null,
    actions: ["added", "modified", "removed"],
    significance: ["security", "undetermined"],
    min_severity: "info",
  },
  changes: [
    change(),
    change({ key: "a", reasons: ["Second."] }),
    change({ key: "b", reasons: ["Third."] }),
    change({ key: "c", reasons: ["Fourth, not shown."] }),
  ],
  edits: [],
  page: { limit: 6, has_more: false, next_cursor: null, total: null },
  scanned: 4,
  scan_exhausted: false,
};

const QUEUE: QueueResponse = {
  subject: "ops",
  total_pending: 8,
  overdue_campaigns: 1,
  entries: [
    {
      campaign_id: "c1",
      name: "Finance share review",
      focus: "share",
      status: "open",
      baseline_at: "2026-10-01T00:00:00Z",
      due_at: "2026-10-15T00:00:00Z",
      assigned: 10,
      decided: 4,
      pending: 6,
      overdue: true,
    },
    {
      campaign_id: "c2",
      name: "Payroll group review",
      focus: "group",
      status: "open",
      baseline_at: "2026-10-01T00:00:00Z",
      due_at: null,
      assigned: 2,
      decided: 0,
      pending: 2,
      overdue: false,
    },
  ],
};

function stub({
  capabilities = EVERYTHING,
  status = ok(HEALTHY),
  runtime = ok(PRODUCTION),
  risk = ok(completeRiskSummary()),
  changes = ok(CHANGES),
  feed = ok(FEED),
  queue = ok(QUEUE),
}: {
  capabilities?: string[];
  status?: ApiResult<CollectionStatus>;
  runtime?: ApiResult<RuntimeInfo>;
  risk?: ApiResult<RiskSummaryResponse>;
  changes?: ApiResult<ChangeSummaryResponse>;
  feed?: ApiResult<ChangesResponse>;
  queue?: ApiResult<QueueResponse>;
} = {}) {
  vi.mocked(currentViewer).mockResolvedValue(viewer(capabilities) as never);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(status);
  vi.mocked(adg.fetchRuntime).mockResolvedValue(runtime);
  vi.mocked(adg.fetchRiskSummary).mockResolvedValue(risk);
  vi.mocked(adg.fetchRiskRules).mockResolvedValue(
    ok({
      rules: [
        {
          rule_id: "nested_privileged_membership",
          title: "Nested privileged membership",
        } as never,
      ],
      configuration: completeRiskSummary().configuration,
    }),
  );
  vi.mocked(adg.fetchChangeSummary).mockResolvedValue(changes);
  vi.mocked(adg.fetchChanges).mockResolvedValue(feed);
  vi.mocked(adg.fetchReviewerQueue).mockResolvedValue(queue);
}

async function renderOverview() {
  render(await OverviewPage());
}

function section(name: string): HTMLElement {
  return screen.getByRole("region", { name });
}

const FINDINGS = sectionHref("risks");

beforeEach(() => {
  vi.clearAllMocks();
});

describe("section routes", () => {
  it("come from the navigation, so a route decision there reaches the Overview", () => {
    for (const id of ["risks", "changes", "governance", "collectors"]) {
      expect(sectionHref(id)).toBe(NAV_ITEMS.find((item) => item.id === id)?.href);
    }
  });
});

describe("Needs attention", () => {
  it("links each severity count to the findings view filtered by it", async () => {
    stub();
    await renderOverview();
    const attention = section("Needs attention");
    expect(within(attention).getByRole("link", { name: "3 critical findings" })).toHaveAttribute(
      "href",
      `${FINDINGS}?severity=critical`,
    );
    expect(within(attention).getByRole("link", { name: "8 high findings" })).toHaveAttribute(
      "href",
      `${FINDINGS}?severity=high`,
    );
    expect(
      within(attention).getByRole("link", { name: "9,386 open findings in all" }),
    ).toHaveAttribute("href", FINDINGS);
  });

  it("names the most common rules, each a link to its findings", async () => {
    stub({
      risk: ok({ ...completeRiskSummary(), rule_counts: { nested_privileged_membership: 5, other_rule: 2 } }),
    });
    await renderOverview();
    const common = screen.getByRole("list", { name: "Most common open findings" });
    expect(
      within(common).getByRole("link", { name: "Nested privileged membership" }),
    ).toHaveAttribute("href", `${FINDINGS}?rule=nested_privileged_membership`);
    expect(within(common).getByRole("link", { name: "other_rule" })).toBeInTheDocument();
  });

  it("says counts are not totals when the evaluation or collection is incomplete", async () => {
    stub({ risk: ok(riskSummary()) });
    await renderOverview();
    const attention = section("Needs attention");
    expect(within(attention).getByText("Not a total")).toBeInTheDocument();
    expect(within(attention).getByText(/These counts are not exhaustive/)).toBeInTheDocument();
  });

  it("calls zero findings an answer only after a complete evaluation", async () => {
    stub({ risk: ok({ ...completeRiskSummary(), total: 0, severity_counts: {} }) });
    await renderOverview();
    expect(
      within(section("Needs attention")).getByText(
        "No open findings in the latest completed evaluation.",
      ),
    ).toBeInTheDocument();
  });

  it("never reads zero findings over an incomplete pass as clean", async () => {
    stub({ risk: ok({ ...riskSummary(), total: 0, severity_counts: {} }) });
    await renderOverview();
    const attention = section("Needs attention");
    expect(
      within(attention).getByText("No open findings were returned, but that is not a complete answer."),
    ).toBeInTheDocument();
    expect(within(attention).queryByText(/latest completed evaluation/)).not.toBeInTheDocument();
  });

  it("shows no counts at all when the rules have never been evaluated", async () => {
    const never = riskSummary();
    stub({
      risk: ok({
        ...never,
        coverage: { ...never.coverage, has_ever_run: false, evaluated_at: null, complete: false },
        interpretation: { ...never.interpretation!, reading: "not_evaluated" },
        total: 0,
        severity_counts: {},
      }),
    });
    await renderOverview();
    const attention = section("Needs attention");
    expect(within(attention).getByText("Findings have not been evaluated yet.")).toBeInTheDocument();
    expect(within(attention).queryByText(/^0/)).not.toBeInTheDocument();
    expect(within(attention).queryByRole("link", { name: /critical/ })).not.toBeInTheDocument();
  });

  it("shows the API failure rather than a zero", async () => {
    stub({ risk: failed() });
    await renderOverview();
    const attention = section("Needs attention");
    expect(within(attention).getByRole("alert")).toHaveTextContent("ADG could not reach its API");
    expect(within(attention).queryByRole("link", { name: /critical/ })).not.toBeInTheDocument();
  });

  it("is not rendered for an account that cannot read findings", async () => {
    stub({ capabilities: ["changes:read"] });
    await renderOverview();
    expect(screen.queryByRole("region", { name: "Needs attention" })).not.toBeInTheDocument();
    expect(adg.fetchRiskSummary).not.toHaveBeenCalled();
  });
});

describe("Recent changes", () => {
  it("counts the default view of the last seven days and links to the same window", async () => {
    stub();
    await renderOverview();
    const changes = section("Recent changes");
    expect(within(changes).getByText("5 permission or unclassified changes")).toBeInTheDocument();
    expect(within(changes).getByText(/9 more changes .* are outside that view/)).toBeInTheDocument();
    expect(
      within(changes).getByRole("link", { name: "View changes from the last 7 days" }),
    ).toHaveAttribute("href", `${sectionHref("changes")}?days=7`);
  });

  it("names at most three recent changes, each linking to that object's history", async () => {
    stub();
    await renderOverview();
    const list = screen.getByRole("list", { name: "Most recent changes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByRole("link", { name: "Everyone was granted access." })).toHaveAttribute(
      "href",
      `${sectionHref("changes")}?kind=ntfs_ace&key=${encodeURIComponent("\\\\fs01\\finance|S-1-1-0")}`,
    );
    expect(within(list).queryByText("Fourth, not shown.")).not.toBeInTheDocument();
  });

  it("reads an empty window against collection, not as a quiet week", async () => {
    stub({
      status: ok(FAILED),
      changes: ok({ ...CHANGES, total: 0, returned: 0, excluded: 0, highest_severity: null }),
      feed: ok({ ...FEED, changes: [] }),
    });
    await renderOverview();
    expect(within(section("Recent changes")).getByRole("alert")).toHaveTextContent(
      "a collector failed",
    );
  });

  it("is not requested for an account that cannot read changes", async () => {
    stub({ capabilities: ["risks:read"] });
    await renderOverview();
    expect(screen.queryByRole("region", { name: "Recent changes" })).not.toBeInTheDocument();
    expect(adg.fetchChanges).not.toHaveBeenCalled();
  });
});

describe("Reviews", () => {
  it("shows what is assigned to this reviewer and which campaigns are past due", async () => {
    stub();
    await renderOverview();
    const reviews = section("Reviews");
    expect(reviews).toHaveTextContent("8 items assigned to you across 2 campaigns — 1 campaign past due.");
    expect(within(reviews).getByRole("link", { name: "Finance share review" })).toHaveAttribute(
      "href",
      `${sectionHref("governance")}/campaigns/c1?mine=1`,
    );
    expect(within(reviews).getByText(/Was due 2026-10-15/)).toBeInTheDocument();
    expect(within(reviews).getByRole("link", { name: "Open my review queue" })).toBeInTheDocument();
  });

  it("says nothing is assigned, rather than blaming collection, for an empty queue", async () => {
    stub({ queue: ok({ ...QUEUE, entries: [], total_pending: 0, overdue_campaigns: 0 }) });
    await renderOverview();
    const reviews = section("Reviews");
    expect(within(reviews).getByText(/Nothing is assigned to you/)).toBeInTheDocument();
    expect(within(reviews).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(reviews).getByRole("link", { name: "See review campaigns" })).toBeInTheDocument();
  });

  it("is not rendered without governance:read", async () => {
    stub({ capabilities: ["risks:read", "changes:read"] });
    await renderOverview();
    expect(screen.queryByRole("region", { name: "Reviews" })).not.toBeInTheDocument();
    expect(adg.fetchReviewerQueue).not.toHaveBeenCalled();
  });
});

describe("Data health", () => {
  it("states a complete collection and a total evaluation, after the other sections", async () => {
    stub();
    await renderOverview();
    const health = section("Data health");
    expect(within(health).getByText("Complete")).toBeInTheDocument();
    expect(health).toHaveTextContent("2 of 2 collection scopes have a latest run");
    expect(within(health).getByText("Total")).toBeInTheDocument();
    expect(within(health).getByRole("link", { name: "Check collection status" })).toHaveAttribute(
      "href",
      sectionHref("collectors"),
    );
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings.indexOf("Data health")).toBeGreaterThan(headings.indexOf("Needs attention"));
  });

  it("moves to the top and names the concern when a collector failed", async () => {
    stub({ status: ok(FAILED) });
    await renderOverview();
    const health = section("Data health");
    expect(within(health).getByText("Failed")).toBeInTheDocument();
    expect(health).toHaveTextContent("1 of 2 collection scopes");
    expect(within(health).getByText(/FS03\) run failed/)).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings[0]).toBe("Data health");
  });

  it("moves to the top when the evaluation is not a total, even over healthy collection", async () => {
    stub({ risk: ok(riskSummary()) });
    await renderOverview();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings[0]).toBe("Data health");
    expect(within(section("Data health")).getByText("Not a total")).toBeInTheDocument();
  });

  it("says nothing has been collected or evaluated, without a zero", async () => {
    const never = riskSummary();
    stub({
      status: ok({ health: "no_data", summary: "No collector has reported.", concerns: [], collectors: [] }),
      risk: ok({
        ...never,
        coverage: { ...never.coverage, has_ever_run: false, evaluated_at: null },
        interpretation: { ...never.interpretation!, reading: "not_evaluated" },
      }),
    });
    await renderOverview();
    const health = section("Data health");
    expect(within(health).getByText("Never collected")).toBeInTheDocument();
    expect(within(health).getByText(/The\s+rules have never been evaluated/)).toBeInTheDocument();
    expect(health).not.toHaveTextContent("0 of 0");
  });

  it("shows the request failure and still leads with it", async () => {
    stub({ status: failed() });
    await renderOverview();
    expect(within(section("Data health")).getByRole("alert")).toHaveTextContent(
      "ADG could not reach its API",
    );
    expect(screen.getAllByRole("heading", { level: 2 })[0]).toHaveTextContent("Data health");
  });

  it("offers the collector page only to an account that can open it", async () => {
    stub({ capabilities: ["risks:read"] });
    await renderOverview();
    expect(
      within(section("Data health")).queryByRole("link", { name: "Check collection status" }),
    ).not.toBeInTheDocument();
  });

  it("says the data is synthetic in a demo deployment", async () => {
    stub({
      runtime: ok({
        runtime_mode: "demo",
        data_source: "synthetic",
        synthetic: true,
        dataset: { company_name: "Genesee Press" } as never,
        notice: null,
      }),
    });
    await renderOverview();
    expect(section("Data health")).toHaveTextContent(
      "Synthetic demo data for Genesee Press. No production directory or file server is connected.",
    );
  });

  it("says nothing about a data source in production", async () => {
    stub();
    await renderOverview();
    expect(section("Data health")).not.toHaveTextContent("Synthetic");
  });
});

describe("existing Overview behavior", () => {
  it("keeps the sections list and the read-only statement", async () => {
    stub({ capabilities: ["resources:read"] });
    await renderOverview();
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resources" })).toHaveAttribute("href", "/resources");
    expect(screen.getByRole("heading", { name: "What ADG will not do" })).toBeInTheDocument();
  });

  it("renders the signed-out notice without calling the API", async () => {
    vi.mocked(currentViewer).mockResolvedValue({ status: "signed-out" } as never);
    render(await OverviewPage());
    expect(adg.fetchCollectionStatus).not.toHaveBeenCalled();
  });
});
