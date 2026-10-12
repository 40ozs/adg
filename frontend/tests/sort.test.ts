import { describe, expect, it } from "vitest";

import { nextDirection, sortRows, type SortValue } from "@/lib/sort";

const by = (values: SortValue[], type: "text" | "number" | "date", direction: "ascending" | "descending") =>
  sortRows(values, (value) => value, type, direction);

describe("sorting a page of rows", () => {
  it("orders numbers as numbers, not as strings", () => {
    expect(by([10, 9, 100, 1], "number", "ascending")).toEqual([1, 9, 10, 100]);
    expect(by(["10", "9", "1,000"], "number", "descending")).toEqual(["1,000", "10", "9"]);
  });

  it("orders text case-insensitively", () => {
    expect(by(["beta", "Alpha", "alpha2", "Gamma"], "text", "ascending")).toEqual([
      "Alpha",
      "alpha2",
      "beta",
      "Gamma",
    ]);
  });

  it("orders dates chronologically whatever their offset", () => {
    expect(
      by(["2026-09-12T10:00:00+02:00", "2026-09-12T09:00:00Z", "2026-01-01T00:00:00Z"], "date", "ascending"),
    ).toEqual(["2026-01-01T00:00:00Z", "2026-09-12T10:00:00+02:00", "2026-09-12T09:00:00Z"]);
  });

  it("puts blanks last in both directions", () => {
    expect(by([null, 2, "", 1, undefined, "—"], "number", "ascending").slice(0, 2)).toEqual([1, 2]);
    expect(by([null, 2, 1], "number", "descending")).toEqual([2, 1, null]);
  });

  it("orders booleans deterministically, false first", () => {
    expect(by([true, null, false, true], "text", "ascending")).toEqual([false, true, true, null]);
  });

  it("keeps the server's order for ties, and never mutates the input", () => {
    const rows = [
      { id: "a", n: 1 },
      { id: "b", n: 0 },
      { id: "c", n: 1 },
    ];
    expect(sortRows(rows, (row) => row.n, "number", "descending").map((row) => row.id)).toEqual(["a", "c", "b"]);
    expect(rows.map((row) => row.id)).toEqual(["a", "b", "c"]);
  });

  it("orders SIDs lexically", () => {
    expect(by(["S-1-5-21-1-2-3-503", "S-1-5-21-1-2-3-1104"], "text", "ascending")).toEqual([
      "S-1-5-21-1-2-3-1104",
      "S-1-5-21-1-2-3-503",
    ]);
  });

  it("ascends first and reverses on the second activation", () => {
    expect(nextDirection(null, 2)).toBe("ascending");
    expect(nextDirection({ column: 2, direction: "ascending" }, 2)).toBe("descending");
    expect(nextDirection({ column: 2, direction: "descending" }, 2)).toBe("ascending");
    expect(nextDirection({ column: 1, direction: "ascending" }, 2)).toBe("ascending");
  });
});
