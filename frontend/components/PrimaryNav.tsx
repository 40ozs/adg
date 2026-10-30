"use client";

import type { JSX } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS, activeNavItem, groupNavItems, visibleNavItems, type NavItem } from "@/lib/nav";

/**
 * The primary navigation.
 *
 * An ordinary list of links, deliberately. A custom widget here would mean reimplementing
 * arrow-key behavior, focus management, and `aria-current` that browsers already provide
 * for free — and getting one of them subtly wrong for the keyboard users this product is
 * for. Tab moves through the links; the current one is marked with `aria-current="page"`,
 * which a screen reader announces and CSS styles.
 *
 * The links sit in labelled groups (Investigate, Directory, Act, System). Each group is a
 * nested list named by `aria-labelledby`, so a screen reader announces "Investigate, list,
 * 4 items" on the way in. The group names are plain text, not headings and not buttons:
 * they never take focus, and the page's own `<h1>` stays the first heading after the nav.
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
        {groupNavItems(items).map((group) => (
          <li key={group.id} className="nav-group">
            <span className="nav-group-label" id={`nav-group-${group.id}`}>
              {group.label}
            </span>
            <ul aria-labelledby={`nav-group-${group.id}`}>
              {group.items.map((item: NavItem) => (
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
          </li>
        ))}
      </ul>
    </nav>
  );
}
