"use client";

import type { JSX, ReactNode } from "react";
import Link, { useLinkStatus } from "next/link";

/**
 * A link that starts a comparison, and says so while it runs (ADG-DEMO-03).
 *
 * The comparison is computed while the next page renders on the server, so its `running` state
 * lives on the link that started it: `useLinkStatus` reports the navigation as pending, and the
 * link's own live region says "comparing…" until the outcome replaces the page. A route-level
 * `loading.tsx` would be the obvious place for it, and was tried: under Next 15.5 it aborted the
 * search-parameter-only navigations these links make, so the outcome never arrived.
 *
 * Never prefetched. A recovery action is a real comparison, and prefetching the half-dozen a
 * stopped comparison offers would run them for nobody.
 */
export function ComparisonLink({ href, children }: { href: string; children: ReactNode }): JSX.Element {
  return (
    <Link href={href} prefetch={false}>
      {children}
      <Running />
    </Link>
  );
}

function Running(): JSX.Element {
  const { pending } = useLinkStatus();
  return (
    <span role="status" aria-live="polite" className="muted">
      {pending ? " — comparing…" : ""}
    </span>
  );
}
