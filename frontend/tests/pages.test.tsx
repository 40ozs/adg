// @vitest-environment jsdom
/**
 * The detail pages, end to end from a stubbed API to the DOM.
 *
 * These are the acceptance criteria of this phase, written as tests: an administrator can
 * walk from a user to every share that user reaches, and from a share to the people who
 * effectively reach it; a raw ACL and an engine answer never look like the same statement;
 * a long list pages server-side; and two groups with the same name are told apart.
 *
 * Every page here is a React server component, which is an async function returning an
 * element tree — so it can be awaited and rendered directly. The API module is stubbed at
 * its boundary, which is the point: these exercise the page's own data flow, its empty-state
 * attribution and its wiring, without a network or a database.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { CollectionStatus } from "@/lib/contracts";
import {
  boundary,
  ntfsAce,
  page,
  principal,
  principalDetail,
  provenance,
  resourceDetail,
  rights,
  shareAce,
  shareDetail,
} from "./factories";

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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/identities/principal",
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  redirect: vi.fn(),
}));

vi.mock("@/lib/auth/current", () => ({
  currentViewer: vi.fn(),
}));

vi.mock("@/lib/api/adg", () => ({
  fetchCollectionStatus: vi.fn(),
  fetchPrincipalDetail: vi.fn(),
  fetchDirectGroups: vi.fn(),
  fetchEffectiveGroups: vi.fn(),
  fetchDirectMembers: vi.fn(),
  fetchEffectiveMembers: vi.fn(),
  fetchTrusteeShares: vi.fn(),
  fetchAccessibleShares: vi.fn(),
  fetchAccessibleResources: vi.fn(),
  fetchResourcePrincipals: vi.fn(),
  fetchServer: vi.fn(),
  fetchServerShares: vi.fn(),
  fetchShare: vi.fn(),
  fetchShareAcl: vi.fn(),
  fetchShareRootAcl: vi.fn(),
  fetchResource: vi.fn(),
  fetchResourceAcl: vi.fn(),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import DirectoryPage from "@/app/resources/directory/page";
import LegacyPrincipalPage from "@/app/identities/principal/page";
import { notFound, redirect } from "next/navigation";
import PrincipalLayout from "@/app/identities/principal/[key]/layout";
import PrincipalViewPage from "@/app/identities/principal/[key]/[[...view]]/page";
import SharePage from "@/app/resources/share/page";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });

const HEALTHY: CollectionStatus = {
  health: "healthy",
  summary: "Every collector completed.",
  concerns: [],
  collectors: [],
};

const SIGNED_IN = {
  status: "signed-in" as const,
  session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z" },
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

beforeEach(() => {
  // Call history, not implementations: several assertions below are about a fetch *not*
  // happening, which a previous test's calls would satisfy.
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue(SIGNED_IN as never);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(ok(HEALTHY));
});

const params = (values: Record<string, string>) => Promise.resolve(values);

/**
 * A principal's page as Next.js renders it on first load: its layout, with the view page as
 * the layout's child, both from the path `/identities/principal/{key}/{view}`.
 */
async function principalPage(key: string, view?: string, search: Record<string, string> = {}) {
  const route = Promise.resolve({ key: encodeURIComponent(key), view: view ? [view] : undefined });
  return PrincipalLayout({
    params: route,
    children: await PrincipalViewPage({ params: route, searchParams: params(search) }),
  });
}

/** The value beside a term in a `dl.facts`. */
const factValue = (term: string): string =>
  screen.getByText(term).nextElementSibling?.textContent ?? "";

/* ------------------------------------------------------------ the user journey */

describe("a user's page", () => {
  const ADA = principalDetail({
    key: "S-1-5-21-1-2-3-1104",
    sid: "S-1-5-21-1-2-3-1104",
    display_name: "Ada Lovelace",
    sam_account_name: "ada",
    is_group: false,
    kind: "user",
    enabled: true,
    direct_group_count: 2,
  });

  beforeEach(() => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(ADA));
  });

  it("shows the SID, the account state, and the kind", async () => {
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(
      ok({ principal: ADA, items: [], page: page() }),
    );

    render(await principalPage(ADA.key));

    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Ada Lovelace");
    // The SID appears twice on purpose: once as the identity, once as the storage key.
    expect(screen.getAllByText("S-1-5-21-1-2-3-1104").length).toBeGreaterThan(0);
    expect(factValue("Kind")).toBe("user");
    expect(factValue("Enabled")).toContain("yes");
  });

  it("offers no member sections for something that is not a group", async () => {
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(
      ok({ principal: ADA, items: [], page: page() }),
    );

    render(await principalPage(ADA.key));

    expect(screen.queryByRole("link", { name: "Direct members" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Direct groups" })).toBeInTheDocument();
  });

  it("leads from the user to every share they reach", async () => {
    // The first half of the acceptance criterion: user -> shares.
    vi.mocked(adg.fetchAccessibleShares).mockResolvedValue(
      ok({
        subject: principal({ key: ADA.key, display_name: "Ada Lovelace" }),
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
              is_acl_boundary: false,
            },
            share: { key: "fs01|finance", name: "Finance", server_key: "fs01", observed: true },
            access: true,
            rights: rights({ label: "Modify", primary: "modify" }),
            certainty: "certain",
            limiting_layer: "ntfs",
            conditions: [],
          },
        ],
        page: page({ total: 1 }),
      }),
    );

    render(await principalPage(ADA.key, "shares"));

    const link = screen.getByRole("link", { name: "Finance" });
    expect(link).toHaveAttribute("href", "/resources/share?key=fs01%7Cfinance");
    expect(screen.getByText("Effective access — computed answer")).toBeInTheDocument();
  });

  it("summarizes the rights on the page as the page, not as the estate", async () => {
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
              key: "\\\\fs01\\finance\\reports",
              path: "\\\\FS01\\Finance\\Reports",
              share_key: "fs01|finance",
              observed: true,
              owner_sid: null,
              dacl_present: true,
              dacl_protected: false,
              is_acl_boundary: false,
            },
            share: null,
            access: true,
            rights: rights({ label: "Read", primary: "read" }),
            certainty: "certain",
            limiting_layer: "none",
            conditions: [],
          },
        ],
        page: page({ has_more: true, next_cursor: "eyJvIjoxMDAsInQiOiJvIn0" }),
      }),
    );

    render(await principalPage(ADA.key, "directories"));

    expect(screen.getByText(/directories on this page/)).toBeInTheDocument();
    expect(screen.getByText(/not a total for the estate/)).toBeInTheDocument();
  });

  it("pages a long list server-side, carrying the position in the link", async () => {
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(
      ok({
        principal: ADA,
        items: [
          {
            principal: principal({ key: "S-1-5-21-1-2-3-1200", display_name: "Finance" }),
            edge_kind: "member",
            edge_key: "e1",
            host_key: null,
            is_foreign_security_principal: false,
            first_observed_at: "2026-09-01T10:00:00Z",
            last_observed_at: "2026-09-12T10:00:00Z",
            last_observed_run_id: provenance.last_observed_run_id,
          },
        ],
        page: page({ has_more: true, next_cursor: "eyJrIjoiZmluYW5jZSIsInQiOiJrIn0" }),
      }),
    );

    render(await principalPage(ADA.key, "groups"));

    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      expect.stringContaining("p=eyJrIjoiZmluYW5jZSIsInQiOiJrIn0"),
    );
  });

  it("attributes an empty section to collection state rather than to absence", async () => {
    vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(
      ok({
        health: "failed",
        summary: "The NTFS collector failed on FS02.",
        concerns: [],
        collectors: [],
      }),
    );
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(
      ok({ principal: ADA, items: [], page: page() }),
    );

    render(await principalPage(ADA.key, "groups"));

    expect(screen.getByText(/but a collector failed/)).toBeInTheDocument();
  });
});

describe("a group's page", () => {
  const FINANCE = principalDetail({
    key: "S-1-5-21-1-2-3-1200",
    sid: "S-1-5-21-1-2-3-1200",
    display_name: "Finance",
    is_group: true,
    kind: "group",
    direct_member_count: 2,
  });

  beforeEach(() => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(FINANCE));
  });

  it("opens on its members and offers the graph around it", async () => {
    vi.mocked(adg.fetchDirectMembers).mockResolvedValue(
      ok({ group: FINANCE, items: [], page: page() }),
    );

    render(await principalPage(FINANCE.key));

    expect(screen.getByRole("link", { name: "Direct members" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const label of ["Effective members", "Parent groups", "Named on share ACLs"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("tells two same-named groups apart by the host that scopes them", async () => {
    const admins = (host: string) => ({
      principal: principal({
        key: `${host}|S-1-5-32-544`,
        sid: "S-1-5-32-544",
        host_key: host,
        display_name: "Administrators",
        is_group: true,
      }),
      depth: 1,
      path: [FINANCE.key, `${host}|S-1-5-32-544`],
      // Unlabelled on purpose: the chain falls back to keys rather than failing.
      path_principals: [],
      edge_kinds: ["member"],
      via_foreign_security_principal: false,
    });

    vi.mocked(adg.fetchEffectiveGroups).mockResolvedValue(
      ok({
        principal: FINANCE,
        items: [admins("fs01"), admins("fs02")],
        page: page(),
        traversal: {
          complete: true,
          limits: { max_depth: 10, max_nodes: 100, max_edges: 100, max_paths: 100 },
          depth_reached: 1,
          nodes_visited: 2,
          edges_read: 2,
          truncation: [],
        },
        cycles: [],
      }),
    );

    const { container } = render(
      await principalPage(FINANCE.key, "effective-groups"),
    );

    expect(container.textContent).toContain("(on fs01)");
    expect(container.textContent).toContain("(on fs02)");
    // The key is behind the name, not under it: each row opens its own group.
    expect(screen.getAllByRole("link", { name: "Administrators" }).map((link) => link.getAttribute("href"))).toEqual([
      "/identities/principal/fs01%7CS-1-5-32-544",
      "/identities/principal/fs02%7CS-1-5-32-544",
    ]);
    expect(container.textContent).not.toContain("fs01|S-1-5-32-544");
  });

  it("warns that an incomplete traversal is a lower bound, not a list", async () => {
    vi.mocked(adg.fetchEffectiveMembers).mockResolvedValue(
      ok({
        group: FINANCE,
        include: "non_groups",
        items: [],
        page: page(),
        traversal: {
          complete: false,
          limits: { max_depth: 2, max_nodes: 10, max_edges: 10, max_paths: 10 },
          depth_reached: 2,
          nodes_visited: 10,
          edges_read: 10,
          truncation: ["max_nodes"],
        },
        cycles: [],
      }),
    );

    render(
      await principalPage(FINANCE.key, "effective-members"),
    );

    // Empty page, so the traversal notice is not rendered; the empty state must still not
    // claim there are none.
    expect(screen.queryByText(/This is the whole list/)).not.toBeInTheDocument();
  });

  it("shows share entries naming the group as raw entries, not as access", async () => {
    vi.mocked(adg.fetchTrusteeShares).mockResolvedValue(
      ok({
        kind: "raw_smb_acl",
        trustee: principal({ key: FINANCE.key, display_name: "Finance" }),
        scope: "sid",
        items: [
          {
            share_key: "fs01|finance",
            share: null,
            aces: [shareAce({ trustee: principal({ key: FINANCE.key, display_name: "Finance" }) })],
          },
        ],
        page: page(),
      }),
    );

    render(
      await principalPage(FINANCE.key, "acl-references"),
    );

    expect(screen.getByText("Raw SMB share ACL — as collected")).toBeInTheDocument();
    expect(screen.getByText(/An entry here is not access/)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------- the share journey */

describe("a share's page", () => {
  const SHARE = shareDetail();

  beforeEach(() => {
    vi.mocked(adg.fetchShare).mockResolvedValue(ok(SHARE));
  });

  it("keeps the share ACL and the NTFS ACL beneath it as separate destinations", async () => {
    vi.mocked(adg.fetchShareAcl).mockResolvedValue(
      ok({
        kind: "raw_smb_acl",
        share_key: SHARE.key,
        share: SHARE,
        entries: [shareAce()],
        page: page(),
      }),
    );

    render(await SharePage({ searchParams: params({ key: SHARE.key }) }));

    expect(screen.getByRole("link", { name: "Share ACL" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Root NTFS ACL" })).toBeInTheDocument();
    expect(screen.getByText("Raw SMB share ACL — as collected")).toBeInTheDocument();
  });

  it("leads from the share to the people who effectively reach it", async () => {
    // The second half of the acceptance criterion: share -> effective users.
    vi.mocked(adg.fetchResourcePrincipals).mockResolvedValue(
      ok({
        resource: {
          key: "\\\\fs01\\finance",
          path: "\\\\FS01\\Finance",
          share_key: "fs01|finance",
          observed: true,
          owner_sid: null,
          dacl_present: true,
          dacl_protected: false,
          is_acl_boundary: false,
        },
        share: { key: "fs01|finance", name: "Finance", server_key: "fs01", observed: true },
        access_path: "remote_smb",
        enumeration: { complete: true, trustees_truncated: false, unenumerable_trustees: [] },
        findings: [],
        items: [
          {
            principal: principal({ key: "S-1-5-21-1-2-3-1104", display_name: "Ada Lovelace" }),
            access: true,
            rights: rights({ label: "Modify", primary: "modify" }),
            certainty: "certain",
            limiting_layer: "smb_share",
            conditions: [],
            via: [],
          },
        ],
        page: page({ total: 1 }),
      }),
    );

    render(await SharePage({ searchParams: params({ key: SHARE.key, tab: "effective" }) }));

    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute(
      "href",
      "/identities/principal/S-1-5-21-1-2-3-1104",
    );
    expect(screen.getByText("Effective access — computed answer")).toBeInTheDocument();
  });

  it("refuses to answer effective access when the directory beneath was never read", async () => {
    vi.mocked(adg.fetchShare).mockResolvedValue(ok(shareDetail({ root_resource: null })));

    render(await SharePage({ searchParams: params({ key: SHARE.key, tab: "effective" }) }));

    expect(screen.getByText("ADG cannot answer this yet")).toBeInTheDocument();
    expect(adg.fetchResourcePrincipals).not.toHaveBeenCalled();
  });
});

/* --------------------------------------------------------- the directory journey */

describe("a directory's page", () => {
  it("shows where permissions changed, with both boundary verdicts", async () => {
    vi.mocked(adg.fetchResource).mockResolvedValue(
      ok(
        resourceDetail({
          key: "\\\\fs01\\finance\\reports",
          path: "\\\\FS01\\Finance\\Reports",
          is_share_root: false,
          dacl_protected: true,
          inheritance_enabled: false,
          is_acl_boundary: true,
          boundary_reason: "protected",
          parent_path: "\\\\FS01\\Finance",
          boundary: boundary({
            reported: true,
            reported_reason: "protected",
            computed: false,
            agrees: false,
            parent_acl_hash_agrees: false,
          }),
        }),
      ),
    );
    vi.mocked(adg.fetchResourceAcl).mockResolvedValue(
      ok({
        kind: "raw_ntfs_acl",
        resource_key: "\\\\fs01\\finance\\reports",
        resource: null,
        acl_hash: null,
        entries: [ntfsAce({ source: "explicit", inherited_from: null })],
        page: page(),
      }),
    );

    render(
      await DirectoryPage({ searchParams: params({ key: "\\\\FS01\\Finance\\Reports" }) }),
    );

    expect(screen.getByText("Inheritance is blocked here")).toBeInTheDocument();
    expect(screen.getByText("This is an ACL boundary")).toBeInTheDocument();
    // Both verdicts, side by side. ADG's does not replace the collector's.
    expect(factValue("Collector's verdict")).toContain("a boundary");
    expect(factValue("ADG's verdict")).toContain("not a boundary");
  });

  it("announces a NULL DACL above everything else on the page", async () => {
    vi.mocked(adg.fetchResource).mockResolvedValue(
      ok(resourceDetail({ dacl_present: false, declared_ace_count: 0, stored_ace_count: 0 })),
    );
    vi.mocked(adg.fetchResourceAcl).mockResolvedValue(
      ok({
        kind: "raw_ntfs_acl",
        resource_key: "\\\\fs01\\finance",
        resource: null,
        acl_hash: null,
        entries: [],
        page: page(),
      }),
    );

    render(await DirectoryPage({ searchParams: params({ key: "\\\\FS01\\Finance" }) }));

    const alerts = screen.getAllByRole("alert");
    expect(alerts.some((alert) => alert.textContent?.includes("NULL DACL"))).toBe(true);
  });

  it("walks up its own path", async () => {
    vi.mocked(adg.fetchResource).mockResolvedValue(
      ok(
        resourceDetail({
          key: "\\\\fs01\\finance\\reports",
          path: "\\\\FS01\\Finance\\Reports",
          is_share_root: false,
        }),
      ),
    );
    vi.mocked(adg.fetchResourceAcl).mockResolvedValue(
      ok({
        kind: "raw_ntfs_acl",
        resource_key: "\\\\fs01\\finance\\reports",
        resource: null,
        acl_hash: null,
        entries: [],
        page: page(),
      }),
    );

    render(
      await DirectoryPage({ searchParams: params({ key: "\\\\FS01\\Finance\\Reports" }) }),
    );

    const location = screen.getByRole("navigation", { name: "Location" });
    expect(within(location).getByRole("link", { name: "FS01" })).toHaveAttribute(
      "href",
      "/resources/server?key=FS01",
    );
    expect(within(location).getByRole("link", { name: "Finance" })).toHaveAttribute(
      "href",
      "/resources/share?key=%5C%5CFS01%5CFinance",
    );
  });
});

/* ------------------------------------------------- moving through a principal */

describe("a principal's shortest chains", () => {
  const ADA = principal({ key: "S-1-5-21-1-2-3-1104", display_name: "Ada Lovelace", is_group: false });
  const TEAM = principal({ key: "S-1-5-21-1-2-3-1201", display_name: "Finance-Team", is_group: true });
  const RW = principal({ key: "S-1-5-21-1-2-3-1202", display_name: "Finance-RW", is_group: true });
  const ORPHAN = principal({ key: "S-1-5-21-1-2-3-7777", sid: "S-1-5-21-1-2-3-7777", resolved: false });
  const TRAVERSAL = {
    complete: true,
    limits: { max_depth: 10, max_nodes: 100, max_edges: 100, max_paths: 100 },
    depth_reached: 3,
    nodes_visited: 3,
    edges_read: 3,
    truncation: [],
  };

  beforeEach(() => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(principalDetail({ ...ADA, kind: "user" })));
    vi.mocked(adg.fetchEffectiveGroups).mockResolvedValue(
      ok({
        principal: principalDetail(ADA),
        items: [
          {
            principal: TEAM,
            depth: 1,
            path: [ADA.key, TEAM.key],
            path_principals: [ADA, TEAM],
            edge_kinds: ["member"],
            via_foreign_security_principal: false,
          },
          {
            principal: RW,
            depth: 3,
            path: [ADA.key, TEAM.key, ORPHAN.key, RW.key],
            path_principals: [ADA, TEAM, ORPHAN, RW],
            edge_kinds: ["member", "member", "member"],
            via_foreign_security_principal: false,
          },
        ],
        page: page(),
        traversal: TRAVERSAL,
        cycles: [],
      }),
    );
  });

  it("shows names, in order, where the API sent SIDs", async () => {
    const { container } = render(await principalPage(ADA.key, "effective-groups"));

    const chains = [...container.querySelectorAll(".principal-chain")].map((chain) => chain.textContent);
    expect(chains).toEqual([
      "Ada Lovelace → Finance-Team",
      "Ada Lovelace → Finance-Team → S-1-5-21-1-2-3-7777 → Finance-RW",
    ]);
  });

  it("links every described hop to its principal by key, and leaves an undescribed one as text", async () => {
    const { container } = render(await principalPage(ADA.key, "effective-groups"));

    const chain = container.querySelectorAll(".principal-chain")[1] as HTMLElement;
    const links = within(chain).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Ada Lovelace", `/identities/principal/${ADA.key}`],
      ["Finance-Team", `/identities/principal/${TEAM.key}`],
      ["Finance-RW", `/identities/principal/${RW.key}`],
    ]);
    // The same identity on two chains is the same link.
    const first = container.querySelectorAll(".principal-chain")[0] as HTMLElement;
    expect(within(first).getByRole("link", { name: "Finance-Team" })).toHaveAttribute(
      "href",
      `/identities/principal/${TEAM.key}`,
    );
    const orphan = within(chain).getByText("S-1-5-21-1-2-3-7777");
    expect(orphan.tagName).toBe("CODE");
    expect(orphan.closest("a")).toBeNull();
    // Shown because it did not resolve, held to one line, its key in full behind it.
    expect(orphan).toHaveClass("identifier", "sid");
    expect(orphan.getAttribute("title")).toContain(ORPHAN.key);
    // A described hop shows only its name; its key is the link's title.
    expect(links[1]).toHaveAttribute("title", TEAM.key);
  });

  it("keeps a hop recorded as unresolved unlinked, under the name the record holds", async () => {
    // ADG-DEMO-01: a record of kind `unresolved` exists (resolved: true) to say no authority
    // could say what the SID is. It is not a resolved principal, and the chain must not say so.
    const RECORDED = principal({
      key: "S-1-5-21-1-2-3-1108",
      sid: "S-1-5-21-1-2-3-1108",
      kind: "unresolved",
      last_known_name: "CORP\\lhansen",
      unresolved_reason: "deleted",
    });
    const PC = principal({ key: "S-1-5-21-1-2-3-3001", display_name: "WS-0042$", kind: "computer", is_group: false });
    vi.mocked(adg.fetchEffectiveGroups).mockResolvedValue(
      ok({
        principal: principalDetail(ADA),
        items: [
          {
            principal: RW,
            depth: 3,
            path: [ADA.key, RECORDED.key, PC.key, RW.key],
            path_principals: [ADA, RECORDED, PC, RW],
            edge_kinds: ["member", "member", "member"],
            via_foreign_security_principal: false,
          },
        ],
        page: page(),
        traversal: TRAVERSAL,
        cycles: [],
      }),
    );
    const { container } = render(await principalPage(ADA.key, "effective-groups"));

    const chain = container.querySelector(".principal-chain") as HTMLElement;
    expect(chain.textContent).toBe("Ada Lovelace → CORP\\lhansen → WS-0042$ → Finance-RW");
    const recorded = within(chain).getByText("CORP\\lhansen");
    expect(recorded.tagName).toBe("CODE");
    expect(recorded.closest("a")).toBeNull();
    expect(recorded.getAttribute("title")).toBe(`${RECORDED.key}: a run recorded this SID as unresolved`);
    // A computer account is a principal like any other: it opens its own page.
    expect(within(chain).getByRole("link", { name: "WS-0042$" })).toHaveAttribute(
      "href",
      `/identities/principal/${PC.key}`,
    );
  });
});

describe("a principal's identity information", () => {
  const ADA = principalDetail({ key: "S-1-5-21-1-2-3-1104", display_name: "Ada", is_group: false });

  beforeEach(() => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(ADA));
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(ok({ principal: ADA, items: [], page: page() }));
  });

  it("is a disclosure, open at first, that the reader can fold away", async () => {
    render(await principalPage(ADA.key));

    const heading = screen.getByRole("heading", { name: "Identity information" });
    const details = heading.closest("details") as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(true);
    expect(heading.closest("summary")).not.toBeNull();

    // The browser toggles a <details> from its <summary>; the content is the facts list.
    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(details.open).toBe(false);
    expect(within(details).getByText("SID")).toBeInTheDocument();
  });

  it("is in the layout, so moving to another view reads neither the identity nor renders it", async () => {
    // A view change renders the view page alone; the layout above it, with the disclosure
    // as the reader left it, is not rendered again.
    vi.mocked(adg.fetchEffectiveGroups).mockResolvedValue(
      ok({
        principal: ADA,
        items: [],
        page: page(),
        traversal: {
          complete: true,
          limits: { max_depth: 1, max_nodes: 1, max_edges: 1, max_paths: 1 },
          depth_reached: 0,
          nodes_visited: 0,
          edges_read: 0,
          truncation: [],
        },
        cycles: [],
      }),
    );
    const route = Promise.resolve({ key: ADA.key, view: ["effective-groups"] });

    render(await PrincipalViewPage({ params: route, searchParams: params({}) }));

    expect(adg.fetchPrincipalDetail).not.toHaveBeenCalled();
    expect(adg.fetchEffectiveGroups).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "Identity information" })).not.toBeInTheDocument();
  });

  it("says the same about deletion in the banner and in the facts", async () => {
    const ORPHAN = principalDetail({
      key: "S-1-5-21-1-2-3-1108",
      sid: "S-1-5-21-1-2-3-1108",
      kind: "unresolved",
      display_name: null,
      last_known_name: "CORP\\lhansen",
      unresolved_reason: "deleted",
      is_deleted: false,
    });
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(ORPHAN));
    vi.mocked(adg.fetchDirectGroups).mockResolvedValue(ok({ principal: ORPHAN, items: [], page: page() }));
    render(await principalPage(ORPHAN.key));

    expect(screen.getByRole("alert")).toHaveTextContent("recorded as unresolved (deleted)");
    const deleted = screen.getByText("Deleted").nextElementSibling as HTMLElement;
    expect(deleted.textContent).toMatch(/^yes — as the run that could not resolve this SID reported it$/);
  });

  it("refuses a view that does not exist", async () => {
    const route = Promise.resolve({ key: ADA.key, view: ["everything"] });

    await expect(PrincipalViewPage({ params: route, searchParams: params({}) })).rejects.toThrow(
      "NEXT_NOT_FOUND",
    );
    expect(notFound).toHaveBeenCalled();
  });

  it("keeps the old ?key= address working by redirecting to the key's path", async () => {
    await LegacyPrincipalPage({
      searchParams: params({ key: "fs01|S-1-5-32-544", tab: "effective-members", include: "all", p: "abc" }),
    });

    expect(redirect).toHaveBeenCalledWith(
      "/identities/principal/fs01%7CS-1-5-32-544/effective-members?p=abc&include=all",
    );
  });

  it("resolves the old ?host= form to the storage key before redirecting", async () => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(
      ok(principalDetail({ key: "fs01|S-1-5-32-544", sid: "S-1-5-32-544", host_key: "fs01" })),
    );

    await LegacyPrincipalPage({ searchParams: params({ key: "S-1-5-32-544", host: "FS01" }) });

    expect(adg.fetchPrincipalDetail).toHaveBeenCalledWith("token", "S-1-5-32-544", { host: "FS01" });
    expect(redirect).toHaveBeenCalledWith("/identities/principal/fs01%7CS-1-5-32-544");
  });
});
