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

/**
 * The conceptual groups the navigation is arranged in (ADG-UX-01). The order here is the
 * order on screen. A group is a label, not a section: it has no page and is never a focus
 * stop.
 */
export const NAV_GROUPS = [
  { id: "investigate", label: "Investigate" },
  { id: "directory", label: "Directory" },
  { id: "act", label: "Act" },
  { id: "system", label: "System" },
] as const;

export type NavGroupId = (typeof NAV_GROUPS)[number]["id"];

export interface NavItem {
  /** Stable id, used as the React key and in tests. */
  id: string;
  label: string;
  href: string;
  group: NavGroupId;
  /** The capability required to see anything on this page, or null for always-visible. */
  requires: string | null;
  /** Shown as a badge; the section exists but has no data behind it yet. */
  placeholder?: boolean;
  /** What the section is for, in one sentence. Also the link's tooltip. */
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    href: "/",
    group: "investigate",
    requires: null,
    description: "Collection state and where to start.",
  },
  {
    // The route stays /risks: deep links, the hosted checks and the API (`risks:read`) all
    // use it. Only the user-facing word changed, because these are rule matches, not scores.
    id: "risks",
    label: "Findings",
    href: "/risks",
    group: "investigate",
    requires: "risks:read",
    description:
      "Conditions ADG detected in the collected access data, with the evidence behind each.",
  },
  {
    id: "access",
    label: "Access",
    href: "/access",
    group: "investigate",
    requires: "access:read",
    description: "Who can reach a resource, what an identity can reach, and the access paths that explain why.",
  },
  {
    id: "changes",
    label: "Changes",
    href: "/changes",
    group: "investigate",
    requires: "changes:read",
    description: "What changed in the estate, and whether those changes affected access.",
  },
  {
    id: "identities",
    label: "Identities",
    href: "/identities",
    group: "directory",
    requires: "identities:read",
    description: "Users, groups, and the memberships that connect them.",
  },
  {
    id: "resources",
    label: "Resources",
    href: "/resources",
    group: "directory",
    requires: "resources:read",
    description: "Servers, shares, and the directories beneath them.",
  },
  {
    id: "governance",
    label: "Reviews",
    href: "/governance",
    group: "act",
    requires: "governance:read",
    description: "Review access decisions that require human confirmation.",
  },
  {
    id: "simulations",
    label: "What-if",
    href: "/simulations",
    group: "act",
    requires: "simulations:read",
    description: "Propose a permission change and measure what it would do. Changes nothing.",
  },
  {
    id: "collectors",
    label: "Collectors",
    href: "/collectors",
    group: "system",
    requires: "collectors:read",
    description: "Scan runs, data coverage, and what each run could not read.",
  },
  {
    id: "settings",
    label: "Settings",
    href: "/settings",
    group: "system",
    requires: "settings:read",
    description: "Deployment configuration and the authorization model.",
  },
] as const;

/** A document title: what the page is, then the product. Short, so a narrow tab still says it. */
export function documentTitle(name: string): string {
  return `${name} — ADG`;
}

/**
 * A section's document title, from its nav label, so the browser tab and the nav never
 * disagree (`/risks` is "Findings — ADG").
 */
export function sectionTitle(id: string): string {
  const item = NAV_ITEMS.find((candidate) => candidate.id === id);
  if (item === undefined) {
    throw new Error(`No navigation item "${id}".`);
  }
  return documentTitle(item.label);
}

/** The items a principal holding `capabilities` should be offered. */
export function visibleNavItems(
  capabilities: readonly string[],
  items: readonly NavItem[] = NAV_ITEMS,
): NavItem[] {
  const held = new Set(capabilities);
  return items.filter((item) => item.requires === null || held.has(item.requires));
}

/**
 * `items` arranged under their groups, in group order. A group with nothing visible is
 * dropped, so a heading never announces a section the account cannot open.
 */
export function groupNavItems(
  items: readonly NavItem[],
): { id: NavGroupId; label: string; items: NavItem[] }[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: items.filter((item) => item.group === group.id),
  })).filter((group) => group.items.length > 0);
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
