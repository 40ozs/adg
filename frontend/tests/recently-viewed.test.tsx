// @vitest-environment jsdom
/**
 * The Recently viewed components and the sign-out clear, against jsdom's real
 * `sessionStorage` (ADG-UX-RECENT-05A §14 items 5–6, 9–13, 18–19).
 *
 * `fetch` is a spy throughout: recording, reading, filtering and clearing the list must never
 * make a request, and sign-out must clear the list before its one request starts.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ForgetRecentView, RecentlyViewed, RecordRecentView } from "@/components/RecentlyViewed";
import { SignOutButton } from "@/components/SignOutButton";
import { RECENT_MAX_AGE_MS, RECENT_STORAGE_KEY, readRecent, recordRecent } from "@/lib/recent";
import { recentOwner } from "@/lib/recent-owner";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
}));

const ADA = recentOwner("dev:ada");
const BOB = recentOwner("dev:bob");
const BOTH = ["identities:read", "resources:read"];

const fetchSpy = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));

beforeEach(() => {
  sessionStorage.clear();
  fetchSpy.mockClear();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function seed(owner = ADA) {
  const now = Date.now();
  recordRecent(sessionStorage, owner, { kind: "identity", key: "S-1-5-21-1-2-3-1104", label: "Jane Smith" }, now - 4000);
  recordRecent(sessionStorage, owner, { kind: "server", key: "fs01", label: "FS01" }, now - 3000);
  recordRecent(sessionStorage, owner, { kind: "share", key: "fs01|finance", label: "Finance" }, now - 2000);
  recordRecent(sessionStorage, owner, { kind: "directory", key: "\\\\fs01\\finance\\q\\2026", label: "2026" }, now - 1000);
}

function rows() {
  return within(screen.getByRole("list", { name: "Recently viewed items" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
}

describe("RecordRecentView", () => {
  it("records after mount, once per (kind, key), even under StrictMode's double effect", () => {
    const view = <RecordRecentView owner={ADA} kind="server" entityKey="fs01" label="FS01" />;
    const { rerender } = render(<StrictMode>{view}</StrictMode>);
    rerender(<StrictMode>{view}</StrictMode>);

    expect(readRecent(sessionStorage, ADA).map((e) => e.key)).toEqual(["fs01"]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("does not record during server rendering", () => {
    renderToString(<RecordRecentView owner={ADA} kind="server" entityKey="fs01" label="FS01" />);

    expect(sessionStorage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });
});

describe("ForgetRecentView", () => {
  it("deletes that entry and leaves the rest", () => {
    seed();
    render(<ForgetRecentView owner={ADA} kind="server" entityKey="fs01" />);

    expect(readRecent(sessionStorage, ADA).map((e) => e.kind)).toEqual(["directory", "share", "identity"]);
  });
});

describe("RecentlyViewed", () => {
  it("server-renders the heading and description only: no entries and no empty state", () => {
    seed();
    const html = renderToString(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(html).toContain("Recently viewed");
    expect(html).toContain("Not sent to ADG.");
    expect(html).not.toContain("Jane Smith");
    expect(html).not.toContain("Nothing yet");
    expect(html).not.toContain("aria-busy");
  });

  it("lists entries newest first, each with its kind and a link to the canonical route", () => {
    seed();
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(rows()).toEqual(["Directory 2026", "Share Finance", "Server FS01", "Identity Jane Smith"]);
    expect(screen.getByRole("link", { name: "Jane Smith" })).toHaveAttribute(
      "href",
      "/identities/principal/S-1-5-21-1-2-3-1104",
    );
    expect(screen.getByRole("link", { name: "2026" })).toHaveAttribute(
      "href",
      "/resources/directory?key=%5C%5Cfs01%5Cfinance%5Cq%5C2026",
    );
    expect(screen.getByRole("region", { name: "Recently viewed" })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows the empty state when nothing has been opened", () => {
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(screen.getByText(/^Nothing yet\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("deletes another account's list before rendering any of it", () => {
    seed(ADA);
    render(<RecentlyViewed owner={BOB} capabilities={BOTH} />);

    expect(screen.queryByText("Jane Smith")).toBeNull();
    expect(screen.getByText(/^Nothing yet\./)).toBeInTheDocument();
    expect(sessionStorage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });

  it("deletes entries the account can no longer open and says so without naming them", () => {
    seed();
    render(<RecentlyViewed owner={ADA} capabilities={["identities:read"]} />);

    expect(rows()).toEqual(["Identity Jane Smith"]);
    expect(screen.getByText("Some items were removed because this account can no longer open them.")).toBeInTheDocument();
    expect(readRecent(sessionStorage, ADA).map((e) => e.kind)).toEqual(["identity"]);
  });

  it("drops entries older than eight hours before display", () => {
    recordRecent(sessionStorage, ADA, { kind: "server", key: "old", label: "Old" }, Date.now() - RECENT_MAX_AGE_MS - 1);
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(screen.queryByText("Old")).toBeNull();
    expect(sessionStorage.getItem(RECENT_STORAGE_KEY)).not.toContain("Old");
  });

  it("Clear empties the list and storage at once, keeps focus in the section, and sends nothing", () => {
    seed();
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    const clear = screen.getByRole("button", { name: "Clear" });
    clear.focus();
    fireEvent.click(clear);

    expect(screen.queryByRole("list", { name: "Recently viewed items" })).toBeNull();
    expect(screen.getByText(/^Nothing yet\./)).toBeInTheDocument();
    expect(sessionStorage.getItem(RECENT_STORAGE_KEY)).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Recently viewed" }));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("explains blocked storage instead of throwing", () => {
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(screen.getByText(/isn't available in this browser because it blocks session storage/)).toBeInTheDocument();
  });

  it("has no nested interactive elements and no per-item remove", () => {
    seed();
    render(<RecentlyViewed owner={ADA} capabilities={BOTH} />);

    expect(screen.getAllByRole("button")).toHaveLength(1);
    for (const link of screen.getAllByRole("link")) {
      expect(link.querySelector("a, button")).toBeNull();
    }
  });
});

describe("SignOutButton", () => {
  it("clears the list before the logout request starts", async () => {
    seed();
    let storedAtRequest: string | null | undefined;
    fetchSpy.mockImplementation(() => {
      storedAtRequest = sessionStorage.getItem(RECENT_STORAGE_KEY);
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    render(<SignOutButton />);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign out" })));

    expect(fetchSpy).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
    expect(storedAtRequest).toBeNull();
  });

  it("leaves no list behind when the logout request hangs", async () => {
    seed();
    fetchSpy.mockImplementation(() => new Promise<Response>(() => {}));
    render(<SignOutButton />);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign out" })));

    expect(screen.getByRole("button", { name: "Signing out…" })).toBeDisabled();
    expect(sessionStorage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });
});
