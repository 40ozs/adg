// @vitest-environment jsdom
/**
 * The collector status page, from a stubbed API to the DOM.
 *
 * This is the page every empty table in ADG points at, so its acceptance criteria are not
 * about layout. They are:
 *
 * * a failure on one server is **visible** even when another succeeded — the defect Phase 6D
 *   found in the backend, asserted again here so that a future page which shows only the
 *   latest run per collector fails a test rather than quietly hiding it;
 * * the **last successful** and the **last failed** scan are both on screen, because a scope
 *   whose latest run failed still has data on screen and the page has to say how old it is;
 * * an object count of **zero** is annotated, because a collector that ran cleanly and
 *   stored nothing reports success everywhere else in the product;
 * * the error summary is **grouped**, and the widespread codes come first.
 *
 * The page is a React server component — an async function returning an element tree — so it
 * is awaited and rendered directly, with the API module stubbed at its boundary.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  CollectionOperations,
  ObjectCounts,
  RunOutcome,
  ScanRunList,
  ScopeOperations,
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
  fetchCollectionOperations: vi.fn(),
  fetchScanRuns: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import CollectorsPage from "@/app/collectors/page";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });

const SIGNED_IN = {
  status: "signed-in" as const,
  session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z" },
  principal: {
    subject: "ops",
    display_name: "Ops",
    email: null,
    source: "development" as const,
    development: true,
    roles: ["auditor"],
    capabilities: ["collectors:read"],
    inactive_roles: [],
    unrecognized_roles: [],
    expires_at: null,
  },
};

function run(overrides: Partial<RunOutcome> = {}): RunOutcome {
  return {
    run_id: "11111111-1111-1111-1111-111111111111",
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

const COUNTS: ObjectCounts = {
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

function report(overrides: Partial<CollectionOperations> = {}): CollectionOperations {
  return {
    health: "healthy",
    summary: "Collection is current for: ntfs.",
    notes: [],
    scopes: [scope()],
    counts: COUNTS,
    errors: [],
    total_errors: 0,
    ...overrides,
  };
}

const NO_RUNS: ScanRunList = {
  items: [],
  page: { limit: 25, has_more: false, next_cursor: null, total: 0 },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue(SIGNED_IN as never);
  vi.mocked(adg.fetchScanRuns).mockResolvedValue(ok(NO_RUNS));
});

async function renderPage(data: CollectionOperations) {
  vi.mocked(adg.fetchCollectionOperations).mockResolvedValue(ok(data));
  render(await CollectorsPage());
}

/** The row of the scopes table whose first cell contains `label`. */
function scopeRow(label: string): HTMLElement {
  const table = screen.getByRole("table", { name: /One row per collector and target/ });
  const cell = within(table).getByText(label);
  const row = cell.closest("tr");
  if (row === null) {
    throw new Error(`no row for ${label}`);
  }
  return row;
}

describe("a failure is never hidden by a success elsewhere", () => {
  const MIXED = report({
    health: "failed",
    summary: "A collector's most recent run failed.",
    notes: ["ntfs (FS03) has never completed a run."],
    scopes: [
      scope({ latest: run({ target: "FS01" }) }),
      scope({
        latest: run({
          target: "FS02",
          status: "partial",
          completeness: "partial",
          error_count: 2,
          shortfall: "2 object(s) could not be read.",
        }),
      }),
      scope({
        latest: run({ target: "FS03", status: "failed", completeness: "none", error_count: 1 }),
        has_ever_succeeded: false,
        last_success: null,
        last_failure: run({ target: "FS03", status: "failed", completeness: "none" }),
        note: "ntfs (FS03) has never completed a run.",
      }),
    ],
  });

  it("shows one row per server, not one per collector kind", async () => {
    await renderPage(MIXED);

    expect(scopeRow("ntfs (FS01)")).toBeTruthy();
    expect(scopeRow("ntfs (FS02)")).toBeTruthy();
    expect(scopeRow("ntfs (FS03)")).toBeTruthy();
  });

  it("says how much of the estate is complete, as a fraction", async () => {
    await renderPage(MIXED);

    expect(screen.getByText(/1 of 3 scopes are complete/)).toBeTruthy();
  });

  it("raises an alert rather than a status when something is unobserved", async () => {
    await renderPage(MIXED);

    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  it("lists what needs attention above the detail", async () => {
    await renderPage(MIXED);

    expect(screen.getByText("What needs attention")).toBeTruthy();
    // Twice on purpose: once in the summary list at the top, and once on the scope's own
    // row. The page uses the API's sentence verbatim in both places rather than wording it
    // twice, which is how the two would come to disagree.
    expect(screen.getAllByText("ntfs (FS03) has never completed a run.")).toHaveLength(2);
  });

  it("names what an incomplete run failed to deliver", async () => {
    await renderPage(MIXED);

    expect(within(scopeRow("ntfs (FS02)")).getByText(/2 object\(s\) could not be read/)).toBeTruthy();
  });
});

describe("both of the runs an operator needs", () => {
  it("shows the last success and the last failure of the same scope", async () => {
    const success = run({ run_id: "aaa", completed_at: "2026-09-13T08:00:00Z" });
    const failure = run({
      run_id: "bbb",
      status: "failed",
      completeness: "none",
      completed_at: "2026-09-14T08:00:00Z",
    });
    await renderPage(
      report({
        health: "failed",
        scopes: [
          scope({
            latest: failure,
            last_success: success,
            last_failure: failure,
            stale_success: true,
            note: "The latest ntfs (FS01) run failed.",
          }),
        ],
      }),
    );

    const row = scopeRow("ntfs (FS01)");

    expect(within(row).getByText(/2026-09-13 08:00:00 UTC/)).toBeTruthy();
    expect(within(row).getByText(/2026-09-14 08:00:00 UTC/)).toBeTruthy();
  });

  it("warns that a stale success is not the latest attempt", async () => {
    const failure = run({ run_id: "bbb", status: "failed", completeness: "none" });
    await renderPage(
      report({
        health: "failed",
        scopes: [
          scope({
            latest: failure,
            last_success: run({ run_id: "aaa" }),
            last_failure: failure,
            stale_success: true,
          }),
        ],
      }),
    );

    expect(screen.getByText(/the data on screen is from this run/i)).toBeTruthy();
  });

  it("distinguishes 'never succeeded' from 'never failed'", async () => {
    // Two absences with opposite meanings, and the worst state on the page is the first.
    await renderPage(
      report({
        health: "failed",
        scopes: [
          scope({
            latest: run({ status: "failed", completeness: "none" }),
            has_ever_succeeded: false,
            last_success: null,
            last_failure: run({ status: "failed", completeness: "none" }),
          }),
        ],
      }),
    );

    const row = scopeRow("ntfs (FS01)");

    expect(within(row).getByText("never")).toBeTruthy();
    expect(within(row).queryByText("none")).toBeNull();
  });

  it("shows 'none' where a scope has never failed", async () => {
    await renderPage(report());

    expect(within(scopeRow("ntfs (FS01)")).getByText("none")).toBeTruthy();
  });
});

describe("what is stored", () => {
  it("shows a count for every kind of object", async () => {
    await renderPage(report());

    const table = screen.getByRole("table", { name: /What the collectors actually wrote/ });

    expect(within(table).getByText("108")).toBeTruthy();
    expect(within(table).getByText("Principals")).toBeTruthy();
  });

  it("annotates a zero rather than printing it bare", async () => {
    // The number this panel exists for. A collector that ran cleanly and wrote nothing
    // reports success everywhere else in ADG; this is the only place it shows.
    await renderPage(report({ counts: { ...COUNTS, ntfs_aces: 0 } }));

    const table = screen.getByRole("table", { name: /What the collectors actually wrote/ });

    expect(within(table).getByText(/grants nobody access/)).toBeTruthy();
  });

  it("renders the panel even when the store is completely empty", async () => {
    await renderPage(
      report({
        health: "no_data",
        scopes: [],
        counts: {
          principals: 0,
          membership_edges: 0,
          servers: 0,
          shares: 0,
          share_aces: 0,
          directories: 0,
          ntfs_aces: 0,
          scan_runs: 0,
          total: 0,
        },
      }),
    );

    expect(screen.getByText("What is stored")).toBeTruthy();
    // Said where the scopes table would be, and again where the run list would be. Both
    // are places a reader might otherwise conclude "there is nothing there".
    expect(
      screen.getAllByText(/empty because nothing ran|no collector has reported/i).length,
    ).toBeGreaterThan(0);
  });
});

describe("the error summary", () => {
  it("groups by code and puts the widespread one first", async () => {
    await renderPage(
      report({
        total_errors: 92,
        errors: [
          {
            code: "path_too_long",
            count: 90,
            collectors: ["ntfs"],
            latest_occurred_at: "2026-09-14T08:00:00Z",
            sample_targets: ["\\\\FS01\\a"],
            is_widespread: false,
          },
          {
            code: "access_denied",
            count: 2,
            collectors: ["ntfs", "smb"],
            latest_occurred_at: "2026-09-14T08:00:00Z",
            sample_targets: ["\\\\FS02\\b"],
            is_widespread: true,
          },
        ],
      }),
    );

    const table = screen.getByRole("table", { name: /grouped by code/ });
    const rows = within(table).getAllByRole("row").slice(1);

    expect(within(rows[0]).getByText("access_denied")).toBeTruthy();
    expect(within(rows[0]).getByText("widespread")).toBeTruthy();
    expect(within(rows[1]).getByText("path_too_long")).toBeTruthy();
  });

  it("does not present an absence of errors as complete coverage", async () => {
    // A scope that has never run reports nothing at all, so "no errors" and "nothing
    // missed" are different statements.
    await renderPage(report());

    expect(screen.getByText(/not the same as complete coverage/)).toBeTruthy();
  });
});

describe("when the page cannot be loaded at all", () => {
  it("renders the failure rather than an empty shell", async () => {
    vi.mocked(adg.fetchCollectionOperations).mockResolvedValue({
      ok: false,
      failure: {
        kind: "unreachable",
        status: null,
        message: "The ADG API at http://localhost:8000 did not respond.",
      },
    });

    render(await CollectorsPage());

    expect(screen.getByText(/could not reach its API/i)).toBeTruthy();
  });

  it("does not claim the collectors are fine when the account may not see them", async () => {
    vi.mocked(adg.fetchCollectionOperations).mockResolvedValue({
      ok: false,
      failure: {
        kind: "forbidden",
        status: 403,
        message: "This request requires the 'collectors:read' capability.",
      },
    });

    render(await CollectorsPage());

    expect(screen.getByText(/collectors:read/)).toBeTruthy();
    expect(screen.queryByText(/scopes are current and complete/)).toBeNull();
  });
});
