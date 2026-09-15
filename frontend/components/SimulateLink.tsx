import type { JSX } from "react";
import Link from "next/link";

import type { SimulationSeed } from "@/lib/simulation";
import { simulateHref } from "@/lib/simulation";

/**
 * The `Simulate` action, offered from wherever somebody is already looking at the thing.
 *
 * A plain link, not a button that posts. Clicking it opens the proposal editor with one
 * change already filled in, and the URL says which — so it can be pasted into a ticket, it
 * survives a reload, and nothing has happened to the estate by clicking it. That last point
 * is the reason it is a link: an action that *looks* like it might do something is exactly
 * what this phase's first acceptance criterion is about.
 *
 * The seed is a starting point and not a commitment. The editor shows the change, lets it be
 * edited, and evaluates nothing until somebody asks.
 */
export function SimulateLink({
  seed,
  label,
  title,
}: {
  seed: SimulationSeed;
  label?: string;
  /** The `title` attribute, when the label alone does not say what would be proposed. */
  title?: string;
}): JSX.Element {
  return (
    <Link className="button" href={simulateHref(seed)} title={title} prefetch={false}>
      {label ?? "Simulate"}
    </Link>
  );
}

/** The same action rendered inline in a dense table, where a button would crowd the row. */
export function SimulateAction({
  seed,
  label,
  title,
}: {
  seed: SimulationSeed;
  label?: string;
  title?: string;
}): JSX.Element {
  return (
    <Link href={simulateHref(seed)} title={title} prefetch={false}>
      {label ?? "Simulate removal"}
    </Link>
  );
}
