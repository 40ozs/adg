// @vitest-environment jsdom
/**
 * The what-if screens, asserted on what an operator has to be unable to miss.
 *
 * Four properties, and each is an acceptance criterion of the phase rather than a rendering
 * preference:
 *
 * 1. **The non-destructive nature is unmistakable.** The notice is present, it is an alert,
 *    and it carries the API's own sentence.
 * 2. **The output is explainable, not only a count.** Every row says which direction, in
 *    words, with the masks either side; a surviving route is named rather than counted.
 * 3. **A caveat is not a footnote.** `loss_may_not_hold` appears beside the row it qualifies,
 *    in the API's own words.
 * 4. **A partial answer cannot be read as a complete one.** A truncated report says so above
 *    the list, not below it.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ApplicationsPanel,
  BaselinePanel,
  DeltaTable,
  ImpactPanel,
  SimulationNotice,
  StoredSimulationsTable,
  TruncationPanel,
} from "@/components/Simulation";
import { SimulateLink } from "@/components/SimulateLink";
import { NtfsAceTable } from "@/components/RawAcl";
import { ntfsAce } from "./factories";
import {
  NOTICE,
  simApplication,
  simBaseline,
  simDelta,
  simPrincipal,
  simReport,
  simRights,
  simSummary,
  storedSimulation,
} from "./simulation-factories";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("the non-destructive notice", () => {
  it("is an alert carrying the API's own sentence", () => {
    render(<SimulationNotice notice={NOTICE} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("NO CHANGES WILL BE APPLIED");
    expect(alert).toHaveTextContent("Active Directory");
    expect(alert).toHaveTextContent("NTFS descriptor");
  });
});

describe("what became of each change", () => {
  it("shows the outcome of a change that did not apply, in words", () => {
    // An empty impact list means something completely different depending on this panel.
    render(
      <ApplicationsPanel
        applications={[
          simApplication({
            outcome: "target_not_found",
            outcome_description:
              "The entry or membership this change names is not in the baseline state.",
            applied: false,
          }),
        ]}
      />,
    );

    expect(screen.getByText("target_not_found")).toBeInTheDocument();
    expect(
      screen.getByText(/is not in the baseline state/),
    ).toBeInTheDocument();
  });

  it("renders the change as a sentence rather than as a document", () => {
    render(<ApplicationsPanel applications={[simApplication()]} />);

    expect(screen.getByText(/Remove S-1-5-21-1-2-3-1104 from/)).toBeInTheDocument();
  });
});

describe("the impact panel", () => {
  it("leads with a sentence, not a number", () => {
    render(
      <ImpactPanel
        report={simReport({
          summary: simSummary({
            evaluated: 1,
            lost_access: 1,
            principals_losing: [simPrincipal()],
            principals_affected: 1,
          }),
        })}
      />,
    );

    expect(screen.getByText(/would lose access or rights/)).toBeInTheDocument();
  });

  it("names the sensitive directories a change reaches", () => {
    render(
      <ImpactPanel
        report={simReport({
          summary: simSummary({
            evaluated: 1,
            lost_access: 1,
            sensitive_resources_affected: ["\\\\fs01\\payroll"],
          }),
        })}
      />,
    );

    expect(screen.getByText(/Sensitive directories affected/)).toBeInTheDocument();
    expect(screen.getByText(/payroll/)).toBeInTheDocument();
  });

  it("says the watch flag is withheld rather than showing a guessed false", () => {
    // Who is being notified about what is a statement about the organization, and it is not
    // inherited by holding simulations:read.
    render(
      <ImpactPanel
        report={simReport({
          summary: simSummary({ evaluated: 1, watched_resources_affected: null }),
        })}
      />,
    );

    expect(screen.getByText(/alerts:read/)).toBeInTheDocument();
  });
});

describe("the delta table", () => {
  const losing = simDelta({
    direction: "lost_access",
    direction_description: "Holds something today and would hold nothing.",
    changed: true,
    rights_before: simRights({ mask: "0x001301BF", value: 0x001301bf, label: "Modify" }),
    caveats: [
      {
        code: "loss_may_not_hold",
        description:
          "Rights are reported as removed, but the simulated answer is a lower bound.",
      },
    ],
  });

  it("shows both whole answers and the direction in words", () => {
    render(<DeltaTable report={simReport({ deltas: [losing] })} />);

    const row = screen.getByRole("row", { name: /Alice Smith/ });
    expect(within(row).getByText("0x001301BF")).toBeInTheDocument();
    expect(within(row).getByText("0x00000000")).toBeInTheDocument();
    expect(within(row).getByText("Holds something today and would hold nothing.")).toBeInTheDocument();
  });

  it("renders the caveat beside the row it qualifies, in the API's words", () => {
    render(<DeltaTable report={simReport({ deltas: [losing] })} />);

    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("loss_may_not_hold");
    expect(note).toHaveTextContent("the simulated answer is a lower bound");
  });

  it("names a route that survives rather than counting it", () => {
    // The field that stands between a report and a remediation that achieves nothing.
    render(
      <DeltaTable
        report={simReport({
          deltas: [
            simDelta({
              alternate_path_retained: true,
              retained_routes: [
                {
                  layer: "ntfs",
                  chain: [simPrincipal(), simPrincipal({ key: "g", display_name: "Domain Users" })],
                  ace_key: "ace-1",
                  ace_position: 0,
                  rights: simRights({ mask: "0x001200A9" }),
                  assumed: false,
                  inherited: false,
                  via_group: true,
                },
              ],
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText(/Alice Smith → Domain Users/)).toBeInTheDocument();
  });

  it("keeps an unchanged row rather than filtering it out", () => {
    render(<DeltaTable report={simReport({ deltas: [simDelta()] })} />);

    expect(screen.getByText("unchanged")).toBeInTheDocument();
    expect(screen.getByText(/A pair that is not listed was not evaluated/)).toBeInTheDocument();
  });

  it("says plainly when nothing was evaluated", () => {
    render(<DeltaTable report={simReport({ deltas: [] })} />);

    expect(screen.getByText(/No principal\/directory pair was evaluated/)).toBeInTheDocument();
  });

  it("marks a sensitive directory on the row", () => {
    render(
      <DeltaTable
        report={simReport({
          deltas: [
            simDelta({
              resource: {
                resource_key: "\\\\fs01\\payroll",
                share_key: "fs01|payroll",
                path: "\\\\FS01\\Payroll",
                sensitive: true,
                sensitivity_labels: ["Payroll data"],
                watched: true,
              },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("sensitive")).toBeInTheDocument();
    expect(screen.getByText("watched")).toBeInTheDocument();
    expect(screen.getByText("Payroll data")).toBeInTheDocument();
  });
});

describe("a truncated report", () => {
  it("says so as an alert, above the list", () => {
    render(
      <TruncationPanel
        report={simReport({
          complete: false,
          truncation: [
            {
              code: "trustee_expansion_incomplete",
              description: "A trustee's membership could not be enumerated.",
            },
          ],
        })}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("part of the answer");
    expect(alert).toHaveTextContent("trustee_expansion_incomplete");
  });

  it("renders nothing at all for a complete one", () => {
    const { container } = render(<TruncationPanel report={simReport()} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("the baseline panel", () => {
  it("says a stale baseline is the estate having moved, not the proposal being wrong", () => {
    render(
      <BaselinePanel baseline={simBaseline({ stale: true, current_token: "d4e5f6" })} />,
    );

    expect(screen.getByText(/what has moved is the estate/)).toBeInTheDocument();
    expect(screen.getByText("d4e5f6")).toBeInTheDocument();
  });

  it("says an empty estate makes every answer useless rather than reassuring", () => {
    render(<BaselinePanel baseline={simBaseline({ is_empty: true })} />);

    expect(screen.getByText(/truthfully and uselessly/)).toBeInTheDocument();
  });
});

describe("the proposals listing", () => {
  it("marks a proposal whose baseline has moved", () => {
    render(
      <StoredSimulationsTable
        simulations={[
          storedSimulation({ name: "CHG-1", baseline: simBaseline({ stale: true }) }),
          storedSimulation({ simulation_id: "other", name: "CHG-2" }),
        ]}
      />,
    );

    expect(screen.getByText("the estate has moved since")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CHG-1" })).toBeInTheDocument();
  });
});

describe("the Simulate action", () => {
  it("is a link, so nothing can happen by pressing it", () => {
    render(<SimulateLink seed={{ kind: "remove_member", group_key: "g", member_key: "m" }} />);

    const link = screen.getByRole("link", { name: "Simulate" });
    expect(link.getAttribute("href")).toContain("/simulations/new?");
    expect(link.getAttribute("href")).toContain("kind=remove_member");
  });
});

describe("an ACL row", () => {
  it("offers a simulate action naming that entry when the directory is known", () => {
    render(
      <NtfsAceTable entries={[ntfsAce({ ace_key: "ace-1" })]} resourceKey="\\\\FS01\\Finance" />,
    );

    const link = screen.getByRole("link", { name: "Simulate removal" });
    expect(link.getAttribute("href")).toContain("kind=remove_ntfs_ace");
    expect(link.getAttribute("href")).toContain("ace_key=ace-1");
  });

  it("offers none when the row cannot say which directory it came from", () => {
    // A row that could not name its own object must not propose to change one.
    render(<NtfsAceTable entries={[ntfsAce({ ace_key: "ace-1" })]} />);

    expect(screen.queryByRole("link", { name: "Simulate removal" })).toBeNull();
    expect(screen.queryByText("What if")).toBeNull();
  });
});
