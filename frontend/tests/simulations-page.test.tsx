// @vitest-environment jsdom
/**
 * The what-if pages, from a stubbed API to the DOM.
 *
 * Their acceptance criteria are not about layout. They are the things somebody must be unable
 * to miss on a screen that talks about changing permissions:
 *
 * * **the listing says what a simulation is before it lists any**, because this is where an
 *   operator lands from the navigation with no idea what the section does, and a page of rows
 *   named after change tickets would otherwise read as a queue of changes that have been made;
 * * **the notice survives a failed request**, because the state where the API is unreachable
 *   is exactly the state in which a half-rendered page is most likely to be misread;
 * * **the detail page says a stored result names principals by key**, rather than rendering
 *   keys and letting a reader conclude ADG has lost the names;
 * * **a proposal that has never been run says so**, and does not read as one found to change
 *   nothing.
 *
 * The pages are React server components — async functions returning an element tree — so they
 * are awaited and rendered directly, with the API module stubbed at its boundary.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  SimulationDetailResponse,
  SimulationExportResponse,
  StoredSimulationsResponse,
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
  fetchSimulations: vi.fn(),
  fetchSimulation: vi.fn(),
  fetchSimulationExport: vi.fn(),
  fetchSimulationVocabulary: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import SimulationsPage from "@/app/simulations/page";
import SimulationDetailPage from "@/app/simulations/[simulationId]/page";
import { NOTICE, simBaseline, storedSimulation } from "./simulation-factories";

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
    capabilities: ["simulations:read"],
    inactive_roles: [],
    unrecognized_roles: [],
    expires_at: null,
  },
};

const listing = (over: Partial<StoredSimulationsResponse> = {}): StoredSimulationsResponse => ({
  notice: NOTICE,
  simulations: [storedSimulation()],
  page: { limit: 100, has_more: false, next_cursor: null, total: null },
  ...over,
});

const detail = (over: Partial<SimulationDetailResponse> = {}): SimulationDetailResponse => ({
  notice: NOTICE,
  simulation: storedSimulation(),
  stale: false,
  current_token: "a1b2c3",
  evaluations: [],
  ...over,
});

const exported = (): SimulationExportResponse => ({
  document_version: "1.0",
  notice: NOTICE,
  exported_at: "2026-09-14T11:00:00Z",
  plan: {
    simulation_id: storedSimulation().simulation_id,
    name: "CHG-1042",
    description: null,
    created_by: null,
    created_at: "2026-09-14T10:00:00Z",
    overlay_hash: "0f1e2d",
    baseline: simBaseline(),
    changes: storedSimulation().changes,
  },
  result: null,
  stale: false,
  current_token: "a1b2c3",
  vocabulary: {
    notice: NOTICE,
    change_kinds: [],
    inherited_ace_dispositions: [],
    outcomes: [],
    directions: [],
    caveats: [],
    truncations: [],
    scope_kinds: [],
    max_changes: 50,
    bounds_ceilings: {
      max_principals: 500,
      max_resources: 500,
      max_pairs: 5000,
      max_explanations: 100,
      time_budget_ms: 60_000,
    },
  },
});

beforeEach(() => {
  vi.mocked(currentViewer).mockResolvedValue(SIGNED_IN);
});

describe("the proposals listing", () => {
  it("says nothing will be applied before it lists anything", async () => {
    vi.mocked(adg.fetchSimulations).mockResolvedValue(ok(listing()));

    render(await SimulationsPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent("NO CHANGES WILL BE APPLIED");
    expect(screen.getByRole("link", { name: "Write a new proposal" })).toBeInTheDocument();
  });

  it("still says it when the API cannot be reached", async () => {
    // The state where a half-rendered page is most likely to be misread is the one where the
    // notice matters most, so it does not come from the response body alone.
    vi.mocked(adg.fetchSimulations).mockResolvedValue({
      ok: false,
      failure: { kind: "unreachable", status: null, message: "The API is not answering." },
    });

    render(await SimulationsPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("NO CHANGES WILL BE APPLIED");
  });

  it("marks a proposal measured against a state the estate has moved on from", async () => {
    vi.mocked(adg.fetchSimulations).mockResolvedValue(
      ok(listing({ simulations: [storedSimulation({ baseline: simBaseline({ stale: true }) })] })),
    );

    render(await SimulationsPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("the estate has moved since")).toBeInTheDocument();
  });
});

describe("one proposal", () => {
  beforeEach(() => {
    vi.mocked(adg.fetchSimulationExport).mockResolvedValue(ok(exported()));
  });

  it("says a proposal that has never run has no result, not an empty one", async () => {
    vi.mocked(adg.fetchSimulation).mockResolvedValue(ok(detail()));

    render(
      await SimulationDetailPage({
        params: Promise.resolve({ simulationId: storedSimulation().simulation_id }),
      }),
    );

    expect(screen.getByText(/never been evaluated/)).toBeInTheDocument();
    expect(
      screen.getByText(/not the same as a proposal that\s+was found to change nothing/),
    ).toBeInTheDocument();
  });

  it("renders each proposed change as a sentence", async () => {
    vi.mocked(adg.fetchSimulation).mockResolvedValue(ok(detail()));

    render(
      await SimulationDetailPage({
        params: Promise.resolve({ simulationId: storedSimulation().simulation_id }),
      }),
    );

    // Twice: once in the proposal panel, once inside the structured export beneath it.
    expect(screen.getAllByText(/Remove S-1-5-21-1-2-3-1104 from/).length).toBeGreaterThan(0);
    expect(screen.getByText("Take a principal out of a group.")).toBeInTheDocument();
  });

  it("offers running it again, and says what that will not do", async () => {
    vi.mocked(adg.fetchSimulation).mockResolvedValue(ok(detail({ stale: true })));

    render(
      await SimulationDetailPage({
        params: Promise.resolve({ simulationId: storedSimulation().simulation_id }),
      }),
    );

    expect(
      screen.getByRole("button", { name: "Run this proposal again" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Changes no permission/)).toBeInTheDocument();
  });

  it("does not invent a proposal that is not there", async () => {
    vi.mocked(adg.fetchSimulation).mockResolvedValue({
      ok: false,
      failure: { kind: "not_found", status: 404, message: "No such proposal." },
    });

    render(
      await SimulationDetailPage({ params: Promise.resolve({ simulationId: "missing" }) }),
    );

    expect(screen.getByRole("link", { name: "Back to proposals" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run this proposal again" })).toBeNull();
  });
});
