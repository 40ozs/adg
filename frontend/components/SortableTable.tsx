"use client";

import type { JSX, ReactNode } from "react";
import { useMemo, useState } from "react";

import { nextDirection, sortRows, type SortDirection, type SortType, type SortValue } from "@/lib/sort";

export interface SortableColumn {
  label: ReactNode;
  /** How this column's sort values compare. Text unless said otherwise. */
  type?: SortType;
  /** False for a column of actions, where an order means nothing. */
  sortable?: boolean;
  /** Applied to every body cell of the column (`muted`, for instance). */
  className?: string;
}

export interface SortableRow {
  key: string;
  /** One rendered cell per column. */
  cells: ReactNode[];
  /** One sort value per column, in the column's own terms; null is blank. */
  sort: SortValue[];
  className?: string;
}

/**
 * The application's one table, ordered by any column the reader picks.
 *
 * Cells arrive rendered, from server components, together with the value each sorts by:
 * a cell's text is a poor key (a principal's cell carries its name and its notes),
 * and a page that sorted by it would put "10 hops" before "9 hops".
 *
 * Each sortable header is a real button, so it is reachable by Tab and works on Enter and
 * Space; the header carries `aria-sort`, which screen readers announce with the column. The
 * first activation ascends and the next descends. Sorting is state in this component, so it
 * never reloads or refetches anything, and it reorders the page in hand only — the caption
 * a caller passes is where it says so.
 */
export function SortableTable({
  columns,
  rows,
  caption,
  captionClassName,
  className,
  rowHeader = 0,
}: {
  columns: readonly SortableColumn[];
  rows: readonly SortableRow[];
  caption?: ReactNode;
  /** `visually-hidden` for a caption only a screen reader needs, for instance. */
  captionClassName?: string;
  className?: string;
  /** The column whose cell names the row (`<th scope="row">`), or null for none. */
  rowHeader?: number | null;
}): JSX.Element {
  const [sort, setSort] = useState<{ column: number; direction: SortDirection } | null>(null);
  const ordered = useMemo(
    () =>
      sort === null
        ? rows
        : sortRows(rows, (row) => row.sort[sort.column], columns[sort.column]?.type ?? "text", sort.direction),
    [rows, columns, sort],
  );

  return (
    <div className="table-scroll">
      <table className={className}>
        {caption !== undefined && <caption className={captionClassName}>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column, index) => {
              const direction = sort?.column === index ? sort.direction : undefined;
              return (
                <th key={index} scope="col" aria-sort={direction}>
                  {column.sortable === false ? (
                    column.label
                  ) : (
                    <button
                      type="button"
                      className="sort-button"
                      onClick={() => setSort({ column: index, direction: nextDirection(sort, index) })}
                    >
                      {column.label}
                      <span className="sort-indicator" aria-hidden="true">
                        {direction === "ascending" ? "▲" : direction === "descending" ? "▼" : "↕"}
                      </span>
                    </button>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => (
            <tr key={row.key} className={row.className}>
              {row.cells.map((cell, index) =>
                index === rowHeader ? (
                  <th key={index} scope="row" className={columns[index]?.className}>
                    {cell}
                  </th>
                ) : (
                  <td key={index} className={columns[index]?.className}>
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
