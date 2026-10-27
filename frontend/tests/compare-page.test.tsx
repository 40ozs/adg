// @vitest-environment jsdom
/**
 * The point-in-time comparison page (ADG-DEMO-03), from a stubbed API to the DOM.
 *
 * Every acceptance criterion here is a sentence the page must not say:
 *
 * * a timed-out comparison is never presented as a comparison -- no list, no "nothing is
 *   different";
 * * an incomplete one is never presented as the whole -- its list is labelled partial;
 * * a recovery action is a real request with the API's parameters, not a relabelling;
 * * a recovered comparison says it is complete and that it recovered;
 * * the client's own timeout is a typed state, not "ADG could not reach its API".
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { ChangeComparisonResponse } from "@/lib/contracts";

const linkStatus = vi.hoisted(() => ({ pending: false }));
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
    <a href={href} data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
  useLinkStatus: () => ({ pending: linkStatus.pending }),
}));
vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({ compareInstants: vi.fn() }));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import ComparePage from "@/app/changes/compare/page";
import { DRILL, fromAnOlderServer, incomplete, recovered, timedOut } from "./coverage-factories";

const VIEWER = {
  status: "signed-in" as const,
  session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z", mode: "development" as const },
  principal: {
    subject: "ada",
    display_name: "Ada",
    email: null,
    source: "development" as const,
    development: true,
    roles: ["auditor"],
    capabilities: ["changes:read"],
    inactive_roles: [],
    unrecognized_roles: [],
    expires_at: null,
  },
};

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });
const DRILL_QUERY = { from: DRILL.at_from, to: DRILL.at_to, time_budget_ms: "1" };

async function open(
  body: ApiResult<ChangeComparisonResponse>,
  query: Record<string, string> = DRILL_QUERY,
): Promise<void> {
  vi.mocked(adg.compareInstants).mockResolvedValue(body);
  render(await ComparePage({ searchParams: Promise.resolve(query) }));
}

function outcome(): HTMLElement {
  return screen.getByRole("region", { name: "How this comparison ended" });
}

function params(href: string | null): URLSearchParams {
  return new URL(href ?? "", "http://x").searchParams;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue(VIEWER);
});

describe("start: the request the page makes", () => {
  it("sends the drill's window and instants", async () => {
    await open(ok(timedOut()));

    expect(adg.compareInstants).toHaveBeenCalledWith("token", {
      from: DRILL.at_from,
      to: DRILL.at_to,
      time_budget_ms: "1",
      significance: ["security", "undetermined"],
    });
  });

  it("forwards every scope the API accepts, not only share and directory", async () => {
    await open(ok(recovered()), {
      from: DRILL.at_from,
      to: DRILL.at_to,
      server: "GP-ENG-FS01",
      kind: "ntfs_ace",
      retry_of: "aaaaaaaa-0000-4000-8000-000000000002",
    });

    expect(adg.compareInstants).toHaveBeenCalledWith("token", {
      from: DRILL.at_from,
      to: DRILL.at_to,
      server: "GP-ENG-FS01",
      kind: ["ntfs_ace"],
      retry_of: "aaaaaaaa-0000-4000-8000-000000000002",
      significance: ["security", "undetermined"],
    });
  });

  it("turns the narrowing form's scope and key into the API's scope parameter", async () => {
    await open(ok(recovered()), {
      from: DRILL.at_from,
      to: DRILL.at_to,
      scope: "group",
      key: "S-1-5-21-1-2-3-1101",
    });

    expect(vi.mocked(adg.compareInstants).mock.calls[0][1]).toMatchObject({
      group: "S-1-5-21-1-2-3-1101",
    });
  });

  it("announces the running state on the action that started it", async () => {
    linkStatus.pending = true;
    try {
      await open(ok(timedOut()));
    } finally {
      linkStatus.pending = false;
    }
    const actions = within(outcome()).getByRole("navigation", { name: "Recovery actions" });
    const [retry] = within(actions).getAllByRole("link");
    const running = within(retry).getByRole("status");

    expect(running).toHaveAttribute("aria-live", "polite");
    expect(running.textContent).toBe(" — comparing…");
  });

  it("never prefetches a recovery: each one is a real comparison", async () => {
    await open(ok(timedOut()));
    const actions = within(outcome()).getByRole("navigation", { name: "Recovery actions" });

    for (const link of within(actions).getAllByRole("link")) {
      expect(link).toHaveAttribute("data-prefetch", "false");
    }
  });
});

describe("timed out: stated, nothing presented, recovery offered", () => {
  it("says it timed out, in words and a symbol, as an announced status", async () => {
    await open(ok(timedOut()));
    const [status] = within(outcome()).getAllByRole("status");

    expect(status).toHaveClass("banner");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status.textContent).toContain("Timed out");
    expect(status.textContent).toContain("⏱");
    expect(status.textContent).toContain("Timed out — no comparison is presented");
  });

  it("presents no list and never says nothing is different", async () => {
    await open(ok(timedOut()));

    expect(screen.queryByText(/Nothing is different/)).toBeNull();
    expect(screen.queryByRole("region", { name: /Differences/ })).toBeNull();
    expect(screen.queryByText(/objects were present and identical/)).toBeNull();
  });

  it("offers the API's recovery actions as real requests naming this attempt", async () => {
    await open(ok(timedOut()));
    const actions = within(outcome()).getByRole("navigation", { name: "Recovery actions" });
    const [retry, narrow] = within(actions).getAllByRole("link");

    expect(retry).toHaveTextContent("Retry inside the standard 4-second window");
    expect(params(retry.getAttribute("href")).get("time_budget_ms")).toBe("4000");
    expect(params(retry.getAttribute("href")).get("retry_of")).toBe(
      "aaaaaaaa-0000-4000-8000-000000000001",
    );
    expect(params(retry.getAttribute("href")).get("from")).toBe(DRILL.at_from);
    expect(narrow).toHaveTextContent("Compare only folder permission entries (3,947 → 4,124 objects)");
    expect(params(narrow.getAttribute("href")).get("kind")).toBe("ntfs_ace");
  });

  it("puts the reason one disclosure away", async () => {
    await open(ok(timedOut()));
    const diagnostics = within(outcome()).getByText("Diagnostics");

    expect(diagnostics.tagName).toBe("SUMMARY");
    expect(outcome().textContent).toContain("Window 1 ms; ran 174 ms");
  });

  it("offers a narrowing form that keeps the instants and names this attempt", async () => {
    await open(ok(timedOut()));
    const form = screen.getByRole("form", { name: "Narrow the comparison" });

    expect(within(form).getByLabelText("Scope")).toBeInTheDocument();
    expect(within(form).getByLabelText("Objects")).toBeInTheDocument();
    const hidden = Array.from(form.querySelectorAll("input[type=hidden]")).map((input) => [
      input.getAttribute("name"),
      input.getAttribute("value"),
    ]);
    expect(hidden).toEqual([
      ["from", DRILL.at_from],
      ["to", DRILL.at_to],
      ["retry_of", "aaaaaaaa-0000-4000-8000-000000000001"],
    ]);
  });
});

describe("incomplete: the part compared, labelled as a part", () => {
  it("says it is not the whole comparison, and where it stopped", async () => {
    await open(ok(incomplete()), { from: DRILL.at_from, to: DRILL.at_to });

    expect(outcome().textContent).toContain("Incomplete — this is not the whole comparison");
    expect(outcome().textContent).toContain("group memberships up to S-1-5-21-");
    expect(
      screen.getByRole("heading", { name: "What was compared before the comparison stopped (partial)" }),
    ).toBeInTheDocument();
  });

  it("never says nothing is different without saying it is about the part compared", async () => {
    await open(ok(incomplete()), { from: DRILL.at_from, to: DRILL.at_to });

    expect(screen.queryByRole("heading", { name: "Nothing is different" })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Nothing is different in the part that was compared" }),
    ).toBeInTheDocument();
  });

  it("offers the narrower comparisons that fit", async () => {
    await open(ok(incomplete()), { from: DRILL.at_from, to: DRILL.at_to });
    const actions = within(outcome()).getByRole("navigation", { name: "Recovery actions" });

    expect(within(actions).getByRole("link")).toHaveTextContent("Compare only folder permission entries");
  });

  it("reads an older server's truncated comparison as incomplete, never complete", async () => {
    await open(ok(fromAnOlderServer(true)), { from: DRILL.at_from, to: DRILL.at_to });

    expect(outcome().textContent).toContain("Incomplete");
    expect(outcome().textContent).not.toContain("Complete comparison");
  });
});

describe("recovered: complete, and says so", () => {
  it("is marked complete and recovered", async () => {
    await open(ok(recovered()), { from: DRILL.at_from, to: DRILL.at_to, kind: "ntfs_ace" });
    const [status] = within(outcome()).getAllByRole("status");

    expect(status.textContent).toContain("Complete");
    expect(status.textContent).toContain("✓");
    expect(status.textContent).toContain("Complete — this comparison recovered the earlier attempt");
    expect(within(outcome()).queryByRole("navigation", { name: "Recovery actions" })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "What this comparison could and could not compare" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Narrow the comparison" })).toBeNull();
  });
});

describe("failures are typed", () => {
  it("states a client-side timeout as a timeout, not as an unreachable API", async () => {
    await open(
      {
        ok: false,
        failure: {
          kind: "timeout",
          status: null,
          message: "The ADG API did not answer within 10 seconds.",
        },
      },
      { from: DRILL.at_from, to: DRILL.at_to, time_budget_ms: "8000" },
    );

    const [status] = screen.getAllByRole("status");
    expect(status).toHaveClass("banner");
    expect(status.textContent).toContain("Timed out — no comparison is presented");
    expect(screen.queryByText("ADG could not reach its API")).toBeNull();
    const retry = within(status).getByRole("link", { name: "Retry inside the standard window" });
    expect(params(retry.getAttribute("href")).get("time_budget_ms")).toBeNull();
    expect(screen.getByRole("form", { name: "Narrow the comparison" })).toBeInTheDocument();
  });

  it("states an unreachable API as one", async () => {
    await open(
      {
        ok: false,
        failure: { kind: "unreachable", status: null, message: "The ADG API did not respond." },
      },
      { from: DRILL.at_from, to: DRILL.at_to },
    );

    expect(screen.getByText("ADG could not reach its API")).toBeInTheDocument();
  });
});

describe("keyboard", () => {
  it("reaches the recovery actions and the diagnostics by Tab, in order", async () => {
    const user = userEvent.setup();
    await open(ok(timedOut()));
    const [back, retry, narrow] = screen.getAllByRole("link");

    await user.tab();
    expect(document.activeElement).toBe(back);
    await user.tab();
    expect(document.activeElement).toBe(retry);
    await user.tab();
    expect(document.activeElement).toBe(narrow);
    await user.tab();
    expect(document.activeElement?.tagName).toBe("SUMMARY");
  });
});
