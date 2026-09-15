/**
 * The navigation model.
 *
 * Filtering here is a courtesy, not a control — the backend refuses the data either way —
 * so the tests that matter are the ones proving the frontend never *invents* authorization:
 * it takes the capability list it was given and does set membership, nothing more.
 */

import { describe, expect, it } from "vitest";

import { NAV_ITEMS, activeNavItem, visibleNavItems } from "@/lib/nav";

const VIEWER = [
  "resources:read",
  "identities:read",
  "access:read",
  "risks:read",
  "changes:read",
  "collectors:read",
  "search",
];
const AUDITOR = [...VIEWER, "settings:read"];

describe("the navigation", () => {
  it("offers every section named by the phase", () => {
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Overview",
      "Resources",
      "Identities",
      "Access",
      "Risks",
      "Changes",
      "Collectors",
      "Settings",
    ]);
  });

  it("marks the sections that have no data behind them yet", () => {
    const placeholders = NAV_ITEMS.filter((item) => item.placeholder).map((item) => item.id);

    expect(placeholders).toEqual(["risks"]);
  });

  it("shows a viewer everything except settings", () => {
    expect(visibleNavItems(VIEWER).map((item) => item.id)).toEqual([
      "overview",
      "resources",
      "identities",
      "access",
      "risks",
      "changes",
      "collectors",
    ]);
  });

  it("shows an auditor settings as well", () => {
    expect(visibleNavItems(AUDITOR).map((item) => item.id)).toContain("settings");
  });

  it("shows an account with no capabilities only the overview", () => {
    // Which is where the banner explaining that no role is assigned lives.
    expect(visibleNavItems([]).map((item) => item.id)).toEqual(["overview"]);
  });

  it("never grants a section from a capability it does not name", () => {
    // The frontend must not infer "an admin can do everything". It reads the list it was
    // given, and the list comes from the backend.
    expect(visibleNavItems(["settings:write"]).map((item) => item.id)).toEqual(["overview"]);
  });
});

describe("the current section", () => {
  it("marks the overview only on the root path", () => {
    expect(activeNavItem("/")?.id).toBe("overview");
  });

  it("does not mark the overview on every other path", () => {
    // A naive startsWith("/") would mark every page as Overview.
    expect(activeNavItem("/resources")?.id).toBe("resources");
  });

  it("marks the section a nested path belongs to", () => {
    expect(activeNavItem("/resources/servers/fs01")?.id).toBe("resources");
  });

  it("does not match a path that merely shares a prefix", () => {
    expect(activeNavItem("/resources-archive")).toBeNull();
  });

  it("returns nothing for a path outside the navigation", () => {
    expect(activeNavItem("/search")).toBeNull();
  });
});
