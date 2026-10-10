// @vitest-environment jsdom
/**
 * The demo guide links every scenario into the real application.
 *
 * The scenarios are the dataset's (Null Meridian's golden scenarios A–H, served by
 * `GET /api/v1/demo`); the guide adds nothing but links — to the directory page for the
 * folder, the principal page for each person and group, and the access explanation for each
 * person against the folder. A link that pointed anywhere else would be a second, UI-only
 * version of the scenario, which is exactly what the guide must not be.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DemoPage from "@/app/demo/page";
import { DemoGuide, DemoScenarioList } from "@/components/DemoGuide";
import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { DemoOverview, DemoScenario, RuntimeInfo } from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({ fetchRuntime: vi.fn(), fetchDemoOverview: vi.fn() }));

const MOVER = "S-1-5-21-3949361974-1583827165-633756269-1243";
const SALES_OPS = "S-1-5-21-3949361974-1583827165-633756269-1437";
const REVENUE = "\\\\NM-FIN-FS01\\Finance\\Reports\\Revenue";

const scenarioF: DemoScenario = {
  key: "F",
  title: "Cross-department access, two ways",
  summary: "They moved from Finance to Sales Operations; both routes remain.",
  resource: REVENUE,
  subjects: [
    { kind: "person", name: "Brianna Reddy", sid: MOVER },
    { kind: "group", name: "Sales-Ops", sid: SALES_OPS },
  ],
  related_resources: [],
};

const scenarioH: DemoScenario = {
  key: "H",
  title: "A safe first clean-up",
  summary: "An empty group still granted Modify.",
  resource: "\\\\NM-ENG-FS01\\Engineering\\Projects\\Zephyr",
  subjects: [],
  related_resources: ["\\\\NM-ENG-FS01\\Projects\\Zephyr"],
};

const overview: DemoOverview = {
  runtime_mode: "demo",
  dataset: {
    key: "null-meridian-v1",
    company_name: "Null Meridian",
    domain: "nullmeridian.info",
    snapshot_date: "2026-10-01",
    seed: "null-meridian-v1",
    synthetic: true,
    description: "A fictional company.",
    facts: {},
  },
  generations: [
    { index: 0, name: "g1", description: "2026-09-03: Initial observation", applied: true, in_baseline: true },
    { index: 1, name: "g2", description: "2026-09-17: Two weeks of change", applied: true, in_baseline: true },
    { index: 2, name: "g3", description: "2026-10-01: Snapshot", applied: false, in_baseline: true },
  ],
  baseline_generations: 3,
  next_generation: "g3",
  scenarios: [scenarioF, scenarioH],
  mutation_policy: [
    {
      method: "POST",
      path: "/api/v1/scan-runs",
      operation: "begin a collector scan run",
      treatment: "unavailable",
      reason: "No observations from outside.",
    },
    {
      method: "POST",
      path: "/api/v1/remediation/{plan_id}/exports",
      operation: "export a change plan and its runbook",
      treatment: "simulated",
      reason: "Inert runbook.",
    },
  ],
};

function hrefOf(name: string): string | null {
  return screen.getByRole("link", { name }).getAttribute("href");
}

describe("the scenario list", () => {
  it("links the folder to the real directory page", () => {
    render(<DemoScenarioList scenarios={[scenarioF]} />);

    expect(hrefOf(REVENUE)).toBe(`/resources/directory?key=${encodeURIComponent(REVENUE)}`);
  });

  it("links people and groups to their principal pages, by SID", () => {
    render(<DemoScenarioList scenarios={[scenarioF]} />);

    expect(hrefOf("Brianna Reddy")).toBe(`/identities/principal?key=${encodeURIComponent(MOVER)}`);
    expect(hrefOf("Sales-Ops")).toBe(`/identities/principal?key=${encodeURIComponent(SALES_OPS)}`);
  });

  it("offers each person an explanation of their access to the folder", () => {
    render(<DemoScenarioList scenarios={[scenarioF]} />);

    const explain = hrefOf("explain their access to this folder");
    const params = new URL(explain ?? "", "http://x").searchParams;
    expect(explain?.startsWith("/access/explain?")).toBe(true);
    expect(params.get("principal")).toBe(MOVER);
    expect(params.get("resource")).toBe(REVENUE);
  });

  it("links related folders too", () => {
    render(<DemoScenarioList scenarios={[scenarioH]} />);

    expect(screen.getByRole("link", { name: "\\\\NM-ENG-FS01\\Projects\\Zephyr" })).toBeInTheDocument();
  });

  it("says so when a dataset defines no scenarios", () => {
    render(<DemoScenarioList scenarios={[]} />);

    expect(screen.getByText(/defines no guided scenarios/)).toBeInTheDocument();
  });
});

describe("the guide", () => {
  it("shows the history, and what the demo lets a visitor change", () => {
    render(<DemoGuide overview={overview} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Null Meridian");
    const history = screen.getByRole("table");
    expect(within(history).getAllByText("applied")).toHaveLength(2);
    expect(within(history).getByText("not yet applied")).toBeInTheDocument();
    expect(screen.getByText(/Not available in the demo \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Simulation only/)).toBeInTheDocument();
  });

  it("offers no control: the guide changes nothing", () => {
    render(<DemoGuide overview={overview} />);

    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("the page", () => {
  const demo: RuntimeInfo = {
    runtime_mode: "demo",
    data_source: "synthetic",
    synthetic: true,
    dataset: overview.dataset,
    notice: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the guide for a signed-in visitor of a demo deployment", async () => {
    vi.mocked(currentViewer).mockResolvedValue({
      status: "signed-in",
      session: { accessToken: "t" },
    } as never);
    vi.mocked(adg.fetchRuntime).mockResolvedValue({ ok: true, data: demo });
    vi.mocked(adg.fetchDemoOverview).mockResolvedValue({ ok: true, data: overview });

    render(await DemoPage());

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Demo guide: Null Meridian");
    expect(adg.fetchDemoOverview).toHaveBeenCalledWith("t");
  });

  it("asks nothing of a production deployment's API", async () => {
    vi.mocked(currentViewer).mockResolvedValue({
      status: "signed-in",
      session: { accessToken: "t" },
    } as never);
    vi.mocked(adg.fetchRuntime).mockResolvedValue({
      ok: true,
      data: { ...demo, runtime_mode: "production", data_source: "collectors", synthetic: false, dataset: null },
    });

    render(await DemoPage());

    expect(screen.getByText(/not a demo environment/)).toBeInTheDocument();
    expect(adg.fetchDemoOverview).not.toHaveBeenCalled();
  });
});
