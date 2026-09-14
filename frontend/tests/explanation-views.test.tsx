// @vitest-environment jsdom
/**
 * The access explanation screen, rendered.
 *
 * Four scenarios, which are the four an auditor meets: access that arrives by more than
 * one route, an explicit denial, an absence of any grant, and an answer nobody has
 * collected enough to give. Three of them are situations a careless screen reports as the
 * same word.
 *
 * These assert what is *on the screen* rather than the markup — that the verdict is not the
 * only thing that says which of the three negatives this is, that a removal which changes
 * nothing is never worded as a fix, that the alternative routes are named, and that every
 * route can be selected and inspected from the keyboard without the diagram.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PrincipalAccessView, ResourceAccessView } from "@/lib/contracts";

import { AccessGraph } from "@/components/AccessGraph";
import { PrincipalAccessTable, ResourceAccessTable } from "@/components/EffectiveAccess";
import {
  CautionPanel,
  ExplanationHeader,
  LayerPanel,
  RemovalPanel,
  VerdictPanel,
} from "@/components/Explanation";
import { ExplainLauncher } from "@/components/ExplainLauncher";
import { ExplanationExplorer } from "@/components/ExplanationExplorer";
import { ExplanationExport } from "@/components/ExplanationExport";
import {
  ALICE_SID,
  RESOURCE_KEY,
  TEAM,
  applied,
  denied,
  explanation,
  indeterminate,
  noGrant,
  path,
  removal,
  resource,
  rights,
  share,
  twoRoutes,
} from "./explanation-factories";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("the verdict", () => {
  it("says access arrived, and how certain that is", () => {
    render(<VerdictPanel explanation={twoRoutes()} />);

    expect(screen.getByText("Access")).toBeInTheDocument();
    expect(screen.getByText(/Every input this answer depends on was collected/)).toBeInTheDocument();
  });

  it("says a denial is a control somebody may be relying on", () => {
    render(<VerdictPanel explanation={denied()} />);

    expect(screen.getByText("Denied")).toBeInTheDocument();
    expect(screen.getByText(/relying on it/)).toBeInTheDocument();
  });

  it("says a no-grant is the absence of a control, not a control", () => {
    render(<VerdictPanel explanation={noGrant()} />);

    expect(screen.getByText("No access", { selector: ".verdict" })).toBeInTheDocument();
    expect(screen.getByText(/no entry here to remove/)).toBeInTheDocument();
  });

  it("never lets an inconclusive answer read as a negative one", () => {
    render(<VerdictPanel explanation={indeterminate()} />);

    expect(screen.getByText("Unknown")).toBeInTheDocument();

    // Not anywhere on the panel, and the rights row is where it would otherwise appear:
    // the engine labels an empty mask "No access", which is precisely the conclusion this
    // answer does not support.
    expect(screen.queryByText("No access")).not.toBeInTheDocument();
    expect(screen.getByText("None established")).toBeInTheDocument();

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("Not a finding of no access")).toBeInTheDocument();
  });

  it("puts no such alert on a conclusive answer", () => {
    render(<VerdictPanel explanation={noGrant()} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("names the narrower layer, which is where a fix has to go", () => {
    render(<VerdictPanel explanation={twoRoutes()} />);

    expect(screen.getByText(/the NTFS ACL is the narrower one/)).toBeInTheDocument();
  });
});

describe("the caveats", () => {
  it("collects every gap in an incomplete answer, above the working", () => {
    render(<CautionPanel explanation={indeterminate()} />);

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText(/does not support a negative conclusion/)).toBeInTheDocument();
    expect(within(alert).getByText(/These are not all the routes/)).toBeInTheDocument();
    expect(within(alert).getByText(/access token is a lower bound/)).toBeInTheDocument();
    expect(within(alert).getByText(/No run has read this directory/)).toBeInTheDocument();
  });

  it("renders nothing at all when there is nothing to qualify", () => {
    const { container } = render(<CautionPanel explanation={explanation()} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("what a removal would do", () => {
  it("warns that more than one change is needed when no single removal helps", () => {
    render(<RemovalPanel explanation={twoRoutes()} />);

    expect(screen.getByText(/More than one change is needed/)).toBeInTheDocument();
    expect(screen.getByText(/No single removal ends access here/)).toBeInTheDocument();
  });

  it("marks each removal that changes nothing, and names what survives it", () => {
    render(<RemovalPanel explanation={twoRoutes()} />);

    expect(screen.getAllByText("Changes nothing")).toHaveLength(2);
    // The alternative route is the reason it changes nothing, so it is on the row.
    expect(screen.getByText("p0001")).toBeInTheDocument();
  });

  it("calls a removal that would widen access a warning rather than a fix", () => {
    render(<RemovalPanel explanation={denied()} />);

    expect(screen.getByText("Would widen access")).toBeInTheDocument();
    expect(screen.getByText(/would grant Modify, not take anything away/)).toBeInTheDocument();
  });

  it("says a partial removal does not revoke access", () => {
    const data = explanation({
      removal_targets: [
        removal({
          revokes_all_access: false,
          rights_removed: rights({ label: "Write" }),
          rights_after: rights({ label: "Read" }),
        }),
      ],
    });
    render(<RemovalPanel explanation={data} />);

    expect(screen.getByText("Narrows access")).toBeInTheDocument();
    expect(screen.getByText(/Access is not revoked/)).toBeInTheDocument();
  });

  it("explains an empty table rather than showing an empty table", () => {
    render(<RemovalPanel explanation={noGrant()} />);

    expect(screen.getByText(/No removable relationship was measured/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("forbids believing any removal when the routes were cut short", () => {
    const data = { ...twoRoutes(), complete: false };
    render(<RemovalPanel explanation={data} />);

    expect(screen.getByText(/no removal below may be believed sufficient/)).toBeInTheDocument();
  });
});

describe("the ACL layers", () => {
  it("shows both, because which one is narrower decides where the fix goes", () => {
    render(<LayerPanel title="The NTFS ACL" explanation={twoRoutes()} layer="ntfs" />);

    expect(screen.getByRole("heading", { name: "The NTFS ACL" })).toBeInTheDocument();
    expect(screen.getByText(/2 of 2 entries matched/)).toBeInTheDocument();
  });

  it("says a layer does not apply rather than rendering it empty", () => {
    const base = explanation();
    const local = {
      ...base,
      access_path: "local",
      effective: { ...base.effective, share: null },
    };
    render(<LayerPanel title="The share ACL" explanation={local} layer="share" />);

    expect(screen.getByText(/Not applied on the/)).toBeInTheDocument();
    expect(screen.getByText(/Local access does not cross a share/)).toBeInTheDocument();
  });

  it("puts the position and the inheritance source on every entry", () => {
    // "The Finance-Team ACE" is not a thing an administrator can go and find, and a change
    // applied to an inherited entry on the wrong object changes nothing.
    render(<LayerPanel title="The NTFS ACL" explanation={twoRoutes()} layer="ntfs" />);

    const table = screen.getByRole("table", { name: /granted/i });
    expect(within(table).getByText(/set on \\\\FS01\\Finance/)).toBeInTheDocument();
    expect(within(table).getAllByText("inherited").length).toBeGreaterThan(0);
  });

  it("labels an entry that matched and took nothing", () => {
    const base = explanation();
    const data = {
      ...base,
      effective: {
        ...base.effective,
        ntfs: { ...base.effective.ntfs, superseded: [applied({ ace_key: "x", position: 4 })] },
      },
    };
    render(<LayerPanel title="The NTFS ACL" explanation={data} layer="ntfs" />);

    expect(screen.getByText(/Removing one of these on its own changes nothing/)).toBeInTheDocument();
  });

  it("marks a Deny entry as a deny", () => {
    render(<LayerPanel title="The NTFS ACL" explanation={denied()} layer="ntfs" />);

    const table = screen.getByRole("table", { name: /denied/i });
    expect(within(table).getByText("deny")).toBeInTheDocument();
  });
});

describe("the routes", () => {
  it("lists every route with what it says and what it is worth", () => {
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    const table = screen.getByRole("table", { name: /Select a route/ });
    expect(within(table).getAllByRole("row")).toHaveLength(3); // header plus two routes
    expect(within(table).getAllByText("Contributes")).toHaveLength(2);
  });

  it("can be selected and inspected from the keyboard, without the diagram", () => {
    // The acceptance criterion: accessibility must not depend on the visualization.
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    const button = screen.getByRole("button", { name: /p0000/ });
    expect(button).toHaveAttribute("aria-pressed", "false");

    button.focus();
    expect(button).toHaveFocus();
  });

  it("opens the route it was asked for", async () => {
    const user = userEvent.setup();
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    await user.click(screen.getByRole("button", { name: /p0000/ }));

    const inspector = screen.getByRole("region", { name: "Route p0000" });
    expect(within(inspector).getByText(/Alice Smith is a member of Finance-Team/)).toBeInTheDocument();
    expect(within(inspector).getByText(/NTFS entry #0 names Finance-Team/)).toBeInTheDocument();
    // Every hop the route names, and no more.
    expect(within(inspector).getAllByRole("listitem")).toHaveLength(4);
  });

  it("shows the exact entry, its position, and where it was set", async () => {
    const user = userEvent.setup();
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    await user.click(screen.getByRole("button", { name: /p0000/ }));
    const inspector = screen.getByRole("region", { name: "Route p0000" });

    expect(within(inspector).getByText("ntfs|team")).toBeInTheDocument();
    expect(within(inspector).getByText(/a change made here would not affect it/)).toBeInTheDocument();
  });

  it("says whether the route contributed to the final answer", async () => {
    const user = userEvent.setup();
    const data = explanation({
      paths: [path({ id: "p0000", effect: "redundant", edges: [], nodes: [] })],
    });
    render(<ExplanationExplorer explanation={data} />);

    await user.click(screen.getByRole("button", { name: /p0000/ }));
    const inspector = screen.getByRole("region", { name: "Route p0000" });

    expect(within(inspector).getByText(/Removing this route alone would not change/)).toBeInTheDocument();
  });

  it("says an ownership route has no entry, because no ACL viewer shows it", async () => {
    const user = userEvent.setup();
    const data = explanation({
      paths: [path({ id: "p0000", ace_key: null, ace_position: -1, edges: [], nodes: [] })],
    });
    render(<ExplanationExplorer explanation={data} />);

    await user.click(screen.getByRole("button", { name: /p0000/ }));

    expect(screen.getByText(/rights come from owning the object/)).toBeInTheDocument();
  });

  it("closes the inspector when the same route is chosen again", async () => {
    const user = userEvent.setup();
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    await user.click(screen.getByRole("button", { name: /p0000/ }));
    expect(screen.getByRole("region", { name: "Route p0000" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /p0000/ }));
    expect(screen.queryByRole("region", { name: "Route p0000" })).not.toBeInTheDocument();
  });

  it("marks a route reached through an assumption rather than an observed membership", () => {
    const data = explanation({ paths: [path({ assumed: true })] });
    render(<ExplanationExplorer explanation={data} />);

    expect(screen.getByText(/through an assumed token SID/)).toBeInTheDocument();
  });

  it("says there is no route rather than showing an empty table", () => {
    render(<ExplanationExplorer explanation={noGrant()} />);

    expect(screen.getByText(/No entry on either ACL reached this principal/)).toBeInTheDocument();
  });

  it("warns on the route table itself when the enumeration was cut short", () => {
    render(<ExplanationExplorer explanation={{ ...twoRoutes(), complete: false }} />);

    expect(screen.getByText("Not every route")).toBeInTheDocument();
    expect(screen.getByText(/absence of one here is not evidence there is none/)).toBeInTheDocument();
  });
});

describe("the diagram", () => {
  it("is an image with a written summary, not an unlabelled picture", () => {
    render(
      <AccessGraph explanation={twoRoutes()} selectedPathId={null} onSelectPath={() => {}} />,
    );

    const image = screen.getByRole("img");
    expect(image).toHaveAccessibleName(/How Alice Smith reaches/);
    expect(image).toHaveAccessibleName(/listed in the tables below this diagram/);
  });

  it("says how many routes there are, and whether that is all of them", () => {
    render(
      <AccessGraph
        explanation={{ ...twoRoutes(), complete: false }}
        selectedPathId={null}
        onSelectPath={() => {}}
      />,
    );

    expect(screen.getByRole("img")).toHaveAccessibleName(/not all of them were enumerated/);
  });

  it("draws every node and every relationship", () => {
    const data = twoRoutes();
    const { container } = render(
      <AccessGraph explanation={data} selectedPathId={null} onSelectPath={() => {}} />,
    );

    expect(container.querySelectorAll("[data-node]")).toHaveLength(data.graph.nodes.length);
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(data.graph.edges.length);
  });

  it("repeats every relationship as text, so the picture is never the only copy", () => {
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    expect(screen.getByText(/Every relationship in the diagram, as a list/)).toBeInTheDocument();
    expect(screen.getByText("Alice Smith is a member of Finance-Team")).toBeInTheDocument();
    expect(screen.getByText("NTFS entry #0 allows rights on \\\\FS01\\Finance")).toBeInTheDocument();
  });

  it("selects the route through a node when one is clicked", async () => {
    const user = userEvent.setup();
    render(<ExplanationExplorer explanation={twoRoutes()} />);

    const { container } = render(
      <AccessGraph explanation={twoRoutes()} selectedPathId={null} onSelectPath={() => {}} />,
    );
    const node = container.querySelector('[data-node="group:S-1-5-21-1004336348-1177238915-682003330-1201"]');
    expect(node).not.toBeNull();

    await user.click(node as Element);
  });
});

describe("the export", () => {
  const exported = (): string => (screen.getByRole("textbox") as HTMLTextAreaElement).value;

  it("offers the explanation as text, in full, without a clipboard", () => {
    // A copy button that silently fails is how an auditor loses a finding.
    render(<ExplanationExport explanation={twoRoutes()} />);

    expect(exported()).toContain("VERDICT: ACCESS");
    expect(exported()).toContain("[p0000]");
  });

  it("carries the caveats into the exported text", () => {
    render(<ExplanationExport explanation={indeterminate()} />);

    expect(exported()).toContain("This answer does not support a negative conclusion");
  });

  it("switches to the response's own JSON", async () => {
    const user = userEvent.setup();
    render(<ExplanationExport explanation={twoRoutes()} />);

    await user.click(screen.getByRole("radio", { name: /Structured JSON/ }));

    expect(exported()).toContain('"schema_version": "1.0"');
  });

  it("says so when the browser refuses clipboard access", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("denied")) },
    });
    render(<ExplanationExport explanation={twoRoutes()} />);

    await user.click(screen.getByRole("button", { name: /Copy to clipboard/ }));

    expect(await screen.findByText(/Select the text below and copy it/)).toBeInTheDocument();
  });
});

describe("asking the question", () => {
  it("is a plain GET form, so the answer is addressable afterwards", () => {
    const { container } = render(<ExplainLauncher />);
    const form = container.querySelector("form");

    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/access/explain");
  });

  it("requires both halves of the question", () => {
    // An access route that answers about everybody when a parameter is omitted is the
    // Cartesian product of the estate wearing a query string.
    render(<ExplainLauncher />);

    expect(screen.getByLabelText("Principal")).toBeRequired();
    expect(screen.getByLabelText("Directory")).toBeRequired();
    expect(screen.getByLabelText("Host (optional)")).not.toBeRequired();
  });

  it("offers the access path, because a share ACL does not apply to local access", () => {
    render(<ExplainLauncher />);

    expect(screen.getByLabelText("Access path")).toHaveValue("remote_smb");
    expect(screen.getByRole("option", { name: /On the console/ })).toBeInTheDocument();
  });

  it("keeps what was already asked", () => {
    render(<ExplainLauncher principal="S-1-5-18" resource="\\\\FS01\\Finance" host="fs01" />);

    expect(screen.getByLabelText("Principal")).toHaveValue("S-1-5-18");
    expect(screen.getByLabelText("Host (optional)")).toHaveValue("fs01");
  });
});

describe("the heading", () => {
  it("names the pair, not the answer", () => {
    // "Why Alice can reach Payroll" is wrong for three of the four outcomes, and wrongest
    // for the one where nobody has collected enough to say.
    render(<ExplanationHeader explanation={indeterminate()} />);

    expect(screen.getByRole("heading", { name: "Access explanation" })).toBeInTheDocument();
    expect(screen.getByText(/against/)).toBeInTheDocument();
  });

  it("says which collected state the answer came from", () => {
    render(<ExplanationHeader explanation={twoRoutes()} />);

    expect(screen.getByText(/As of 2026-09-14T21:13:52/)).toBeInTheDocument();
  });

  it("says nobody has looked when no collector has ever run", () => {
    const base = twoRoutes();
    render(
      <ExplanationHeader explanation={{ ...base, basis: { ...base.basis, is_empty: true } }} />,
    );

    expect(screen.getByText(/No collector run is on record/)).toBeInTheDocument();
  });
});

describe("reaching the explanation from a finding", () => {
  // A non-expert administrator does not type a SID into a form. They are looking at a row
  // that says somebody has Modify on Payroll, and the question is "why?" — so the answer
  // has to be one click from that row, which is the acceptance criterion this covers.
  const resourceRow = (): ResourceAccessView => ({
    resource: resource(),
    share: share(),
    access: true,
    rights: rights(),
    certainty: "certain",
    limiting_layer: "ntfs",
    conditions: [],
  });

  const principalRow = (): PrincipalAccessView => ({
    principal: TEAM,
    access: true,
    rights: rights(),
    certainty: "certain",
    limiting_layer: "ntfs",
    conditions: [],
    via: [],
  });

  it("links from a reachable directory to the derivation for that pair", () => {
    render(
      <ResourceAccessTable items={[resourceRow()]} subject="directory" explainFor={ALICE_SID} />,
    );

    const link = screen.getByRole("link", { name: "Explain" });
    expect(link).toHaveAttribute(
      "href",
      `/access/explain?principal=${ALICE_SID}&resource=${encodeURIComponent(RESOURCE_KEY)}`,
    );
  });

  it("links from a principal on an ACL to the derivation for that pair", () => {
    render(<PrincipalAccessTable items={[principalRow()]} explainOn={RESOURCE_KEY} />);

    const link = screen.getByRole("link", { name: "Explain" });
    // The principal's storage key, not its bare SID: a local group's SID means different
    // things on different servers, and the key is what the API is anchored on.
    expect(link).toHaveAttribute(
      "href",
      `/access/explain?principal=${TEAM.key}&resource=${encodeURIComponent(RESOURCE_KEY)}`,
    );
  });

  it("offers no link at all when the caller did not say whose answer it is", () => {
    // A link built from a guess would explain the wrong pair, which is worse than no link.
    render(<ResourceAccessTable items={[resourceRow()]} subject="directory" />);

    expect(screen.queryByRole("link", { name: "Explain" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Why" })).not.toBeInTheDocument();
  });

  it("offers no link from a principal table without a directory", () => {
    render(<PrincipalAccessTable items={[principalRow()]} />);

    expect(screen.queryByRole("link", { name: "Explain" })).not.toBeInTheDocument();
  });
});
