// @vitest-environment jsdom
/**
 * Directory access: the summary is the default, the tree is lazy, All paths is unchanged.
 *
 * The page renders through the same layout + view route a browser hits, with the API
 * mocked at `lib/api/adg`, so what is asserted is what a reader sees and which requests the
 * page makes. The tree and the exceptions are client components; their requests go through
 * an injected fetcher and are counted, because "a branch opened twice is fetched once" is a
 * claim about requests, not about pixels.
 */

import type React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type {
  AccessRootView,
  CollectionStatus,
  DirectoryAccessResponse,
  DirectoryExceptionsResponse,
  DirectoryTreeNodeView,
  DirectoryTreeResponse,
  DirectoryView,
} from "@/lib/contracts";
import { BranchLoader, type BranchResult, directoryAccessHref, treeUrl } from "@/lib/directoryAccess";

import { page, principal, principalDetail, rights } from "./factories";

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
    <a href={href} data-prefetch={prefetch === false ? "off" : undefined} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/form", () => ({
  default: ({ children, action, ...rest }: { children: React.ReactNode; action: string }) => (
    <form action={action} {...rest}>
      {children}
    </form>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/identities/principal/S-1-5-21-1-2-3-1104/directories",
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  redirect: vi.fn(),
}));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));

vi.mock("@/lib/api/adg", () => ({
  fetchCollectionStatus: vi.fn(),
  fetchPrincipalDetail: vi.fn(),
  fetchAccessibleShares: vi.fn(),
  fetchAccessibleResources: vi.fn(),
  fetchDirectoryAccess: vi.fn(),
  fetchDirectoryAccessTree: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import PrincipalLayout from "@/app/identities/principal/[key]/layout";
import PrincipalViewPage from "@/app/identities/principal/[key]/[[...view]]/page";
import { DirectoryAccessTree } from "@/components/DirectoryAccessTree";
import { RootExceptions } from "@/components/RootExceptions";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });

const BASIS = {
  token: "basis-1",
  runs: 1,
  latest_run_id: null,
  latest_activity_at: null,
  observations_applied: 1,
  batches_received: 1,
  is_empty: false,
};

const HEALTHY: CollectionStatus = { health: "healthy", summary: "ok", concerns: [], collectors: [] };

const ADA = principalDetail({
  key: "S-1-5-21-1-2-3-1104",
  sid: "S-1-5-21-1-2-3-1104",
  display_name: "Ada Lovelace",
  is_group: false,
  kind: "user",
});

const MODIFY = rights({ mask: "0x001301BF", value: 0x001301bf, label: "Modify", primary: "Modify" });
const READ = rights({ mask: "0x001200A9", value: 0x001200a9, label: "Read & Execute", primary: "Read & Execute" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue({
    status: "signed-in",
    session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z" },
    principal: {
      subject: "ada",
      display_name: "Ada",
      email: null,
      source: "development",
      development: true,
      roles: ["viewer"],
      capabilities: ["identities:read", "access:read"],
      inactive_roles: [],
      unrecognized_roles: [],
      expires_at: null,
    },
  } as never);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(ok(HEALTHY));
  vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(ADA));
});

async function view(search: Record<string, string> = {}) {
  const route = Promise.resolve({ key: encodeURIComponent(ADA.key), view: ["directories"] });
  return PrincipalLayout({
    params: route,
    children: await PrincipalViewPage({ params: route, searchParams: Promise.resolve(search) }),
  });
}

function dir(path: string, over: Partial<DirectoryView> = {}): DirectoryView {
  const parts = path.split("\\");
  return {
    key: path.toLowerCase(),
    path,
    name: parts[parts.length - 1],
    server: parts[2],
    share: parts[3],
    share_key: `${parts[2]}|${parts[3]}`.toLowerCase(),
    relative_path: parts.slice(4).join("\\"),
    observed: true,
    is_acl_boundary: false,
    boundary_reason: null,
    dacl_protected: false,
    ...over,
  };
}

const LONG =
  "\\\\FS01\\Finance\\Budgets\\FY2026\\Forecasts\\Regional\\EMEA\\Northern-Europe\\Scandinavia\\Sweden\\Stockholm-Office";

function root(path: string, over: Partial<AccessRootView> = {}): AccessRootView {
  return {
    directory: dir(path),
    verdict: {
      access: true,
      outcome: "granted",
      rights: MODIFY,
      certainty: "certain",
      limiting_layer: "none",
      conditions: [],
    },
    reachable_descendants: 0,
    exception_count: 0,
    uniform: true,
    reasons: [],
    rights_breakdown: [{ rights: MODIFY, directories: 1 }],
    starts_because: "no_access",
    starts_because_label: "Below a folder without access",
    resumes_within: null,
    resumes_below: null,
    ...over,
  };
}

function summary(roots: AccessRootView[], over: Partial<DirectoryAccessResponse> = {}): DirectoryAccessResponse {
  const reachable = roots.reduce((sum, item) => sum + 1 + item.reachable_descendants, 0);
  return {
    basis: BASIS,
    subject: principal({ key: ADA.key }),
    access_path: "remote_smb",
    token: {
      subject: principal({ key: ADA.key }),
      assumption: "authenticated_user",
      access_path: "remote_smb",
      membership_complete: true,
      entries: [],
    },
    complete: true,
    truncation: [],
    totals: {
      reachable_directories: reachable,
      roots: roots.length,
      exceptions: roots.reduce((sum, item) => sum + item.exception_count, 0),
      shares: new Set(roots.map((item) => item.directory.share_key)).size,
      candidates_evaluated: reachable,
      unreachable_named: 0,
      observed_directories: reachable,
      files_excluded: 0,
    },
    access_levels: ["Modify", "Read & Execute"],
    roots,
    matched: roots.length,
    page: page({ total: roots.length }),
    ...over,
  };
}

const BUDGETS = root("\\\\FS01\\Finance\\Budgets", {
  reachable_descendants: 184,
  exception_count: 3,
  uniform: false,
  reasons: [
    { reason: "rights_widen", label: "Wider rights", count: 2 },
    { reason: "access_ends", label: "Access ends", count: 1 },
  ],
  rights_breakdown: [
    { rights: MODIFY, directories: 183 },
    { rights: rights({ mask: "0x001F01FF", value: 0x001f01ff, label: "Full control" }), directories: 2 },
  ],
});
const EXPORTS = root("\\\\FS01\\Finance\\Payroll\\Exports", {
  verdict: { ...BUDGETS.verdict, rights: READ },
  reachable_descendants: 12,
});
const CLIENT_A = root("\\\\FS02\\Projects\\Client A", { reachable_descendants: 9, exception_count: 0 });

/* ------------------------------------------------------------------- summary */

describe("the summary", () => {
  beforeEach(() => {
    vi.mocked(adg.fetchDirectoryAccess).mockResolvedValue(ok(summary([BUDGETS, EXPORTS, CLIENT_A])));
  });

  it("is the default view, and shows roots rather than every directory", async () => {
    render(await view());

    expect(adg.fetchDirectoryAccess).toHaveBeenCalledTimes(1);
    expect(adg.fetchAccessibleResources).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /3 access roots cover 208 directories/ })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + three roots, not 208
    expect(screen.getByRole("link", { name: "Directory access" })).toHaveAttribute("aria-current", "page");
  });

  it("tells a varied root apart from a uniform one", async () => {
    render(await view());

    const rows = screen.getAllByRole("row");
    const budgets = rows.find((row) => row.textContent?.includes("Budgets"));
    const client = rows.find((row) => row.textContent?.includes("Client A"));
    expect(budgets?.textContent).toContain("varies");
    expect(within(budgets as HTMLElement).getByText("varies")).toHaveAttribute(
      "title",
      expect.stringContaining("Permissions vary"),
    );
    expect(budgets?.textContent).toContain("184");
    expect(client?.textContent).not.toContain("varies");
    expect(client?.textContent).toContain("None");
  });

  it("sorts the descendant count as a number, without a request", async () => {
    render(await view());
    const before = vi.mocked(adg.fetchDirectoryAccess).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /Reachable descendants/ }));

    const names = screen.getAllByRole("rowheader").map((cell) => cell.querySelector("a")?.textContent);
    expect(names).toEqual(["Client A", "Payroll\\Exports", "Budgets"]); // 9, 12, 184
    expect(screen.getByRole("columnheader", { name: /Reachable descendants/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    expect(vi.mocked(adg.fetchDirectoryAccess).mock.calls.length).toBe(before);
  });

  it("sends its filters to the server and keeps them in the mode links", async () => {
    vi.mocked(adg.fetchDirectoryAccess).mockResolvedValue(
      ok(summary([BUDGETS], { matched: 1 })),
    );
    render(await view({ q: "finance", access: "Modify", share: "fs01|finance" }));

    expect(adg.fetchDirectoryAccess).toHaveBeenCalledWith(
      "token",
      ADA.key,
      expect.objectContaining({ q: "finance", access: "Modify", share: "fs01|finance" }),
    );
    expect(screen.getByRole("searchbox")).toHaveValue("finance");
    expect(screen.getByRole("combobox")).toHaveValue("Modify");
    expect(screen.getByRole("link", { name: "Summary" }).getAttribute("href")).toContain("q=finance");
    expect(screen.getByRole("link", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("says when filters match nothing, without calling the principal empty", async () => {
    vi.mocked(adg.fetchDirectoryAccess).mockResolvedValue(ok(summary([], { matched: 0, totals: { ...summary([BUDGETS]).totals } })));
    render(await view({ q: "nothing-like-this" }));

    expect(screen.getByText(/No access root matches these filters/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says why each root starts where it does, to a screen reader as well", async () => {
    render(await view());

    const header = screen.getAllByRole("rowheader")[0];
    expect(header).toHaveTextContent("Below a folder without access");
    expect(header.querySelector(".visually-hidden")).not.toBeNull();
  });

  it("holds a long path to one line and keeps all of it", async () => {
    vi.mocked(adg.fetchDirectoryAccess).mockResolvedValue(ok(summary([root(LONG)])));
    render(await view());

    const link = screen.getByRole("link", { name: /Stockholm-Office/ });
    expect(link).toHaveClass("identifier");
    expect(link).toHaveAttribute("title", LONG);
    expect(link.closest("table")).toHaveClass("directory-summary");
  });

  it("offers both exports, and the full one is every evaluated path", async () => {
    render(await view());

    expect(screen.getByRole("link", { name: "Download summary (CSV)" }).getAttribute("href")).toBe(
      `/api/directory-access/export?key=${encodeURIComponent(ADA.key)}&mode=summary`,
    );
    expect(
      screen.getByRole("link", { name: "Download every evaluated path (CSV)" }).getAttribute("href"),
    ).toContain("mode=paths");
  });

  it("warns when the summary covers part of the estate", async () => {
    vi.mocked(adg.fetchDirectoryAccess).mockResolvedValue(
      ok(summary([BUDGETS], { complete: false, truncation: ["candidates"] })),
    );
    render(await view());

    expect(screen.getByRole("alert")).toHaveTextContent(/covers part of the estate/);
  });
});

/* ------------------------------------------------------------------- modes */

describe("the modes", () => {
  it("keep the principal in the path and the mode in the query", () => {
    expect(directoryAccessHref(ADA.key, { mode: "tree" })).toBe(
      `/identities/principal/${encodeURIComponent(ADA.key)}/directories?mode=tree`,
    );
    expect(directoryAccessHref(ADA.key, { mode: "summary", q: "x" })).toBe(
      `/identities/principal/${encodeURIComponent(ADA.key)}/directories?q=x`,
    );
  });

  it("All paths is the unchanged flat listing, paged with its mode kept", async () => {
    vi.mocked(adg.fetchAccessibleResources).mockResolvedValue(
      ok({
        subject: principal({ key: ADA.key }),
        access_path: "remote_smb",
        token: {
          subject: principal({ key: ADA.key }),
          assumption: "authenticated_user",
          access_path: "remote_smb",
          membership_complete: true,
          entries: [],
        },
        items: [
          {
            resource: {
              key: "\\\\fs01\\finance\\budgets\\fy2026",
              path: "\\\\FS01\\Finance\\Budgets\\FY2026",
              share_key: "fs01|finance",
              observed: true,
              owner_sid: null,
              dacl_present: true,
              dacl_protected: false,
              is_acl_boundary: false,
            },
            share: null,
            access: true,
            rights: MODIFY,
            certainty: "certain",
            limiting_layer: "none",
            conditions: [],
          },
        ],
        page: page({ has_more: true, next_cursor: "next" }),
      }),
    );
    render(await view({ mode: "paths" }));

    expect(adg.fetchDirectoryAccess).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "\\\\FS01\\Finance\\Budgets\\FY2026" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All paths", current: "page" })).toBeInTheDocument();
    const next = screen.getByRole("link", { name: /next/i });
    expect(next.getAttribute("href")).toContain("mode=paths");
  });

  it("Tree renders the share roots collapsed and nothing beneath them", async () => {
    vi.mocked(adg.fetchDirectoryAccessTree).mockResolvedValue(ok(treeLevel(null, [FINANCE, PROJECTS])));
    render(await view({ mode: "tree" }));

    expect(adg.fetchDirectoryAccessTree).toHaveBeenCalledWith("token", ADA.key);
    const toggles = screen.getAllByRole("button", { name: /^Expand/ });
    expect(toggles).toHaveLength(2);
    toggles.forEach((toggle) => expect(toggle).toHaveAttribute("aria-expanded", "false"));
    expect(screen.queryByText("Budgets")).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------- tree */

function node(path: string, over: Partial<DirectoryTreeNodeView> = {}): DirectoryTreeNodeView {
  return {
    directory: dir(path),
    reachable: true,
    named: true,
    verdict: { ...BUDGETS.verdict },
    is_root: false,
    root_key: null,
    reasons: [],
    reachable_below: 0,
    child_count: 0,
    hidden_child_count: 0,
    ...over,
  };
}

const FINANCE = node("\\\\FS01\\Finance", {
  reachable: false,
  verdict: null,
  named: false,
  reachable_below: 196,
  child_count: 2,
  hidden_child_count: 4,
});
const PROJECTS = node("\\\\FS02\\Projects", { reachable: false, verdict: null, named: false, reachable_below: 10, child_count: 1 });
const BUDGETS_NODE = node("\\\\FS01\\Finance\\Budgets", { is_root: true, reachable_below: 184, child_count: 2 });
const PAYROLL_NODE = node("\\\\FS01\\Finance\\Payroll", { reachable: false, verdict: null, named: false, reachable_below: 12, child_count: 1 });
const FY2026 = node("\\\\FS01\\Finance\\Budgets\\FY2026", {
  reasons: [{ reason: "rights_widen", label: "Wider rights" }],
});
const LOCKED = node("\\\\FS01\\Finance\\Budgets\\Board", {
  reachable: false,
  verdict: null,
  named: false,
  reasons: [{ reason: "access_ends", label: "Access ends" }],
  directory: dir("\\\\FS01\\Finance\\Budgets\\Board", { dacl_protected: true, is_acl_boundary: true }),
});

function treeLevel(parent: DirectoryTreeNodeView | null, children: DirectoryTreeNodeView[]): DirectoryTreeResponse {
  return {
    basis: BASIS,
    subject: principal({ key: ADA.key }),
    complete: true,
    truncation: [],
    parent,
    children,
    page: page({ total: children.length }),
  };
}

function fakeServer() {
  const levels: Record<string, DirectoryTreeResponse> = {
    [FINANCE.directory.key]: treeLevel(FINANCE, [BUDGETS_NODE, PAYROLL_NODE]),
    [BUDGETS_NODE.directory.key]: treeLevel(BUDGETS_NODE, [FY2026, LOCKED]),
  };
  const requested: string[] = [];
  const fetchBranch = vi.fn(async (url: string): Promise<BranchResult> => {
    requested.push(url);
    const parent = new URL(url, "http://localhost").searchParams.get("parent") ?? "";
    const level = levels[parent];
    return level ? { ok: true, data: level } : { ok: false, message: "not offered" };
  });
  return { fetchBranch, requested };
}

describe("the tree", () => {
  it("loads a branch only when it is opened, through the proxy, by query parameter", async () => {
    const server = fakeServer();
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={server.fetchBranch} />);
    expect(server.fetchBranch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Expand \\\\FS01\\Finance" }));

    expect(await screen.findByText("Budgets")).toBeInTheDocument();
    expect(server.requested).toEqual([treeUrl(ADA.key, FINANCE.directory.key)]);
    expect(server.requested[0]).toMatch(/^\/api\/adg\/api\/v1\/access\/principals\/S-1-5-21-1-2-3-1104\/directory-access\/tree\?parent=/);
    expect(screen.getByRole("button", { name: "Collapse \\\\FS01\\Finance" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/4 other folder\(s\) without access/)).toBeInTheDocument();
  });

  it("opens a nested branch, marks where access changes and stops, and collapses", async () => {
    const server = fakeServer();
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={server.fetchBranch} />);

    fireEvent.click(screen.getByRole("button", { name: "Expand \\\\FS01\\Finance" }));
    fireEvent.click(await screen.findByRole("button", { name: "Expand Budgets" }));

    expect(await screen.findByText("FY2026")).toBeInTheDocument();
    expect(screen.getByText("Wider rights")).toHaveClass("badge-explicit");
    expect(screen.getByText("Access ends")).toHaveClass("badge-deny");
    expect(screen.getByText("access root")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Budgets" }));
    expect(screen.queryByText("FY2026")).not.toBeInTheDocument();
  });

  it("does not fetch a branch again after it was loaded once", async () => {
    const server = fakeServer();
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={server.fetchBranch} />);
    const finance = () => screen.getByRole("button", { name: /\\\\FS01\\Finance$/ });

    fireEvent.click(finance());
    await screen.findByText("Budgets");
    fireEvent.click(finance()); // collapse
    fireEvent.click(finance()); // open again
    await screen.findByText("Budgets");

    expect(server.fetchBranch).toHaveBeenCalledTimes(1);
  });

  it("opens and closes from the keyboard", async () => {
    const server = fakeServer();
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={server.fetchBranch} />);
    const toggle = screen.getByRole("button", { name: "Expand \\\\FS01\\Finance" });
    toggle.focus();

    fireEvent.keyDown(toggle, { key: "ArrowRight" });
    expect(await screen.findByText("Budgets")).toBeInTheDocument();
    expect(document.activeElement).toBe(toggle); // focus stays on the control
    fireEvent.keyDown(toggle, { key: "ArrowLeft" });
    expect(screen.queryByText("Budgets")).not.toBeInTheDocument();
  });

  it("says when a branch fails, and tries again on request", async () => {
    const fetchBranch = vi
      .fn<(url: string) => Promise<BranchResult>>()
      .mockResolvedValueOnce({ ok: false, message: "The API is unavailable." })
      .mockResolvedValueOnce({ ok: true, data: treeLevel(FINANCE, [BUDGETS_NODE]) });
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={fetchBranch} />);

    fireEvent.click(screen.getByRole("button", { name: "Expand \\\\FS01\\Finance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The API is unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Budgets")).toBeInTheDocument();
    expect(fetchBranch).toHaveBeenCalledTimes(2);
  });

  it("never navigates: expanding changes no address", async () => {
    const server = fakeServer();
    const before = window.location.href;
    render(<DirectoryAccessTree principalKey={ADA.key} initial={treeLevel(null, [FINANCE])} fetchBranch={server.fetchBranch} />);

    fireEvent.click(screen.getByRole("button", { name: "Expand \\\\FS01\\Finance" }));
    await screen.findByText("Budgets");

    expect(window.location.href).toBe(before);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
});

describe("the branch loader", () => {
  it("shares one request between simultaneous loads and caches the answer", async () => {
    let resolve: (value: BranchResult) => void = () => undefined;
    const fetchBranch = vi.fn(
      () => new Promise<BranchResult>((done) => {
        resolve = done;
      }),
    );
    const loader = new BranchLoader(ADA.key, fetchBranch);

    const first = loader.load("\\\\fs01\\finance");
    const second = loader.load("\\\\fs01\\finance");
    resolve({ ok: true, data: treeLevel(FINANCE, []) });
    await Promise.all([first, second]);
    await loader.load("\\\\fs01\\finance");

    expect(fetchBranch).toHaveBeenCalledTimes(1);
    expect(loader.requestCount).toBe(1);
  });

  it("forgets a failure so the next load asks again, and keeps branches apart", async () => {
    const fetchBranch = vi
      .fn<(url: string) => Promise<BranchResult>>()
      .mockResolvedValueOnce({ ok: false, message: "down" })
      .mockResolvedValue({ ok: true, data: treeLevel(null, []) });
    const loader = new BranchLoader(ADA.key, fetchBranch);

    await loader.load("\\\\fs01\\a");
    await loader.load("\\\\fs01\\a");
    await loader.load("\\\\fs01\\b");
    await loader.load("\\\\fs01\\b", "next-page");

    expect(fetchBranch).toHaveBeenCalledTimes(4);
  });
});

/* ------------------------------------------------------------------- exceptions */

describe("a root's exceptions", () => {
  const RESPONSE: DirectoryExceptionsResponse = {
    basis: BASIS,
    subject: principal({ key: ADA.key }),
    root: BUDGETS,
    items: [
      {
        directory: dir("\\\\FS01\\Finance\\Budgets\\FY2026"),
        reasons: [{ reason: "rights_widen", label: "Wider rights" }],
        reachable: true,
        named: true,
        verdict: { ...BUDGETS.verdict, rights: rights({ mask: "0x001F01FF", label: "Full control", primary: "Full control" }) },
        parent_key: BUDGETS.directory.key,
        parent_verdict: BUDGETS.verdict,
      },
      {
        directory: dir("\\\\FS01\\Finance\\Budgets\\Board", { dacl_protected: true }),
        reasons: [{ reason: "access_ends", label: "Access ends" }],
        reachable: false,
        named: false,
        verdict: null,
        parent_key: BUDGETS.directory.key,
        parent_verdict: BUDGETS.verdict,
      },
    ],
    page: page({ total: 2 }),
  };

  function exceptions(fetcher: (url: string) => Promise<DirectoryExceptionsResponse>) {
    return render(
      <RootExceptions
        principalKey={ADA.key}
        rootKey={BUDGETS.directory.key}
        rootName="Budgets"
        rootPath={BUDGETS.directory.path}
        count={2}
        reasons={BUDGETS.reasons}
        breakdown={[
          { label: "Modify", mask: "0x001301BF", directories: 183 },
          { label: "Full control", mask: "0x001F01FF", directories: 2 },
        ]}
        fetcher={fetcher}
      />,
    );
  }

  it("loads on first opening only, and lists where and why", async () => {
    const fetcher = vi.fn<(url: string) => Promise<DirectoryExceptionsResponse>>(async () => RESPONSE);
    const { container } = exceptions(fetcher);
    const details = container.querySelector("details") as HTMLDetailsElement;
    expect(fetcher).not.toHaveBeenCalled();

    await act(async () => {
      details.open = true;
      details.dispatchEvent(new Event("toggle"));
    });
    expect(await screen.findByText("…\\FY2026")).toHaveAttribute("title", "\\\\FS01\\Finance\\Budgets\\FY2026");
    expect(screen.getByText(/nothing on its ACL names this principal/)).toBeInTheDocument();
    expect(screen.getByText(/Rights across this run/)).toBeInTheDocument();

    await act(async () => {
      details.open = false;
      details.dispatchEvent(new Event("toggle"));
      details.open = true;
      details.dispatchEvent(new Event("toggle"));
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(fetcher.mock.calls[0][0]).toContain("/directory-access/exceptions?root=");
  });

  it("names its count and reasons for a screen reader in the disclosure", () => {
    exceptions(vi.fn(async () => RESPONSE));

    const summaryElement = screen.getByText("2").closest("summary");
    expect(summaryElement).toHaveAttribute(
      "aria-label",
      "2 exceptions under Budgets: Wider rights: 2, Access ends: 1",
    );
  });
});

/* ------------------------------------------------------------------- shares */

describe("reachable shares", () => {
  it("link each share to the principal's directory access inside it", async () => {
    vi.mocked(adg.fetchAccessibleShares).mockResolvedValue(
      ok({
        subject: principal({ key: ADA.key }),
        access_path: "remote_smb",
        token: {
          subject: principal({ key: ADA.key }),
          assumption: "authenticated_user",
          access_path: "remote_smb",
          membership_complete: true,
          entries: [],
        },
        items: [
          {
            resource: {
              key: "\\\\fs01\\finance",
              path: "\\\\FS01\\Finance",
              share_key: "fs01|finance",
              observed: true,
              owner_sid: null,
              dacl_present: true,
              dacl_protected: false,
              is_acl_boundary: true,
            },
            share: { key: "fs01|finance", name: "Finance", server_key: "fs01", observed: true },
            access: true,
            rights: MODIFY,
            certainty: "certain",
            limiting_layer: "none",
            conditions: [],
          },
        ],
        page: page(),
      }),
    );
    const route = Promise.resolve({ key: encodeURIComponent(ADA.key), view: ["shares"] });
    render(await PrincipalViewPage({ params: route, searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("link", { name: "Directory access" })).toHaveAttribute(
      "href",
      directoryAccessHref(ADA.key, { share: "fs01|finance" }),
    );
  });
});
