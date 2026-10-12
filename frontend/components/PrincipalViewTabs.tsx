"use client";

import type { JSX } from "react";
import { usePathname } from "next/navigation";

import { Tabs, type TabDefinition } from "@/components/Tabs";

/**
 * A principal's view tabs, marking the open view from the URL.
 *
 * The tabs live in the principal's layout, which does not render again when the view
 * changes, so the current view is read from the path on the client rather than passed
 * down: `/identities/principal/{key}/{view}`, with no view meaning the first tab.
 */
export function PrincipalViewTabs({
  label,
  tabs,
}: {
  label: string;
  tabs: readonly TabDefinition[];
}): JSX.Element {
  const current = usePathname().split("/")[4] ?? tabs[0]?.id ?? "";
  return <Tabs label={label} tabs={tabs} current={current} />;
}
