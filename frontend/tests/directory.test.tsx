// @vitest-environment jsdom
/**
 * The Users and Groups directories: a server-paged listing with a server-side search, whose
 * rows open the principal they name, and whose columns sort the page in hand.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { ListedPrincipalView } from "@/lib/contracts";
import { page, principal } from "./factories";

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
vi.mock("next/form", () => ({
  default: ({ children, action, ...rest }: { children: React.ReactNode; action: string }) => (
    <form action={action} {...rest}>
      {children}
    </form>
  ),
}));
vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({ fetchCollectionStatus: vi.fn(), fetchPrincipals: vi.fn() }));

import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import GroupsDirectoryPage from "@/app/identities/groups/page";
import UsersDirectoryPage from "@/app/identities/users/page";

const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });
const params = (values: Record<string, string>) => Promise.resolve(values);

const listed = (over: Partial<ListedPrincipalView>): ListedPrincipalView => ({
  ...principal(over),
  direct_member_count: 0,
  direct_group_count: 0,
  ...over,
});

const ADA = listed({
  key: "S-1-5-21-1-2-3-1104",
  sid: "S-1-5-21-1-2-3-1104",
  kind: "user",
  is_group: false,
  display_name: "Ada Lovelace",
  sam_account_name: "ada",
  enabled: true,
  direct_group_count: 12,
});
const BOB = listed({
  key: "S-1-5-21-1-2-3-1105",
  sid: "S-1-5-21-1-2-3-1105",
  kind: "user",
  is_group: false,
  display_name: "bob",
  sam_account_name: "bob",
  enabled: false,
  direct_group_count: 9,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(currentViewer).mockResolvedValue({
    status: "signed-in",
    session: { accessToken: "token", expiresAt: "2099-01-01T00:00:00Z" },
    principal: { capabilities: ["identities:read"] },
  } as never);
  vi.mocked(adg.fetchCollectionStatus).mockResolvedValue(
    ok({ health: "healthy", summary: "", concerns: [], collectors: [] }),
  );
});

const bodyRows = () => screen.getAllByRole("row").slice(1);

describe("the users directory", () => {
  beforeEach(() => {
    vi.mocked(adg.fetchPrincipals).mockResolvedValue(
      ok({ kind: "users", query: null, items: [ADA, BOB], page: page({ total: 2 }) }),
    );
  });

  it("asks the server for one page of users, not the whole domain", async () => {
    render(await UsersDirectoryPage({ searchParams: params({}) }));

    expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "users", {
      q: undefined,
      limit: 50,
      cursor: undefined,
    });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Users");
    expect(bodyRows()).toHaveLength(2);
  });

  it("passes a name or a SID to the server as the search", async () => {
    render(await UsersDirectoryPage({ searchParams: params({ q: " S-1-5-21-1-2-3-1104 " }) }));

    expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "users", {
      q: "S-1-5-21-1-2-3-1104",
      limit: 50,
      cursor: undefined,
    });
    expect(screen.getByRole("searchbox")).toHaveValue("S-1-5-21-1-2-3-1104");
    expect(screen.getByRole("search")).toHaveAttribute("action", "/identities/users");
  });

  it("opens a user by storage key", async () => {
    render(await UsersDirectoryPage({ searchParams: params({}) }));

    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute(
      "href",
      "/identities/principal/S-1-5-21-1-2-3-1104",
    );
  });

  it("names a user alone, and gives the SID a column of its own, one line and whole", async () => {
    render(await UsersDirectoryPage({ searchParams: params({}) }));

    const row = bodyRows()[0];
    expect(within(row).getByRole("rowheader").textContent).toBe("Ada Lovelace");
    const sid = within(row).getByText("S-1-5-21-1-2-3-1104");
    // Cut at its start, so the RID that tells it from its neighbours stays on screen.
    expect(sid).toHaveClass("identifier", "sid");
    expect(sid).toHaveAttribute("title", "S-1-5-21-1-2-3-1104");
    expect(within(row).getAllByRole("cell")[2]).toBe(sid.closest("td"));
  });

  it("sorts the page by name and by a count, numerically", async () => {
    render(await UsersDirectoryPage({ searchParams: params({}) }));
    const user = userEvent.setup();
    const first = () => within(bodyRows()[0]).getByRole("rowheader").textContent;

    await user.click(screen.getByRole("button", { name: /Name/ }));
    await user.click(screen.getByRole("button", { name: /Name/ }));
    expect(first()).toContain("bob");

    await user.click(screen.getByRole("button", { name: /Direct groups/ }));
    expect(first()).toContain("bob");
    expect(screen.getByRole("columnheader", { name: /Direct groups/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("links the three directories to each other", async () => {
    render(await UsersDirectoryPage({ searchParams: params({}) }));

    const tabs = screen.getByRole("navigation", { name: "Directories" });
    expect(within(tabs).getByRole("link", { name: "Servers" })).toHaveAttribute("href", "/resources");
    expect(within(tabs).getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Groups" })).toHaveAttribute(
      "href",
      "/identities/groups",
    );
  });
});

describe("the groups directory", () => {
  const FINANCE = listed({
    key: "S-1-5-21-1-2-3-1200",
    sid: "S-1-5-21-1-2-3-1200",
    kind: "domain_group",
    is_group: true,
    display_name: "Finance",
    group_scope: "global",
    direct_member_count: 3,
  });
  const ADMINS = listed({
    key: "fs01|S-1-5-32-544",
    sid: "S-1-5-32-544",
    host_key: "fs01",
    kind: "local_group",
    is_group: true,
    display_name: "Administrators",
    direct_member_count: 20,
  });

  beforeEach(() => {
    vi.mocked(adg.fetchPrincipals).mockResolvedValue(
      ok({ kind: "groups", query: "fin", items: [FINANCE, ADMINS], page: page({ total: 2 }) }),
    );
  });

  it("searches groups on the server and opens one by its key", async () => {
    render(await GroupsDirectoryPage({ searchParams: params({ q: "fin" }) }));

    expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "groups", {
      q: "fin",
      limit: 50,
      cursor: undefined,
    });
    expect(screen.getByRole("link", { name: "Administrators" })).toHaveAttribute(
      "href",
      "/identities/principal/fs01%7CS-1-5-32-544",
    );
  });

  it("shows a group's scope in the Scope column, not under its name", async () => {
    render(await GroupsDirectoryPage({ searchParams: params({ q: "fin" }) }));

    const row = bodyRows()[0];
    expect(within(row).getByRole("rowheader").textContent).toBe("Finance");
    expect(within(row).getAllByRole("cell")[2]).toHaveTextContent("global");
  });

  it("sorts members numerically, most first on the second activation", async () => {
    render(await GroupsDirectoryPage({ searchParams: params({ q: "fin" }) }));
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /Direct members/ }));
    await user.click(screen.getByRole("button", { name: /Direct members/ }));
    expect(within(bodyRows()[0]).getByRole("rowheader").textContent).toContain("Administrators");
  });

  it("says what was searched for when nothing matched", async () => {
    vi.mocked(adg.fetchPrincipals).mockResolvedValue(
      ok({ kind: "groups", query: "zzz", items: [], page: page({ total: 0 }) }),
    );
    render(await GroupsDirectoryPage({ searchParams: params({ q: "zzz" }) }));

    expect(document.body.textContent).toContain('groups matching "zzz"');
  });

  describe("Exclude local groups", () => {
    // A Domain Local group: an Active Directory group, whatever its scope says.
    const FINANCE_RW = listed({
      key: "S-1-5-21-1-2-3-1202",
      sid: "S-1-5-21-1-2-3-1202",
      kind: "domain_group",
      is_group: true,
      display_name: "Finance-RW",
      group_scope: "domain_local",
      direct_member_count: 7,
    });
    const toggle = () => screen.getByRole("checkbox", { name: "Exclude local groups" });
    // What next/form turns into a client-side navigation: the submit event and its fields.
    const submissions = (): Record<string, string>[] => {
      const seen: Record<string, string>[] = [];
      screen.getByRole("search").addEventListener("submit", (event) => {
        event.preventDefault();
        const form = event.target as HTMLFormElement;
        seen.push(Object.fromEntries(new FormData(form)) as Record<string, string>);
      });
      return seen;
    };

    it("is off by default, and the server is asked for every group", async () => {
      render(await GroupsDirectoryPage({ searchParams: params({ q: "fin" }) }));

      expect(toggle()).not.toBeChecked();
      expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "groups", {
        q: "fin",
        exclude_local: undefined,
        limit: 50,
        cursor: undefined,
      });
    });

    it("when on, has the server filter by kind, with the search kept", async () => {
      render(
        await GroupsDirectoryPage({ searchParams: params({ q: "fin", exclude_local: "1" }) }),
      );

      expect(toggle()).toBeChecked();
      expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "groups", {
        q: "fin",
        exclude_local: "true",
        limit: 50,
        cursor: undefined,
      });
      expect(screen.getByRole("searchbox")).toHaveValue("fin");
    });

    it("applies on change through the search form, keeping the search and dropping the page", async () => {
      render(
        await GroupsDirectoryPage({
          searchParams: params({ q: "fin", p: "c1", exclude_local: "1" }),
        }),
      );
      const seen = submissions();
      const href = window.location.href;

      await userEvent.setup().click(toggle());
      expect(seen).toEqual([{ q: "fin" }]);
      await userEvent.setup().click(toggle());
      expect(seen[1]).toEqual({ q: "fin", exclude_local: "1" });
      expect(window.location.href).toBe(href);
    });

    it("is a labelled checkbox that Space toggles", async () => {
      render(await GroupsDirectoryPage({ searchParams: params({}) }));
      const seen = submissions();
      const user = userEvent.setup();

      toggle().focus();
      await user.keyboard(" ");
      expect(seen).toEqual([{ q: "", exclude_local: "1" }]);
      expect(toggle()).toHaveAttribute("form", "directory-search-form");
      expect(screen.getByRole("search")).toHaveAttribute("id", "directory-search-form");
    });

    it("keeps the filter on the next page", async () => {
      vi.mocked(adg.fetchPrincipals).mockResolvedValue(
        ok({
          kind: "groups",
          query: "fin",
          items: [FINANCE, FINANCE_RW],
          page: page({ total: 120, has_more: true, next_cursor: "c2" }),
        }),
      );
      render(
        await GroupsDirectoryPage({ searchParams: params({ q: "fin", exclude_local: "1" }) }),
      );

      const next = new URL(screen.getByRole("link", { name: /Next/ }).getAttribute("href")!, "http://x");
      expect(next.searchParams.get("q")).toBe("fin");
      expect(next.searchParams.get("exclude_local")).toBe("1");
      expect(next.searchParams.get("p")).toBeTruthy();
    });

    it("keeps the reader's sort when the filter changes the rows", async () => {
      const { rerender } = render(await GroupsDirectoryPage({ searchParams: params({}) }));
      const user = userEvent.setup();
      const first = () => within(bodyRows()[0]).getByRole("rowheader").textContent;
      await user.click(screen.getByRole("button", { name: /Direct members/ }));
      await user.click(screen.getByRole("button", { name: /Direct members/ }));
      expect(first()).toContain("Administrators");

      vi.mocked(adg.fetchPrincipals).mockResolvedValue(
        ok({ kind: "groups", query: null, items: [FINANCE, FINANCE_RW], page: page({ total: 2 }) }),
      );
      rerender(await GroupsDirectoryPage({ searchParams: params({ exclude_local: "1" }) }));

      expect(first()).toBe("Finance-RW");
      expect(screen.getByRole("columnheader", { name: /Direct members/ })).toHaveAttribute(
        "aria-sort",
        "descending",
      );
    });

    it("follows Back and Forward: the box shows the filter the page was rendered with", async () => {
      const { rerender } = render(
        await GroupsDirectoryPage({ searchParams: params({ exclude_local: "1" }) }),
      );
      expect(toggle()).toBeChecked();

      rerender(await GroupsDirectoryPage({ searchParams: params({}) }));
      expect(toggle()).not.toBeChecked();
    });

    it("is not offered for users, and is not passed for them", async () => {
      vi.mocked(adg.fetchPrincipals).mockResolvedValue(
        ok({ kind: "users", query: null, items: [ADA], page: page({ total: 1 }) }),
      );
      render(await UsersDirectoryPage({ searchParams: params({ exclude_local: "1" }) }));

      expect(screen.queryByRole("checkbox")).toBeNull();
      expect(adg.fetchPrincipals).toHaveBeenCalledWith("token", "users", {
        q: undefined,
        exclude_local: undefined,
        limit: 50,
        cursor: undefined,
      });
    });
  });
});
