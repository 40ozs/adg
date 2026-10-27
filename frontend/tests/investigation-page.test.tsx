// @vitest-environment jsdom
/**
 * The guided access investigation page (ADG-DEMO-02), rendered end to end.
 *
 * The page is a server component, awaited and rendered directly, with only the API boundary
 * and the session stubbed — the same instrument `explanation-page.test.tsx` uses. The body
 * is `apollo()`, the canonical demo's response in miniature, so each assertion is one step
 * of the reviewer's walkthrough: the question, the observed answer, the route, the evidence
 * behind a hop, what could not be confirmed and whether it matters, the summary, the next
 * step. And the ones a screen gets wrong on its own: an unresolved identity rendered as
 * resolved, a state told apart by color alone, an unsettled SYSTEM answer worded as "no".
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { CollectionStatus, RuntimeInfo } from "@/lib/contracts";
import type { AccessExplanationResponse } from "@/lib/derived";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({
  fetchCollectionCoverage: vi.fn(),
  fetchCollectionStatus: vi.fn(),
  fetchRuntime: vi.fn(),
}));
vi.mock("@/lib/api/explain", () => ({ fetchAccessExplanation: vi.fn() }));

import * as adg from "@/lib/api/adg";
import * as explain from "@/lib/api/explain";
import { currentViewer } from "@/lib/auth/current";
import AccessInvestigationPage from "@/app/access/investigate/page";
import {
  APOLLO_PATH,
  FATIMA_SID,
  ORPHAN_SID,
  apollo,
  apolloFromAnOlderServer,
  apolloWithUnreadShare,
  systemConflictingInvestigated,
  systemOverShareInvestigated,
} from "./investigation-factories";
import { apolloCoverage, fs03Coverage } from "./coverage-factories";

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
    capabilities: ["identities:read", "resources:read", "access:read"],
    inactive_roles: [],
    unrecognized_roles: [],
    expires_at: null,
  },
};

const HEALTHY: CollectionStatus = {
  health: "healthy",
  summary: "Every collector reported.",
  concerns: [],
  collectors: [],
};

const DEMO: RuntimeInfo = {
  runtime_mode: "demo",
  data_source: "synthetic",
  dataset: null,
} as unknown as RuntimeInfo;

function ok<T>(data: T): ApiResult<T> {
  return { ok: true, data };
}

const ASKED = { principal: FATIMA_SID, resource: APOLLO_PATH };

async function open(
  body: AccessExplanationResponse = apollo(),
  query: Record<string, string> = ASKED,
): Promise<void> {
  vi.mocked(explain.fetchAccessExplanation).mockResolvedValue(ok(body));
  render(await AccessInvestigationPage({ searchParams: Promise.resolve(query) }));
}

function section(name: RegExp): HTMLElement {
  return screen.getByRole("region", { name });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue(VIEWER);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(ok(HEALTHY));
  vi.mocked(adg.fetchRuntime).mockResolvedValue(ok(DEMO));
  vi.mocked(adg.fetchCollectionCoverage).mockResolvedValue(ok(apolloCoverage()));
});

describe("1. the question", () => {
  it("asks the API for exactly the pair in the URL, once", async () => {
    await open(apollo(), { ...ASKED, access_path: "remote_smb" });

    expect(explain.fetchAccessExplanation).toHaveBeenCalledTimes(1);
    expect(explain.fetchAccessExplanation).toHaveBeenCalledWith("token", {
      principal: FATIMA_SID,
      resource: APOLLO_PATH,
      host: undefined,
      access_path: "remote_smb",
    });
  });

  it("states the investigation question as the page heading", async () => {
    await open();

    expect(
      screen.getByRole("heading", { level: 1, name: `Why does Fatima Gordon have access to ${APOLLO_PATH}?` }),
    ).toBeInTheDocument();
  });

  it("says the scenario is synthetic on a demo deployment", async () => {
    await open();

    expect(screen.getByRole("note", { name: "" })).toHaveTextContent(/Synthetic scenario/);
  });

  it("does not claim synthetic data on a production deployment", async () => {
    vi.mocked(adg.fetchRuntime).mockResolvedValue(
      ok({ ...DEMO, runtime_mode: "production", data_source: "collectors" } as RuntimeInfo),
    );
    await open();

    expect(screen.queryByText(/Synthetic scenario/)).not.toBeInTheDocument();
  });

  it("offers the investigation launcher when the pair is incomplete", async () => {
    vi.mocked(explain.fetchAccessExplanation).mockResolvedValue(ok(apollo()));
    const { container } = render(
      await AccessInvestigationPage({ searchParams: Promise.resolve({ principal: FATIMA_SID }) }),
    );

    expect(explain.fetchAccessExplanation).not.toHaveBeenCalled();
    expect(container.querySelector("form")?.getAttribute("action")).toBe("/access/investigate");
    expect(screen.getByRole("button", { name: "Investigate this access" })).toBeInTheDocument();
    expect(screen.getByLabelText("Principal")).toHaveValue(FATIMA_SID);
  });
});

describe("2. the observed answer, apart from its explanation", () => {
  it("shows the effective access, rights, certainty and both ACLs", async () => {
    await open();
    const observed = section(/What ADG evaluated/);

    expect(within(observed).getByText("Access present")).toBeInTheDocument();
    expect(within(observed).getByText("Modify")).toBeInTheDocument();
    expect(within(observed).getByText("at_least")).toBeInTheDocument();
    expect(within(observed).getByText(/Over the network \(share ACL and NTFS ACL\)/)).toBeInTheDocument();
    expect(within(observed).getByText(/collector runs on record/)).toBeInTheDocument();
  });
});

describe("3. the route", () => {
  it("shows one route per ACL, each classified in words", async () => {
    await open();
    const routes = section(/the route that carries the access/);

    const ntfs = within(routes).getByRole("article", { name: "Folder permissions (NTFS ACL)" });
    expect(within(ntfs).getByText("Direct entry")).toBeInTheDocument();
    expect(within(ntfs).getByText("Set explicitly on this folder")).toBeInTheDocument();
    expect(within(ntfs).getByText(/Removing it changes nothing/)).toBeInTheDocument();

    const share = within(routes).getByRole("article", { name: "Share permissions (SMB share ACL)" });
    expect(within(share).getByText("Through 1 group membership")).toBeInTheDocument();
    expect(within(share).getByText("Set on the share")).toBeInTheDocument();
  });

  it("lists the hops of the share route in order, principal first", async () => {
    await open();
    const share = screen.getByRole("article", { name: "Share permissions (SMB share ACL)" });
    const hops = within(share).getByRole("list", { name: /Hops on route p0002/ });

    const sentences = within(hops)
      .getAllByRole("listitem")
      .map((item) => item.querySelector("p")?.textContent);
    expect(sentences).toEqual([
      "group membership Fatima Gordon is a member of ENG-Mobile",
      "ACL entry Share entry #3 names ENG-Mobile: Allow Modify",
      "entry applies to the object Share entry #3 applies to this share, set explicitly",
    ]);
  });

  it("keeps the derived, redundant routes one disclosure away, with their hops", async () => {
    const user = userEvent.setup();
    await open();
    const others = screen.getByText(/Every other route ADG found \(2\)/);
    const disclosure = others.closest("details") as HTMLDetailsElement;

    expect(disclosure.open).toBe(false);
    await user.click(others);
    expect(disclosure.open).toBe(true);
    expect(
      within(disclosure).getByText(/Fatima Gordon → ENG-Mobile → Engineering-Frontend → Engineering → Engineering-FS → Share_Engineering_Modify/),
    ).toHaveTextContent(/Redundant, derived \(5 groups\), inherited/);
  });
});

describe("4. the evidence behind a hop", () => {
  it("is a native disclosure per hop, named by the hop and its basis", async () => {
    await open();
    const ntfs = screen.getByRole("article", { name: "Folder permissions (NTFS ACL)" });
    const summaries = ntfs.querySelectorAll("details > summary");

    expect(summaries).toHaveLength(2);
    expect(summaries[0]).toHaveTextContent("Evidence for hop 1: Observed by the NTFS collector");
    for (const summary of summaries) {
      // The first child of a <details>: what makes it keyboard-operable in every browser.
      expect(summary.parentElement?.firstElementChild).toBe(summary);
    }
  });

  it("opens to the collector, the scans and the stored row", async () => {
    const user = userEvent.setup();
    await open();
    const share = screen.getByRole("article", { name: "Share permissions (SMB share ACL)" });
    const [membership] = within(share).getAllByText(/Evidence for hop 1/);
    const disclosure = membership.closest("details") as HTMLDetailsElement;

    expect(membership).toHaveTextContent("Observed by the Active Directory collector");
    await user.click(membership);
    expect(disclosure.open).toBe(true);
    expect(within(disclosure).getByText("First observed")).toBeInTheDocument();
    expect(within(disclosure).getByText(/2026-09-03 03:12 UTC/)).toBeInTheDocument();
    expect(within(disclosure).getByText(/2026-10-01 03:12 UTC/)).toBeInTheDocument();
    expect(within(disclosure).getByText(/membership row/)).toBeInTheDocument();
  });

  it("says provenance was not reported by an older server, not that a hop is unsupported", async () => {
    await open(apolloFromAnOlderServer());

    expect(screen.getByText(/carries no investigation summary/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /What ADG evaluated/ })).not.toBeInTheDocument();
  });
});

describe("5. what ADG could not confirm", () => {
  it("names each gap in words and a symbol, never by color alone", async () => {
    await open();
    const gaps = section(/What ADG could not confirm/);
    const [questions] = within(gaps).getAllByRole("list");
    const items = within(questions).getAllByRole("listitem");

    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item).toHaveTextContent("Missing evidence");
      expect(item).toHaveTextContent("Does not affect the conclusion.");
    }
    // The symbol is decoration; the word carries the meaning for assistive technology.
    expect(within(items[0]).getByText("?", { exact: false, selector: "[aria-hidden='true']" })).toBeInTheDocument();
  });

  it("keeps a stored-but-unresolved identity visibly unresolved (ADG-DEMO-01)", async () => {
    await open();
    const gaps = section(/What ADG could not confirm/);
    const orphan = within(gaps).getByText("GPCORP\\lhansen");
    const item = orphan.closest("li") as HTMLElement;

    expect(within(item).getByText(ORPHAN_SID)).toBeInTheDocument();
    const note = within(item).getByText(/unresolved \(deleted\)/);
    expect(note).toHaveClass("status-warn");
  });

  it("keeps the assumption for context, collapsed, and says it reaches nothing", async () => {
    await open();
    const context = screen.getByText(/Recorded for context \(1\)/).closest("details") as HTMLElement;

    expect(within(context).getByText(/Assumed, not observed/)).toBeInTheDocument();
    expect(within(context).getByText(/cannot change this answer/)).toBeInTheDocument();
  });

  it("says when an open question does reach the conclusion", async () => {
    await open(apolloWithUnreadShare());
    const gaps = section(/What ADG could not confirm/);

    expect(within(gaps).getByText(/Affects the conclusion\./)).toBeInTheDocument();
    expect(within(section(/Investigation summary/)).getByText(/provisional/)).toBeInTheDocument();
  });
});

describe("6. the summary and the next step", () => {
  it("answers the seven questions", async () => {
    await open();
    const summary = section(/Investigation summary/);
    const questions = summary.querySelectorAll("dt");

    expect([...questions].map((item) => item.textContent)).toEqual([
      "Is access present?",
      "What is the strongest supported path?",
      "Is that path direct or inherited?",
      "What evidence supports it?",
      "What evidence remains incomplete or ambiguous?",
      "Does the uncertainty affect the final conclusion?",
      "What should an investigator inspect next?",
    ]);
    expect(within(summary).getByText(/none of them can take that access away/)).toBeInTheDocument();
  });

  it("lists the engine's next steps, each with a link to act on it", async () => {
    await open();
    const next = section(/What to inspect next/);
    const steps = within(next).getAllByRole("listitem");

    expect(steps).toHaveLength(3);
    expect(within(steps[0]).getByRole("link", { name: "Inspect the SID" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(ORPHAN_SID)),
    );
    expect(within(steps[1]).getByRole("link", { name: "Inspect the group" })).toBeInTheDocument();
    expect(within(steps[2]).getByRole("link", { name: "Open the removal analysis" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/access\/explain\?.*#removals$/),
    );
  });

  it("links to the full explanation", async () => {
    await open();

    expect(screen.getByRole("link", { name: "Open the full explanation" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/access\/explain\?principal=/),
    );
  });
});

describe("ADG-DEMO-01 carried through the investigation", () => {
  it("does not word an ambiguous SYSTEM answer as no access", async () => {
    await open(systemOverShareInvestigated());

    expect(screen.getAllByText("Not determined").length).toBeGreaterThan(0);
    expect(screen.queryByText("No access")).not.toBeInTheDocument();
    const gaps = section(/What ADG could not confirm/);
    expect(within(gaps).getByText("Ambiguous")).toBeInTheDocument();
    expect(within(gaps).getByText(/Affects the conclusion\./)).toBeInTheDocument();
    expect(section(/the route that carries the access/)).toHaveTextContent(/No route delivers a right/);
  });

  it("never prints an empty layer as 'No access' on an inconclusive answer", async () => {
    const body = systemOverShareInvestigated();
    await open({
      ...body,
      effective: {
        ...body.effective,
        ntfs: { ...body.effective.ntfs, rights: { ...body.effective.ntfs.rights, value: 0, mask: "0x00000000", label: "No access" } },
      },
    });
    const observed = section(/What ADG evaluated/);

    expect(within(observed).getByText(/NTFS: none established/)).toBeInTheDocument();
    expect(within(observed).queryByText(/No access/)).not.toBeInTheDocument();
  });

  it("keeps a conflicting description visible, in words", async () => {
    await open(systemConflictingInvestigated());

    expect(within(section(/What ADG could not confirm/)).getByText("Conflicting evidence")).toBeInTheDocument();
  });
});

describe("keyboard and landmarks", () => {
  it("gives every section a heading that names its region", async () => {
    await open();

    for (const name of [
      /What ADG evaluated/,
      /the route that carries the access/,
      /What ADG could not confirm/,
      /Investigation summary/,
      /What to inspect next/,
      /How complete is the evidence behind this answer/,
    ]) {
      expect(section(name)).toBeInTheDocument();
    }
  });

  it("reaches every link by Tab, in document order", async () => {
    const user = userEvent.setup();
    await open();
    const links = screen.getAllByRole("link");

    await user.tab();
    expect(document.activeElement).toBe(links[0]);
    expect(links.every((link) => link.getAttribute("tabindex") !== "-1")).toBe(true);
  });
});

describe("7. how complete the evidence behind the answer is (ADG-DEMO-03)", () => {
  it("asks for the scopes this folder's answer reads, once", async () => {
    await open();

    expect(adg.fetchCollectionCoverage).toHaveBeenCalledTimes(1);
    expect(adg.fetchCollectionCoverage).toHaveBeenCalledWith("token", { resource: APOLLO_PATH });
  });

  it("names the collectors that supplied the evidence, each with its state in words", async () => {
    await open();
    const panel = section(/How complete is the evidence behind this answer/);
    const scopes = within(panel).getByRole("list", { name: "Collection scopes this answer reads" });

    const items = within(scopes).getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(items).toHaveLength(4);
    expect(items[0]).toContain("active_directory (corp.geneseepress.example)");
    expect(items.every((item) => item.includes("Collected"))).toBe(true);
    expect(items.join(" ")).toContain("ntfs (GP-ENG-FS01)");
  });

  it("says the incomplete scopes elsewhere cannot change this answer, and names them", async () => {
    await open();
    const panel = section(/How complete is the evidence behind this answer/);

    expect(
      within(panel).getByText("Missing collection elsewhere cannot change this answer."),
    ).toBeInTheDocument();
    const elsewhere = within(panel).getByRole("list", {
      name: "Incomplete scopes this answer does not read",
    });
    expect(elsewhere.textContent).toContain("ntfs (GP-FS-03): the latest scan could not reach its target");
    expect(elsewhere.textContent).toContain("Unavailable");
    expect(within(panel).getByText(/98 of 100 collection scopes collected completely/)).toBeInTheDocument();
  });

  it("says plainly when an incomplete scope could change the answer", async () => {
    vi.mocked(adg.fetchCollectionCoverage).mockResolvedValue(ok(fs03Coverage()));
    await open();
    const panel = section(/How complete is the evidence behind this answer/);

    expect(
      within(panel).getByText("Missing or older collection could change this answer."),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/Older evidence, from the scan of 2026-09-17/)).toBeInTheDocument();
  });

  it("leaves the answer's own certainty to the answer", async () => {
    await open();
    const panel = section(/How complete is the evidence behind this answer/);

    expect(within(panel).getByText(/Context beside the answer, not part of it/)).toBeInTheDocument();
    // Section 1 still states the engine's certainty, unchanged by coverage.
    expect(section(/What ADG evaluated/).textContent).toMatch(/at least|lower bound/i);
  });

  it("renders the investigation unchanged when coverage cannot be read", async () => {
    vi.mocked(adg.fetchCollectionCoverage).mockResolvedValue({
      ok: false,
      failure: { kind: "forbidden", status: 403, message: "No collectors:read." },
    });
    await open();

    expect(screen.queryByRole("region", { name: /How complete is the evidence/ })).toBeNull();
    expect(section(/What ADG evaluated/)).toBeInTheDocument();
  });
});
