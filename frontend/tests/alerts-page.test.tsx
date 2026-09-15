// @vitest-environment jsdom
/**
 * The alerts page, from a stubbed API to the DOM.
 *
 * An alert feed's failure mode is that it goes quiet and nobody can tell whether the estate
 * did or the pipeline did. Everything asserted here is about keeping those apart:
 *
 * * **an abandoned delivery is loud**, because it means somebody was meant to be told and
 *   was not, and it does not clear itself;
 * * **a stale queue names the command that drains it**, because a queue nobody drains grows
 *   quietly while the alerts sit in it;
 * * **an installation with no destination says so**, because such a queue only grows and no
 *   other field would explain why;
 * * **an empty feed points at the watches**, because with none configured three of the four
 *   triggers cannot fire at all;
 * * **suppressed occurrences are visible** on the alert, with their reason.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  AlertDetailResponse,
  AlertQueueResponse,
  AlertView,
  AlertsResponse,
  WatchesResponse,
} from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));

vi.mock("@/lib/api/adg", () => ({
  fetchAlerts: vi.fn(),
  fetchAlertQueue: vi.fn(),
  fetchWatches: vi.fn(),
  fetchAlert: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import AlertsPage from "@/app/risks/alerts/page";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });

function viewer(capabilities: string[]) {
  return {
    status: "signed-in" as const,
    session: {
      accessToken: "token",
      expiresAt: "2099-01-01T00:00:00Z",
      mode: "development" as const,
    },
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

function alert(overrides: Partial<AlertView> = {}): AlertView {
  return {
    alert_key: "a".repeat(64),
    trigger: "watched_resource_acl_changed",
    trigger_description: "An entry on a place you are watching moved.",
    lifecycle: "transient",
    status: "open",
    summary: "The access control list of Finance changed.",
    watch_id: "11111111-1111-1111-1111-111111111111",
    watch_label: "Finance directory",
    resource_key: "\\\\fs01\\finance",
    share_key: null,
    principal_key: null,
    first_raised_at: "2026-09-14T09:00:00Z",
    last_raised_at: "2026-09-14T09:05:00Z",
    last_notified_at: "2026-09-14T09:00:00Z",
    resolved_at: null,
    occurrence_count: 1,
    suppressed_total: 0,
    suppressed_since_notice: 0,
    payload: {},
    detail_url: "/api/v1/alerts/" + "a".repeat(64),
    ...overrides,
  };
}

function feed(items: AlertView[] = [alert()]): AlertsResponse {
  return {
    filters: {},
    items,
    status_counts: { open: items.length, resolved: 0 },
    trigger_counts: {
      watched_resource_acl_changed: items.length,
      watched_group_membership_changed: 0,
      watched_access_expanded: 0,
      critical_risk_finding_opened: 0,
    },
    page: { limit: 100, has_more: false, next_cursor: null, total: items.length },
  };
}

function queue(overrides: Partial<AlertQueueResponse> = {}): AlertQueueResponse {
  return {
    depth: { pending: 0, delivered: 3, failed: 0, abandoned: 0 },
    stale: 0,
    abandoned: 0,
    oldest_pending_at: null,
    policy: ["Alert policy version 'default'.", "  sink 'operator-log' (log): enabled"],
    ...overrides,
  };
}

function watches(overrides: Partial<WatchesResponse> = {}): WatchesResponse {
  return {
    watches: [],
    kinds: [
      {
        kind: "resource",
        triggers: ["watched_resource_acl_changed", "watched_access_expanded"],
        covers: "One directory, by its UNC path.",
      },
      {
        kind: "group",
        triggers: ["watched_group_membership_changed"],
        covers: "One security group.",
      },
    ],
    min_cooldown_seconds: 60,
    max_cooldown_seconds: 86_400,
    default_cooldown_seconds: 900,
    ...overrides,
  };
}

async function renderPage(params: Record<string, string | string[]> = {}): Promise<void> {
  render(await AlertsPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.mocked(currentViewer).mockResolvedValue(viewer(["alerts:read", "alerts:manage"]));
  vi.mocked(adg.fetchAlerts).mockResolvedValue(ok(feed()));
  vi.mocked(adg.fetchAlertQueue).mockResolvedValue(ok(queue()));
  vi.mocked(adg.fetchWatches).mockResolvedValue(ok(watches()));
  vi.mocked(adg.fetchAlert).mockReset();
});

describe("what the page says about the pipeline", () => {
  it("leads with an abandoned delivery, because it does not clear itself", async () => {
    vi.mocked(adg.fetchAlertQueue).mockResolvedValue(
      ok(queue({ abandoned: 2, depth: { abandoned: 2 } })),
    );

    await renderPage();

    expect(screen.getByText(/2 alert\(s\) were never delivered/)).toBeInTheDocument();
    expect(screen.getByText(/meant to receive and did not/)).toBeInTheDocument();
  });

  it("names the command that drains a stale queue", async () => {
    vi.mocked(adg.fetchAlertQueue).mockResolvedValue(
      ok(queue({ stale: 7, depth: { pending: 7 } })),
    );

    await renderPage();

    expect(screen.getByText(/drain-alerts/)).toBeInTheDocument();
  });

  it("says when an installation has nowhere to deliver", async () => {
    vi.mocked(adg.fetchAlertQueue).mockResolvedValue(
      ok(queue({ policy: ["  sinks: NONE CONFIGURED -- delivered nowhere."] })),
    );

    await renderPage();

    expect(screen.getByText(/no enabled destination/)).toBeInTheDocument();
  });

  it("says a quiet feed can be believed when nothing is waiting", async () => {
    await renderPage();

    expect(screen.getByText(/quiet estate rather than a pipeline that stopped/)).toBeInTheDocument();
  });
});

describe("the watches", () => {
  it("says that nothing is watched, and what still fires without one", async () => {
    await renderPage();

    const panel = screen.getByLabelText("Watches");
    expect(within(panel).getByText(/No watches are configured/)).toBeInTheDocument();
    expect(within(panel).getByText(/do not need a watch/)).toBeInTheDocument();
  });

  it("offers a kind only the triggers the server says it supports", async () => {
    // A directory watch offered a membership trigger would be saved, listed, and silent
    // forever. The list is the server's; this page never holds a second copy of the rule.
    await renderPage();

    expect(screen.getByLabelText("Permissions changed")).toBeInTheDocument();
    expect(screen.getByLabelText("Access expanded")).toBeInTheDocument();
    expect(screen.queryByLabelText("Group membership changed")).not.toBeInTheDocument();
  });

  it("shows the cooldown a new watch will actually get", async () => {
    await renderPage();

    expect(screen.getByText(/Quiet for 15 minutes/)).toBeInTheDocument();
  });

  it("tells a reader who may not configure one why", async () => {
    vi.mocked(currentViewer).mockResolvedValue(viewer(["alerts:read"]));

    await renderPage();

    expect(screen.getByText(/held separately on purpose/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add watch" })).not.toBeInTheDocument();
  });
});

describe("the feed", () => {
  it("shows a transient alert as a notice rather than as something to close", async () => {
    await renderPage();

    const row = screen.getByRole("row", { name: /access control list of Finance/ });
    expect(within(row).getByText("Notice")).toBeInTheDocument();
  });

  it("shows how many occurrences nobody was told about", async () => {
    vi.mocked(adg.fetchAlerts).mockResolvedValue(
      ok(feed([alert({ occurrence_count: 11, suppressed_total: 10 })])),
    );

    await renderPage();

    expect(screen.getByText(/10 not delivered/)).toBeInTheDocument();
  });

  it("points an empty feed at the watches rather than calling it quiet", async () => {
    vi.mocked(adg.fetchAlerts).mockResolvedValue(ok(feed([])));

    await renderPage();

    expect(screen.getByText(/three of the four triggers cannot fire at all/)).toBeInTheDocument();
  });

  it("counts every trigger in the filter strip, including the empty ones", async () => {
    await renderPage();

    expect(screen.getByRole("link", { name: /Group membership changed \(0\)/ })).toBeInTheDocument();
  });
});

describe("one alert's history", () => {
  const detail: AlertDetailResponse = {
    alert: alert({ occurrence_count: 2, suppressed_total: 1 }),
    events: [
      {
        event_id: "22222222-2222-2222-2222-222222222222",
        transition: "suppressed",
        suppression_reason: "within_cooldown",
        notified: false,
        folds: 2,
        summary: "The access control list of Finance changed.",
        occurred_at: "2026-09-14T09:05:00Z",
        recorded_at: "2026-09-14T09:05:00Z",
        payload_digest: "c".repeat(64),
        source_run_id: null,
        source_evaluation_id: null,
      },
    ],
    deliveries: [
      {
        delivery_id: "33333333-3333-3333-3333-333333333333",
        sink_name: "operator-log",
        status: "delivered",
        attempts: 1,
        last_error: null,
        enqueued_at: "2026-09-14T09:00:00Z",
        next_attempt_at: "2026-09-14T09:00:00Z",
        delivered_at: "2026-09-14T09:00:01Z",
      },
    ],
  };

  it("shows the suppressed occurrences and why each was held", async () => {
    // Without them, "why was I not told about the other four" has no answer, and a cooldown
    // becomes indistinguishable from a pipeline that dropped them.
    vi.mocked(adg.fetchAlert).mockResolvedValue(ok(detail));

    await renderPage({ alert: "a".repeat(64) });

    const panel = screen.getByLabelText("Alert detail");
    expect(within(panel).getByText("suppressed")).toBeInTheDocument();
    expect(within(panel).getByText(/quiet window/)).toBeInTheDocument();
  });

  it("shows where each delivery went", async () => {
    vi.mocked(adg.fetchAlert).mockResolvedValue(ok(detail));

    await renderPage({ alert: "a".repeat(64) });

    expect(screen.getByText("operator-log")).toBeInTheDocument();
  });
});
