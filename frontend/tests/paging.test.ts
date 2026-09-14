/**
 * Paging with the position in the URL.
 *
 * The failures worth pinning here are all failures of honesty rather than of arithmetic: a
 * trail that silently resets, a "next" link on the last page, or a page number that claims
 * a position the request did not actually reach.
 */

import { describe, expect, it } from "vitest";

import {
  cursorFor,
  decodeTrail,
  encodeTrail,
  hrefWith,
  pageNavigation,
  TRAIL_SEPARATOR,
} from "@/lib/paging";

// Base64url with no padding, which is exactly what `app/api/pagination.py` issues:
// these two decode to {"o":100,"t":"o"} and {"o":200,"t":"o"}.
const C1 = "eyJvIjoxMDAsInQiOiJvIn0";
const C2 = "eyJvIjoyMDAsInQiOiJvIn0";

describe("decoding a trail", () => {
  it("reads page one from an absent parameter", () => {
    expect(decodeTrail(undefined)).toEqual({ cursors: [], rejected: false });
    expect(decodeTrail("")).toEqual({ cursors: [], rejected: false });
  });

  it("reads the cursors walked through, in order", () => {
    expect(decodeTrail(`${C1}${TRAIL_SEPARATOR}${C2}`)).toEqual({
      cursors: [C1, C2],
      rejected: false,
    });
  });

  it("rejects anything this application did not issue rather than paging on", () => {
    // A silent reset is the dangerous failure: page one rendered while the pager claims
    // page four, and a short page then reads as the end of the list.
    for (const hostile of ["not a cursor", `${C1}${TRAIL_SEPARATOR}`, "a/b", "<script>"]) {
      expect(decodeTrail(hostile), hostile).toEqual({ cursors: [], rejected: true });
    }
  });

  it("rejects a repeated query parameter", () => {
    expect(decodeTrail([C1, C2])).toEqual({ cursors: [], rejected: true });
  });

  it("round-trips through encodeTrail", () => {
    expect(decodeTrail(encodeTrail([C1, C2]) ?? undefined).cursors).toEqual([C1, C2]);
  });

  it("writes no parameter at all for page one", () => {
    expect(encodeTrail([])).toBeNull();
  });
});

describe("the cursor sent with a request", () => {
  it("is the last one walked through", () => {
    expect(cursorFor({ cursors: [C1, C2], rejected: false })).toBe(C2);
  });

  it("is absent on page one", () => {
    expect(cursorFor({ cursors: [], rejected: false })).toBeUndefined();
  });
});

describe("building an href", () => {
  it("drops empty, null and undefined values", () => {
    expect(hrefWith("/x", { a: "1", b: "", c: null, d: undefined })).toBe("/x?a=1");
  });

  it("is the bare path when nothing survives", () => {
    expect(hrefWith("/x", { a: null })).toBe("/x");
  });

  it("encodes a value that would otherwise be structure", () => {
    const href = hrefWith("/resources/directory", { key: "\\\\FS01\\Finance" });

    expect(href).toBe("/resources/directory?key=%5C%5CFS01%5CFinance");
  });
});

describe("page navigation", () => {
  const params = { key: "fs01|finance", tab: "acl" };

  it("offers no previous link on page one", () => {
    const navigation = pageNavigation({
      basePath: "/p",
      params,
      trailParam: "p",
      trail: { cursors: [], rejected: false },
      nextCursor: C1,
    });

    expect(navigation.pageNumber).toBe(1);
    expect(navigation.previousHref).toBeNull();
    expect(navigation.firstHref).toBeNull();
    expect(navigation.nextHref).toContain(`p=${C1}`);
  });

  it("offers no next link when the API sent no cursor", () => {
    const navigation = pageNavigation({
      basePath: "/p",
      params,
      trailParam: "p",
      trail: { cursors: [C1], rejected: false },
      nextCursor: null,
    });

    expect(navigation.nextHref).toBeNull();
  });

  it("walks back by dropping the last cursor, not by inventing a backwards one", () => {
    const navigation = pageNavigation({
      basePath: "/p",
      params,
      trailParam: "p",
      trail: { cursors: [C1, C2], rejected: false },
      nextCursor: null,
    });

    expect(navigation.pageNumber).toBe(3);
    expect(navigation.previousHref).toContain(`p=${C1}`);
    expect(navigation.previousHref).not.toContain(C2);
  });

  it("keeps the rest of the page's parameters on every link", () => {
    const navigation = pageNavigation({
      basePath: "/p",
      params,
      trailParam: "p",
      trail: { cursors: [C1], rejected: false },
      nextCursor: C2,
    });

    for (const href of [navigation.previousHref, navigation.nextHref, navigation.firstHref]) {
      expect(href).toContain("key=fs01%7Cfinance");
      expect(href).toContain("tab=acl");
    }
  });

  it("returns to page one with no trail parameter at all", () => {
    const navigation = pageNavigation({
      basePath: "/p",
      params,
      trailParam: "p",
      trail: { cursors: [C1, C2], rejected: false },
      nextCursor: null,
    });

    expect(navigation.firstHref).not.toContain("p=");
  });
});
