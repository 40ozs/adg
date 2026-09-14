/**
 * The primary navigation, as data.
 *
 * Each item names the capability it needs. The capability list comes from `/auth/me` — the
 * backend's own answer — so this module never reproduces the role-to-capability table. That
 * matters: a second copy of an authorization table is a second copy that can be wrong, and
 * the wrong one is always the one somebody trusts.
 *
 * Filtering the navigation is a courtesy, not a control. Typing the URL of a hidden section
 * reaches a page whose data call the API refuses; the page then renders the "forbidden"
 * state. Nothing here is load-bearing for security.
 */

export interface NavItem {
  /** Stable id, used as the React key and in tests. */
  id: string;
  label: string;
  href: string;
  /** The capability required to see anything on this page, or null for always-visible. */
  requires: string | null;
  /** Shown as a badge; the section exists but has no data behind it yet. */
  placeholder?: boolean;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    href: "/",
    requires: null,
    description: "Collection state and where to start.",
  },
  {
    id: "resources",
    label: "Resources",
    href: "/resources",
    requires: "resources:read",
    description: "Servers, shares, and the directories beneath them.",
  },
  {
    id: "identities",
    label: "Identities",
    href: "/identities",
    requires: "identities:read",
    description: "Users, groups, and the memberships that connect them.",
  },
  {
    id: "access",
    label: "Access",
    href: "/access",
    requires: "access:read",
    description: "Who can reach what, and the reasoning behind each answer.",
  },
  {
    id: "risks",
    label: "Risks",
    href: "/risks",
    requires: "risks:read",
    placeholder: true,
    description: "Findings across the estate. Arrives in a later phase.",
  },
  {
    id: "changes",
    label: "Changes",
    href: "/changes",
    requires: "changes:read",
    placeholder: true,
    description: "What changed between scans. Arrives in a later phase.",
  },
  {
    id: "collectors",
    label: "Collectors",
    href: "/collectors",
    requires: "collectors:read",
    description: "Scan runs, coverage, and what each run could not read.",
  },
  {
    id: "settings",
    label: "Settings",
    href: "/settings",
    requires: "settings:read",
    description: "Deployment configuration and the authorization model.",
  },
] as const;

/** The items a principal holding `capabilities` should be offered. */
export function visibleNavItems(
  capabilities: readonly string[],
  items: readonly NavItem[] = NAV_ITEMS,
): NavItem[] {
  const held = new Set(capabilities);
  return items.filter((item) => item.requires === null || held.has(item.requires));
}

/**
 * Which nav item a path belongs to.
 *
 * Longest matching href wins, so `/resources/servers/fs01` highlights Resources while `/`
 * only ever matches the overview. A naive `startsWith` against `/` would mark every page as
 * Overview.
 */
export function activeNavItem(
  pathname: string,
  items: readonly NavItem[] = NAV_ITEMS,
): NavItem | null {
  let best: NavItem | null = null;
  for (const item of items) {
    const matches =
      item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
    if (matches && (best === null || item.href.length > best.href.length)) {
      best = item;
    }
  }
  return best;
}
