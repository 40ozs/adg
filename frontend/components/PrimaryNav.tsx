"use client";

import type { JSX } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS, activeNavItem, visibleNavItems, type NavItem } from "@/lib/nav";

/**
 * The primary navigation.
 *
 * An ordinary list of links, deliberately. A custom widget here would mean reimplementing
 * arrow-key behavior, focus management, and `aria-current` that browsers already provide
 * for free — and getting one of them subtly wrong for the keyboard users this product is
 * for. Tab moves through the links; the current one is marked with `aria-current="page"`,
 * which a screen reader announces and CSS styles.
 *
 * `capabilities` comes from `/auth/me`. Hiding a section the account cannot use is a
 * courtesy; the backend refuses the data either way.
 */
export function PrimaryNav({
  capabilities,
  // Injectable so the "soon" badge stays testable when nothing in the real navigation wears
  // it. The badge is the difference between "this page shows nothing because nothing is
  // computed" and "this page shows nothing because nothing is there", which is the
  // distinction the whole product turns on -- so it must keep working for the next section
  // that lands ahead of its data, not only for whichever one happens to be unfinished today.
  items: source = NAV_ITEMS,
}: {
  capabilities: readonly string[];
  items?: readonly NavItem[];
}): JSX.Element {
  const pathname = usePathname() ?? "/";
  const items = visibleNavItems(capabilities, source);
  const current = activeNavItem(pathname, items);

  return (
    <nav className="shell-nav" aria-label="Primary">
      <ul>
        {items.map((item: NavItem) => (
          <li key={item.id}>
            <Link
              href={item.href}
              aria-current={current?.id === item.id ? "page" : undefined}
              title={item.description}
            >
              {item.label}
              {item.placeholder && (
                <span className="nav-badge" aria-label="not yet available">
                  soon
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
