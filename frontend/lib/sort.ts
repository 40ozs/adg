/**
 * Ordering the rows a table already holds.
 *
 * Every table in this application shows one page of a server-paged answer, so a sort here
 * orders that page and never claims to order the estate. The server's order is the initial
 * order, and ties keep it: `Array.prototype.sort` is stable, and the original index breaks
 * any tie the comparison leaves.
 *
 * A column says what its values are, because the same string means different things:
 * "10" sorts after "9" as a number and before it as text. Blank sorts last whichever way the
 * column is ordered: an unreported value is not the smallest value, and putting it first in
 * one direction would bury the rows that do carry an answer.
 */

export type SortValue = string | number | boolean | null | undefined;
export type SortType = "text" | "number" | "date";
export type SortDirection = "ascending" | "descending";

const collator = new Intl.Collator(undefined, { sensitivity: "base" });

/** The comparable form of one value, or null for blank. */
function comparable(value: SortValue, type: SortType): string | number | null {
  if (value === null || value === undefined) {
    return null;
  }
  // false before true, and both before blank: deterministic whatever the column type.
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  if (typeof value === "number") {
    return Number.isNaN(value) ? null : value;
  }
  const text = value.trim();
  if (text === "" || text === "—") {
    return null;
  }
  if (type === "number") {
    const number = Number(text.replace(/,/g, ""));
    return Number.isNaN(number) ? null : number;
  }
  if (type === "date") {
    const time = Date.parse(text);
    return Number.isNaN(time) ? null : time;
  }
  return text;
}

/** Ascending comparison of two values of one column, blanks excluded by the caller. */
function compare(left: string | number, right: string | number): number {
  if (typeof left === "number" && typeof right === "number") {
    return left - right;
  }
  return collator.compare(String(left), String(right));
}

/** The rows in the asked-for order, without touching the array given. */
export function sortRows<T>(
  rows: readonly T[],
  valueOf: (row: T) => SortValue,
  type: SortType,
  direction: SortDirection,
): T[] {
  const sign = direction === "ascending" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index, value: comparable(valueOf(row), type) }))
    .sort((a, b) => {
      if (a.value === null || b.value === null) {
        return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1;
      }
      return sign * compare(a.value, b.value) || a.index - b.index;
    })
    .map((entry) => entry.row);
}

/** First activation ascends; activating the sorted column again reverses it. */
export function nextDirection(
  current: { column: number; direction: SortDirection } | null,
  column: number,
): SortDirection {
  return current?.column === column && current.direction === "ascending"
    ? "descending"
    : "ascending";
}
