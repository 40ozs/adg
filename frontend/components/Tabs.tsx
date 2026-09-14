import type { JSX } from "react";
import Link from "next/link";

import type { Crumb } from "@/lib/resources";

export interface TabDefinition {
  id: string;
  label: string;
  href: string;
  /** One line under the strip explaining what the selected panel is. */
  hint?: string;
}

/**
 * The sections of a detail page, as links rather than as client-side state.
 *
 * Each panel is one server-side query, and only the selected one runs. A principal page
 * that eagerly loaded direct groups, effective groups, accessible shares and accessible
 * directories would issue four bounded graph traversals to answer a question the reader
 * asked once — and the expensive ones are exactly the ones nobody opened.
 *
 * Links and not buttons: the selected panel and its page position are then in the URL,
 * which makes a finding something an auditor can paste into a ticket.
 */
export function Tabs({
  label,
  tabs,
  current,
}: {
  label: string;
  tabs: readonly TabDefinition[];
  current: string;
}): JSX.Element {
  const selected = tabs.find((tab) => tab.id === current) ?? tabs[0];
  return (
    <div className="tabs">
      <nav aria-label={label}>
        <ul>
          {tabs.map((tab) => (
            <li key={tab.id}>
              <Link href={tab.href} aria-current={tab.id === selected.id ? "page" : undefined}>
                {tab.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {selected.hint && <p className="muted tabs-hint">{selected.hint}</p>}
    </div>
  );
}

/**
 * Where a directory sits, derived from its own path.
 *
 * The share and the directory it publishes are separate crumbs going to separate pages,
 * because they carry separate ACLs. An ancestor crumb may be a directory no run has read;
 * it is still shown, and the page it leads to says so rather than this one hiding the
 * path.
 */
export function Breadcrumbs({ crumbs }: { crumbs: readonly Crumb[] }): JSX.Element | null {
  if (crumbs.length === 0) {
    return null;
  }
  return (
    <nav className="breadcrumb" aria-label="Location">
      <ol>
        {crumbs.map((crumb, index) => (
          <li key={`${crumb.kind}-${index}-${crumb.label}`}>
            {crumb.href ? (
              <Link href={crumb.href}>{crumb.label}</Link>
            ) : (
              <span aria-current="page">{crumb.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
