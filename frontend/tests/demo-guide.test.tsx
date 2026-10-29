// @vitest-environment jsdom
/**
 * The demo guide links every scenario into the real application.
 *
 * The scenarios are the dataset's (Genesee Press's golden scenarios A–H, served by
 * `GET /api/v1/demo`); the guide adds nothing but links — to the directory page for the
 * folder, the principal page for each person and group, and the access explanation for each
 * person against the folder. A link that pointed anywhere else would be a second, UI-only
 * version of the scenario, which is exactly what the guide must not be.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DemoPage from "@/app/demo/page";
import {
  COVERAGE_WALKTHROUGH,
  DemoCoverageAndRecovery,
  DemoGuide,
  DemoInvestigationList,
  DemoScenarioList,
  WALKTHROUGH,
} from "@/components/DemoGuide";
import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type {
  DemoComparison,
  DemoInvestigation,
  DemoOverview,
  DemoScenario,
  RuntimeInfo,
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
vi.mock("@/lib/api/adg", () => ({ fetchRuntime: vi.fn(), fetchDemoOverview: vi.fn() }));

const MOVER = "S-1-5-21-3949361974-1583827165-633756269-1243";
const SALES_OPS = "S-1-5-21-3949361974-1583827165-633756269-1437";
const REVENUE = "\\\\GP-FIN-FS01\\Finance\\Reports\\Revenue";

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
  resource: "\\\\GP-ENG-FS01\\Engineering\\Projects\\Zephyr",
  subjects: [],
  related_resources: ["\\\\GP-ENG-FS01\\Projects\\Zephyr"],
};

const FATIMA = "S-1-5-21-3949361974-1583827165-633756269-1323";
const APOLLO = "\\\\GP-ENG-FS01\\Engineering\\Projects\\Apollo";

const apollo: DemoInvestigation = {
  key: "apollo",
  title: "Why can a long-tenured engineer still modify the Apollo project folder?",
  question: `Why does Fatima Gordon have access to ${APOLLO}?`,
  principal: { kind: "person", name: "Fatima Gordon", sid: FATIMA },
  resource: APOLLO,
  access_path: "remote_smb",
  summary: "Named directly, and through five nested groups; two things ADG cannot confirm.",
  scenarios: ["C", "H"],
};

const overview: DemoOverview = {
  runtime_mode: "demo",
  dataset: {
    key: "genesee-press-v1",
    company_name: "Genesee Press",
    domain: "corp.geneseepress.example",
    snapshot_date: "2026-10-01",
    seed: "genesee-press-v1",
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

    expect(hrefOf("Brianna Reddy")).toBe(`/identities/principal/${encodeURIComponent(MOVER)}`);
    expect(hrefOf("Sales-Ops")).toBe(`/identities/principal/${encodeURIComponent(SALES_OPS)}`);
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

    expect(screen.getByRole("link", { name: "\\\\GP-ENG-FS01\\Projects\\Zephyr" })).toBeInTheDocument();
  });

  it("says so when a dataset defines no scenarios", () => {
    render(<DemoScenarioList scenarios={[]} />);

    expect(screen.getByText(/defines no guided scenarios/)).toBeInTheDocument();
  });
});

describe("the guided investigation (ADG-DEMO-02)", () => {
  it("starts from the dataset's question, without a SID to look up", () => {
    render(<DemoInvestigationList investigations={[apollo]} />);

    const card = screen.getByRole("region", { name: "Start here: a guided access investigation" });
    expect(within(card).getByText(apollo.question)).toBeInTheDocument();
    const start = within(card).getByRole("link", { name: "Investigate access" });
    const href = new URL(start.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe("/access/investigate");
    expect(href.searchParams.get("principal")).toBe(FATIMA);
    expect(href.searchParams.get("resource")).toBe(APOLLO);
    expect(href.searchParams.get("access_path")).toBe("remote_smb");
    expect(within(card).getByText(/Synthetic data/)).toBeInTheDocument();
  });

  it("walks a reviewer through eight steps, each linked to its section of the page", () => {
    render(<DemoInvestigationList investigations={[apollo]} />);

    const steps = within(
      screen.getByRole("list", { name: "The five-minute walkthrough" }),
    ).getAllByRole("listitem");
    expect(steps).toHaveLength(8);
    expect(WALKTHROUGH).toHaveLength(8);
    const anchors = steps
      .slice(1)
      .map((step) => new URL(within(step).getByRole("link").getAttribute("href") ?? "", "http://x").hash);
    expect(anchors).toEqual([
      "#question",
      "#observed-access",
      "#access-routes",
      "#access-routes",
      "#open-questions",
      "#investigation-summary",
      "#next-steps",
    ]);
  });

  it("renders nothing for a server that offers no investigation", () => {
    const { container } = render(<DemoInvestigationList investigations={[]} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("leads the guide, above the scenarios", () => {
    render(<DemoGuide overview={{ ...overview, investigations: [apollo] }} />);

    const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(headings.indexOf("Start here: a guided access investigation")).toBeLessThan(
      headings.indexOf("Scenarios"),
    );
  });

  it("offers each scenario person a step-by-step investigation too", () => {
    render(<DemoScenarioList scenarios={[scenarioF]} />);

    const href = hrefOf("investigate it step by step");
    expect(href?.startsWith("/access/investigate?")).toBe(true);
  });
});

describe("the guide", () => {
  it("shows the history, and what the demo lets a visitor change", () => {
    render(<DemoGuide overview={overview} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Genesee Press");
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

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Demo guide: Genesee Press");
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

const drill: DemoComparison = {
  key: "first-to-latest",
  title: "What is different between the first scan and the latest?",
  question: "What is different across the estate between 2026-09-03 and 2026-10-01?",
  at_from: "2026-09-03T06:00:00Z",
  at_to: "2026-10-01T06:00:00Z",
  drill_window_ms: 1,
  summary: "The estate-wide comparison of ADG's first and latest scans.",
};

describe("coverage and recovery (ADG-DEMO-03)", () => {
  function card(): HTMLElement {
    return screen.getByRole("region", {
      name: "Then: how complete is the evidence, and what if a comparison cannot finish?",
    });
  }

  it("launches the dataset's comparison drill inside its short window", () => {
    render(<DemoCoverageAndRecovery investigations={[apollo]} comparisons={[drill]} />);

    const launch = within(card()).getByRole("link", { name: "Run the comparison drill" });
    const href = new URL(launch.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe("/changes/compare");
    expect(href.searchParams.get("from")).toBe(drill.at_from);
    expect(href.searchParams.get("to")).toBe(drill.at_to);
    expect(href.searchParams.get("time_budget_ms")).toBe("1");
    // A real comparison: never run speculatively by a prefetch.
    expect(launch).toHaveAttribute("data-prefetch", "off");
    expect(within(card()).getByText(drill.question)).toBeInTheDocument();
    expect(within(card()).getByText(/deliberately short 1 ms window/)).toBeInTheDocument();
  });

  it("walks ten steps: four on coverage, six on recovery, the first five linked", () => {
    render(<DemoCoverageAndRecovery investigations={[apollo]} comparisons={[drill]} />);

    const steps = within(
      screen.getByRole("list", { name: "Coverage, then recovery" }),
    ).getAllByRole("listitem");
    expect(steps).toHaveLength(10);
    expect(COVERAGE_WALKTHROUGH).toHaveLength(10);
    const links = steps.map((step) => within(step).queryByRole("link")?.getAttribute("href") ?? null);
    expect(links.slice(0, 3)).toEqual(["/risks#risk-reading", "/risks#risk-coverage", "/collectors"]);
    const answer = new URL(links[3] ?? "", "http://x");
    expect(answer.pathname).toBe("/access/investigate");
    expect(answer.hash).toBe("#collection-coverage");
    expect(answer.searchParams.get("principal")).toBe(FATIMA);
    expect(new URL(links[4] ?? "", "http://x").pathname).toBe("/changes/compare");
    expect(links.slice(5)).toEqual([null, null, null, null, null]);
    expect(steps[9].textContent).toContain("marked complete");
  });

  it("renders nothing for a server that offers no drill", () => {
    const { container } = render(
      <DemoCoverageAndRecovery investigations={[apollo]} comparisons={[]} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("follows the investigation in the guide", () => {
    render(<DemoGuide overview={{ ...overview, investigations: [apollo], comparisons: [drill] }} />);

    const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    const investigation = headings.indexOf("Start here: a guided access investigation");
    const coverage = headings.indexOf(
      "Then: how complete is the evidence, and what if a comparison cannot finish?",
    );
    expect(investigation).toBeGreaterThanOrEqual(0);
    expect(coverage).toBe(investigation + 1);
  });
});
