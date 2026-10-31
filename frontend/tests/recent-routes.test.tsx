// @vitest-environment jsdom
/**
 * Where a view is recorded (ADG-UX-RECENT-05A §14 items 4–6, 16–17).
 *
 * The writer is mounted only on a detail page's `ok` branch, and the forget component only on
 * its `not_found` branch. Both are replaced here by markers carrying their props, so each
 * page's branching is asserted on its own; what the real components do with storage is
 * tested in recently-viewed.test.tsx.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiFailure, ApiResult, FailureKind } from "@/lib/api/client";
import { principalDetail, resourceDetail, shareDetail } from "./factories";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));

vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));

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

vi.mock("@/components/RecentlyViewed", () => ({
  RecordRecentView: (props: Record<string, string>) => (
    <i data-testid="record" data-kind={props.kind} data-key={props.entityKey} data-label={props.label} data-owner={props.owner} />
  ),
  ForgetRecentView: (props: Record<string, string>) => (
    <i data-testid="forget" data-kind={props.kind} data-key={props.entityKey} data-owner={props.owner} />
  ),
}));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { recentOwner } from "@/lib/recent-owner";
import PrincipalLayout from "@/app/identities/principal/[key]/layout";
import PrincipalViewPage from "@/app/identities/principal/[key]/[[...view]]/page";
import ServerPage from "@/app/resources/server/page";
import SharePage from "@/app/resources/share/page";
import DirectoryPage from "@/app/resources/directory/page";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });
const fail = (kind: FailureKind): ApiResult<never> => ({
  ok: false,
  failure: { kind, status: null, message: `The API said ${kind}.` } satisfies ApiFailure,
});

const OWNER = recentOwner("dev:ada");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue({
    status: "signed-in",
    session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z" },
    principal: {
      subject: "dev:ada",
      display_name: "Ada",
      email: null,
      source: "development",
      development: true,
      roles: ["auditor"],
      capabilities: ["identities:read", "resources:read", "access:read"],
      inactive_roles: [],
      unrecognized_roles: [],
      expires_at: null,
    },
  } as never);
  // Every secondary read fails: the page's own branch, not its panels, decides recording.
  for (const fn of Object.values(adg)) {
    vi.mocked(fn as () => Promise<unknown>).mockResolvedValue(fail("unreachable"));
  }
});

const search = (key: string) => ({ searchParams: Promise.resolve({ key }) });

const ROUTES = {
  identity: {
    primary: () => vi.mocked(adg.fetchPrincipalDetail),
    data: principalDetail({
      key: "S-1-5-21-1-2-3-1104",
      display_name: "Jane Smith",
      user_principal_name: "jane.smith@example.test",
    }),
    key: "S-1-5-21-1-2-3-1104",
    label: "Jane Smith",
    render: async (key: string) =>
      PrincipalLayout({ params: Promise.resolve({ key: encodeURIComponent(key) }), children: null }),
  },
  server: {
    primary: () => vi.mocked(adg.fetchServer),
    data: {
      key: "fs01",
      name: "FS01",
      dns_host_name: "fs01.corp.example.test",
      netbios_name: "FS01",
      computer_sid: null,
      domain_sid: null,
      is_domain_member: true,
      operating_system: null,
      share_count: 0,
      provenance: shareDetail().provenance,
    },
    key: "fs01",
    label: "FS01",
    render: async (key: string) => ServerPage(search(key)),
  },
  share: {
    primary: () => vi.mocked(adg.fetchShare),
    data: shareDetail(),
    key: "fs01|finance",
    label: "Finance",
    render: async (key: string) => SharePage(search(key)),
  },
  directory: {
    primary: () => vi.mocked(adg.fetchResource),
    data: resourceDetail({
      key: "\\\\host\\share\\department\\confidential\\project",
      path: "\\\\HOST\\Share\\Department\\Confidential\\Project",
      is_share_root: false,
    }),
    key: "\\\\host\\share\\department\\confidential\\project",
    label: "Project",
    render: async (key: string) => DirectoryPage(search(key)),
  },
} as const;

describe.each(Object.entries(ROUTES))("the %s detail route", (kind, route) => {
  it("records only after its primary read is ok, with the API's key and the short label", async () => {
    route.primary().mockResolvedValue(ok(route.data) as never);

    render(await route.render(route.key));

    const record = screen.getByTestId("record");
    expect(record.dataset).toMatchObject({ kind, key: route.key, label: route.label, owner: OWNER });
    expect(screen.queryByTestId("forget")).toBeNull();
  });

  it("forgets the entry when the object is not found", async () => {
    route.primary().mockResolvedValue(fail("not_found"));

    render(await route.render(route.key));

    expect(screen.getByTestId("forget").dataset).toMatchObject({ kind, key: route.key, owner: OWNER });
    expect(screen.queryByTestId("record")).toBeNull();
  });

  it.each(["forbidden", "server_error", "timeout", "unreachable", "invalid_request", "unauthenticated"] as const)(
    "neither records nor forgets on %s",
    async (failure) => {
      route.primary().mockResolvedValue(fail(failure));

      render(await route.render(route.key));

      expect(screen.queryByTestId("record")).toBeNull();
      expect(screen.queryByTestId("forget")).toBeNull();
    },
  );

  it("neither records nor forgets when signed out", async () => {
    vi.mocked(currentViewer).mockResolvedValue({ status: "signed-out", reason: "expired" } as never);
    route.primary().mockResolvedValue(ok(route.data) as never);

    render(await route.render(route.key));

    expect(screen.queryByTestId("record")).toBeNull();
    expect(screen.queryByTestId("forget")).toBeNull();
  });
});

describe("the identity route", () => {
  it("records in the layout, never in a view page, so switching views is the same entry", async () => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(ok(ROUTES.identity.data));
    const route = Promise.resolve({ key: encodeURIComponent(ROUTES.identity.key), view: ["effective-groups"] });

    render(await PrincipalViewPage({ params: route, searchParams: Promise.resolve({}) }));

    expect(screen.queryByTestId("record")).toBeNull();
  });

  it("never labels an identity with its UPN", async () => {
    vi.mocked(adg.fetchPrincipalDetail).mockResolvedValue(
      ok(principalDetail({ user_principal_name: "jane.smith@example.test" })),
    );

    render(await ROUTES.identity.render(ROUTES.identity.key));

    expect(screen.getByTestId("record").dataset.label).toBe("S-1-5-21-1-2-3-1104");
  });
});

describe("the directory route", () => {
  it("records the final segment, never the UNC path, as the label", async () => {
    vi.mocked(adg.fetchResource).mockResolvedValue(ok(ROUTES.directory.data));

    render(await ROUTES.directory.render(ROUTES.directory.key));

    const label = screen.getByTestId("record").dataset.label ?? "";
    expect(label).toBe("Project");
    expect(label).not.toContain("\\");
  });
});
