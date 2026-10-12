// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SortableTable } from "@/components/SortableTable";

const ROWS = [
  { key: "a", name: "beta", hops: 10, seen: "2026-09-12T10:00:00Z" },
  { key: "b", name: "Alpha", hops: 9, seen: null },
  { key: "c", name: "gamma", hops: 100, seen: "2026-01-01T00:00:00Z" },
];

function table() {
  render(
    <SortableTable
      caption="Sorting orders the rows on this page."
      columns={[
        { label: "Name" },
        { label: "Hops", type: "number" },
        { label: "Seen", type: "date" },
        { label: "Action", sortable: false },
      ]}
      rows={ROWS.map((row) => ({
        key: row.key,
        sort: [row.name, row.hops, row.seen],
        cells: [row.name, row.hops, row.seen ?? "—", <button key="go">Open {row.name}</button>],
      }))}
    />,
  );
}

/** Each body row as [row header, hops], read off the DOM. */
const order = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => [
      within(row).getByRole("rowheader").textContent,
      row.querySelectorAll("td")[0]?.textContent,
    ]);

const header = (name: string) => screen.getByRole("columnheader", { name: new RegExp(`^${name}`) });

describe("a sortable table", () => {
  it("starts in the server's order with no column marked sorted", () => {
    table();
    expect(order().map(([name]) => name)).toEqual(["beta", "Alpha", "gamma"]);
    expect(document.querySelectorAll("th[aria-sort]")).toHaveLength(0);
  });

  it("ascends on the first activation and descends on the second", async () => {
    table();
    const user = userEvent.setup();

    await user.click(within(header("Name")).getByRole("button"));
    expect(order().map(([name]) => name)).toEqual(["Alpha", "beta", "gamma"]);
    expect(header("Name")).toHaveAttribute("aria-sort", "ascending");

    await user.click(within(header("Name")).getByRole("button"));
    expect(order().map(([name]) => name)).toEqual(["gamma", "beta", "Alpha"]);
    expect(header("Name")).toHaveAttribute("aria-sort", "descending");
  });

  it("sorts a number column numerically and keeps each row's cells together", async () => {
    table();
    await userEvent.setup().click(within(header("Hops")).getByRole("button"));

    expect(order()).toEqual([
      ["Alpha", "9"],
      ["beta", "10"],
      ["gamma", "100"],
    ]);
    // Only the sorted column carries the state.
    expect(header("Name")).not.toHaveAttribute("aria-sort");
  });

  it("puts a blank date last", async () => {
    table();
    await userEvent.setup().click(within(header("Seen")).getByRole("button"));
    expect(order().map(([name]) => name)).toEqual(["gamma", "beta", "Alpha"]);
  });

  it("works from the keyboard", async () => {
    table();
    const user = userEvent.setup();

    await user.tab();
    expect(within(header("Name")).getByRole("button")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(header("Name")).toHaveAttribute("aria-sort", "ascending");
    await user.keyboard(" ");
    expect(header("Name")).toHaveAttribute("aria-sort", "descending");
  });

  it("offers no sort on a column of actions", () => {
    table();
    expect(within(header("Action")).queryByRole("button")).not.toBeInTheDocument();
  });
});
