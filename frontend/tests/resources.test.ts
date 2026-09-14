/**
 * UNC paths, and the trail from a server down to a directory.
 *
 * The breadcrumb is the only place in this application that derives structure from a path
 * instead of reading it from the API, so the rules it must not break are pinned here: the
 * share and the directory it publishes stay two separate destinations, a share root has no
 * parent inside the share, and nothing is silently repaired.
 */

import { describe, expect, it } from "vitest";

import {
  directoryHref,
  parentPath,
  parseUncPath,
  resourceBreadcrumb,
  serverHref,
  shareHref,
  shareKeyParts,
  shareRootPath,
} from "@/lib/resources";

describe("parsing a UNC path", () => {
  it("splits server, share, and the path beneath", () => {
    expect(parseUncPath("\\\\FS01\\Finance\\Reports\\2026")).toEqual({
      server: "FS01",
      share: "Finance",
      segments: ["Reports", "2026"],
    });
  });

  it("preserves case, because display is case-preserving", () => {
    expect(parseUncPath("\\\\FS01\\Finance")?.server).toBe("FS01");
  });

  it("accepts forward slashes, which both collectors and people produce", () => {
    expect(parseUncPath("//fs01/finance/reports")?.segments).toEqual(["reports"]);
  });

  it("ignores a trailing separator", () => {
    expect(parseUncPath("\\\\FS01\\Finance\\")?.segments).toEqual([]);
  });

  it("refuses anything that is not a UNC path", () => {
    for (const raw of ["D:\\Shares\\Finance", "\\\\FS01", "finance", ""]) {
      expect(parseUncPath(raw), raw).toBeNull();
    }
  });

  it("does not resolve a '..', which would name a different directory", () => {
    expect(parseUncPath("\\\\FS01\\Finance\\..\\HR")?.segments).toEqual(["..", "HR"]);
  });
});

describe("walking up", () => {
  it("returns the containing directory", () => {
    expect(parentPath("\\\\FS01\\Finance\\Reports\\2026")).toBe("\\\\FS01\\Finance\\Reports");
  });

  it("stops at the share root", () => {
    // Not "unknown": a share root's parent is outside the share and usually outside
    // anything a collector was pointed at.
    expect(parentPath("\\\\FS01\\Finance")).toBeNull();
  });

  it("finds the share root of any path under it", () => {
    expect(shareRootPath("\\\\FS01\\Finance\\Reports\\2026")).toBe("\\\\FS01\\Finance");
  });
});

describe("the breadcrumb", () => {
  it("keeps the share and the directory it publishes apart", () => {
    const crumbs = resourceBreadcrumb("\\\\FS01\\Finance\\Reports");

    expect(crumbs.map((crumb) => crumb.kind)).toEqual([
      "server",
      "share",
      "directory",
      "directory",
    ]);
    // The two carry different ACLs and therefore lead to different pages.
    expect(crumbs[1].href).toBe(shareHref("\\\\FS01\\Finance"));
    expect(crumbs[2].href).toBe(directoryHref("\\\\FS01\\Finance"));
  });

  it("ends at the current location, which is not a link to itself", () => {
    const crumbs = resourceBreadcrumb("\\\\FS01\\Finance\\Reports");

    expect(crumbs[crumbs.length - 1]).toEqual({
      label: "Reports",
      href: null,
      kind: "directory",
    });
  });

  it("terminates on the root directory when the path is the share root", () => {
    const crumbs = resourceBreadcrumb("\\\\FS01\\Finance");

    expect(crumbs).toHaveLength(3);
    expect(crumbs[0].href).toBe(serverHref("FS01"));
    expect(crumbs[1].href).toBe(shareHref("\\\\FS01\\Finance"));
    expect(crumbs[2].href).toBeNull();
  });

  it("links every ancestor on the way down", () => {
    const crumbs = resourceBreadcrumb("\\\\FS01\\Finance\\Reports\\2026\\Q1");

    expect(crumbs.map((crumb) => crumb.label)).toEqual([
      "FS01",
      "Finance",
      "Finance (root directory)",
      "Reports",
      "2026",
      "Q1",
    ]);
    expect(crumbs[3].href).toBe(directoryHref("\\\\FS01\\Finance\\Reports"));
    expect(crumbs[4].href).toBe(directoryHref("\\\\FS01\\Finance\\Reports\\2026"));
  });

  it("is empty rather than wrong for a path it cannot read", () => {
    expect(resourceBreadcrumb("D:\\Shares\\Finance")).toEqual([]);
  });
});

describe("identifiers in links", () => {
  it("carries a backslashed path as one query value, not as structure", () => {
    expect(directoryHref("\\\\FS01\\Finance\\Reports")).toBe(
      "/resources/directory?key=%5C%5CFS01%5CFinance%5CReports",
    );
  });

  it("splits a share key back into its halves", () => {
    expect(shareKeyParts("fs01|finance")).toEqual({ serverKey: "fs01", shareName: "finance" });
  });

  it("refuses a key that is not exactly server|share", () => {
    for (const raw of ["fs01", "fs01|", "|finance", "fs01|finance|extra"]) {
      expect(shareKeyParts(raw), raw).toBeNull();
    }
  });
});
