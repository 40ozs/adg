/**
 * Document titles (ADG-UX-TITLES-03).
 *
 * Every page's title, resolved the way Next resolves it: the page's own title, else the
 * nearest layout's. Each title is a whole string; a `title.template` would not reach the root
 * page or a layout's own children. The table below is the contract, and a new page fails here
 * until somebody decides what its tab says.
 */

import type { Metadata } from "next";
import { describe, expect, it } from "vitest";

import { NAV_ITEMS, documentTitle, sectionTitle } from "@/lib/nav";

type RouteModule = { metadata?: Metadata; generateMetadata?: unknown };

const modules = import.meta.glob<RouteModule>("../app/**/{layout,page,not-found}.tsx", { eager: true });

const EXPECTED: Record<string, string> = {
  "/": "Overview — ADG",
  "/risks": "Findings — ADG",
  "/risks/alerts": "Findings — ADG",
  "/access": "Access — ADG",
  "/access/explain": "Access — ADG",
  "/access/investigate": "Access — ADG",
  "/changes": "Changes — ADG",
  "/changes/compare": "Changes — ADG",
  "/changes/impact": "Changes — ADG",
  "/identities": "Identities — ADG",
  "/identities/users": "Identities — ADG",
  "/identities/groups": "Identities — ADG",
  "/identities/principal": "Identity — ADG",
  "/identities/principal/[key]/[[...view]]": "Identity — ADG",
  "/resources": "Resources — ADG",
  "/resources/server": "Server — ADG",
  "/resources/share": "Share — ADG",
  "/resources/directory": "Directory — ADG",
  "/governance": "Reviews — ADG",
  "/governance/campaigns/[campaignId]": "Reviews — ADG",
  "/governance/items/[itemId]": "Reviews — ADG",
  "/simulations": "What-if — ADG",
  "/simulations/new": "What-if — ADG",
  "/simulations/[simulationId]": "What-if — ADG",
  "/collectors": "Collectors — ADG",
  "/settings": "Settings — ADG",
  "/login": "Sign in — ADG",
  "/status": "System status — ADG",
  "/search": "Search — ADG",
  "/demo": "Demo guide — ADG",
};

/** `../app/risks/alerts/page.tsx` → `/risks/alerts`. */
function routeOf(file: string): string {
  return `/${file.replace(/^\.\.\/app\/?/, "").replace(/\/?(layout|page)\.tsx$/, "")}`.replace(/\/$/, "") || "/";
}

function resolvedTitle(route: string): string {
  const segments = route === "/" ? [] : route.slice(1).split("/");
  let title = modules["../app/layout.tsx"].metadata?.title;
  for (let depth = 1; depth <= segments.length; depth++) {
    title = modules[`../app/${segments.slice(0, depth).join("/")}/layout.tsx`]?.metadata?.title ?? title;
  }
  title = modules[route === "/" ? "../app/page.tsx" : `../app${route}/page.tsx`]?.metadata?.title ?? title;
  expect(typeof title, route).toBe("string");
  return title as string;
}

const pages = Object.keys(modules)
  .filter((file) => file.endsWith("/page.tsx"))
  .map(routeOf)
  .sort();

describe("document titles", () => {
  it("covers every page, and no page is left as plain ADG", () => {
    expect(pages).toEqual(Object.keys(EXPECTED).sort());
    for (const route of pages) {
      expect(resolvedTitle(route), route).not.toBe("ADG");
    }
  });

  it.each(Object.entries(EXPECTED))("%s → %s", (route, title) => {
    expect(resolvedTitle(route)).toBe(title);
  });

  it("titles each primary section with its nav label", () => {
    for (const item of NAV_ITEMS) {
      expect(resolvedTitle(item.href)).toBe(`${item.label} — ADG`);
    }
  });

  it("keeps the /risks route but titles it Findings", () => {
    expect(resolvedTitle("/risks")).toBe("Findings — ADG");
    expect(Object.values(EXPECTED).join()).not.toMatch(/Risks/);
  });

  it("never reads data for a title: every title is static", () => {
    for (const [file, module] of Object.entries(modules)) {
      expect(module.generateMetadata, file).toBeUndefined();
    }
  });

  it("titles the not-found page, which otherwise reads plain ADG", () => {
    expect(modules["../app/not-found.tsx"].metadata?.title).toBe("Page not found — ADG");
  });

  it("builds a title from a name, and refuses a section the nav does not have", () => {
    expect(() => sectionTitle("findings")).toThrow(/No navigation item/);
    expect(documentTitle("Identity")).toBe("Identity — ADG");
  });
});
