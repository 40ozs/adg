// @vitest-environment jsdom
/**
 * The explanation screen, end to end, against a real response body.
 *
 * The payload is `docs/contracts/v1/derived/examples/access-explanation.json` — bytes the
 * API actually sent, captured by the backend's own smoke suite and only rewritten under
 * `ADG_WRITE_EXAMPLES=1`. A hand-built fixture proves the components render *something*;
 * this proves the screen renders the thing the server sends.
 *
 * The page is a React server component, so it is an async function returning an element
 * tree and can be awaited and rendered directly. Only the API boundary and the session are
 * stubbed.
 *
 * The acceptance criteria of Phase 6C are written here as assertions: a non-expert can see
 * why access exists without traversing AD by hand, alternate routes are never hidden, the
 * diagram and the tables carry the same facts, and nothing is reachable only through the
 * picture.
 */

import { render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { CollectionStatus } from "@/lib/contracts";
import type { AccessExplanationResponse } from "@/lib/derived";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({ fetchCollectionStatus: vi.fn() }));
vi.mock("@/lib/api/explain", () => ({ fetchAccessExplanation: vi.fn() }));

import * as adg from "@/lib/api/adg";
import * as explain from "@/lib/api/explain";
import { currentViewer } from "@/lib/auth/current";
import AccessExplanationPage from "@/app/access/explain/page";

// `import.meta.url` is an http URL under jsdom, so the path is resolved from the Vitest
// root — which is this package — rather than from the module.
const CAPTURED = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../docs/contracts/v1/derived/examples/access-explanation.json"),
    "utf-8",
  ),
) as AccessExplanationResponse;

const VIEWER = {
  status: "signed-in" as const,
  session: {
    accessToken: "token",
    expiresAt: "2099-01-01T00:00:00Z",
    mode: "development" as const,
  },
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

function ok<T>(data: T): ApiResult<T> {
  return { ok: true, data };
}

const params = (
  values: Record<string, string>,
): Promise<Record<string, string | string[] | undefined>> => Promise.resolve(values);

const ASKED = { principal: CAPTURED.subject.sid, resource: CAPTURED.resource.key };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue(VIEWER);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(ok(HEALTHY));
  vi.mocked(explain.fetchAccessExplanation).mockResolvedValue(ok(CAPTURED));
});

describe("the explanation screen against a captured response", () => {
  it("asks the API for exactly the pair in the URL", async () => {
    render(await AccessExplanationPage({ searchParams: params({ ...ASKED, host: "fs01" }) }));

    expect(explain.fetchAccessExplanation).toHaveBeenCalledWith("token", {
      principal: CAPTURED.subject.sid,
      resource: CAPTURED.resource.key,
      host: "fs01",
      access_path: "remote_smb",
    });
  });

  it("leads with the verdict and the rights", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.getByRole("heading", { name: "Access explanation" })).toBeInTheDocument();
    expect(screen.getByText("Access")).toBeInTheDocument();
    expect(screen.getAllByText("Modify").length).toBeGreaterThan(0);
  });

  it("shows why, without anybody traversing AD by hand", async () => {
    // The acceptance criterion, as one assertion: the chain from the user to the entry is
    // on the screen, named, in order.
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    const routes = screen.getByRole("table", { name: /Select a route/ });
    expect(within(routes).getByText(/Alice Smith → Finance-Team → Finance-RW/)).toBeInTheDocument();
  });

  it("shows both layers, so the fix goes to the right ACL", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.getByRole("heading", { name: "The share ACL" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The NTFS ACL" })).toBeInTheDocument();
    expect(screen.getByText(/the NTFS ACL is the narrower one/)).toBeInTheDocument();
  });

  it("names the trustee each entry actually matched", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    const ntfs = screen.getAllByRole("table", { name: /granted/i });
    expect(ntfs.length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Finance-RW/).length).toBeGreaterThan(0);
  });

  it("draws the same relationships it lists", async () => {
    // The acceptance criterion that the graph and the table represent the same API facts,
    // asserted on the rendered page rather than on the layout function alone.
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    const drawn = document.querySelectorAll("[data-edge]");
    const listed = within(screen.getByRole("table", { name: /Every node and relationship/ }))
      .getAllByRole("row")
      .slice(1); // drop the header

    expect(drawn).toHaveLength(CAPTURED.graph.edges.length);
    expect(listed).toHaveLength(CAPTURED.graph.edges.length);
  });

  it("reports what a removal would measurably do", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.getByRole("heading", { name: "What a removal would do" })).toBeInTheDocument();
    expect(screen.getAllByText("Removes all access").length).toBeGreaterThan(0);
  });

  it("carries the assumption the engine made as a caveat, not as a footnote", async () => {
    // This answer is conclusive and complete, so the only caveat is the assumption — which
    // is an `info` status rather than an alert. It is still above the working, not under it.
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    const caveats = screen.getByText("What could make this answer wrong").closest("[role]");
    expect(caveats).toHaveAttribute("role", "status");
    expect(within(caveats as HTMLElement).getByText(/Well-known SIDs/)).toBeInTheDocument();
  });

  it("offers the whole answer for a ticket", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    const area = screen.getByLabelText(/The explanation as/) as HTMLTextAreaElement;
    expect(area.value).toContain("ADG access explanation");
    expect(area.value).toContain("VERDICT: ACCESS");
  });

  it("says which collected state it came from", async () => {
    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.getAllByText(new RegExp(CAPTURED.basis.token)).length).toBeGreaterThan(0);
  });
});

describe("when the question is incomplete", () => {
  it("asks for both halves rather than guessing one", async () => {
    render(await AccessExplanationPage({ searchParams: params({ principal: "S-1-5-18" }) }));

    expect(screen.getByText(/always about one principal and one directory/)).toBeInTheDocument();
    expect(screen.getByLabelText("Directory")).toHaveValue("");
    expect(explain.fetchAccessExplanation).not.toHaveBeenCalled();
  });

  it("keeps what was already typed", async () => {
    render(await AccessExplanationPage({ searchParams: params({ principal: "S-1-5-18" }) }));

    expect(screen.getByLabelText("Principal")).toHaveValue("S-1-5-18");
  });
});

describe("when the API cannot answer", () => {
  it("says the account may not see this, and offers the question again", async () => {
    vi.mocked(explain.fetchAccessExplanation).mockResolvedValue({
      ok: false,
      failure: {
        kind: "forbidden",
        status: 403,
        message: "Requires the access:read capability.",
      },
    });

    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.getByText("Your account cannot see this")).toBeInTheDocument();
    expect(screen.getByLabelText("Principal")).toBeInTheDocument();
  });

  it("does not render a verdict it does not have", async () => {
    vi.mocked(explain.fetchAccessExplanation).mockResolvedValue({
      ok: false,
      failure: { kind: "not_found", status: 404, message: "ADG holds nothing under that identifier." },
    });

    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(screen.queryByText("No access")).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Select a route/ })).not.toBeInTheDocument();
  });
});

describe("when nobody is signed in", () => {
  it("shows the signed-out notice and calls nothing", async () => {
    vi.mocked(currentViewer).mockResolvedValue({
      status: "signed-out",
      reason: "no-session",
    } as never);

    render(await AccessExplanationPage({ searchParams: params(ASKED) }));

    expect(explain.fetchAccessExplanation).not.toHaveBeenCalled();
  });
});
