/**
 * Naming a principal on screen, and saying which one it is when the name is not enough.
 *
 * ADG's identity is the SID ([ADR-0001](../../docs/decisions/0001-sid-as-identity.md)); a
 * display name is metadata that may be stale, missing, or shared. Three situations in a
 * Windows estate make a name alone actively misleading, and all three are ordinary:
 *
 * - **BUILTIN\Administrators exists on every computer.** `S-1-5-32-544` is a different
 *   group on every server that reported it, so ADG keys it `fs01|S-1-5-32-544`. Two rows
 *   reading "Administrators" are not a duplicate; they are two groups.
 * - **Two domains can hold two "Finance" groups.** Same name, different domain SID.
 * - **An ACE can name a SID no run has described.** There is no name at all, only the SID,
 *   and that is a finding rather than a blank cell.
 *
 * So the SID is always available behind the name, and when a name is ambiguous *within the
 * list being shown* the qualifier is promoted next to it rather than left to be hunted for.
 * Nothing here decides access, and nothing here resolves a SID: this module reads what the
 * API sent and never fills a gap in it.
 */

import type { PrincipalDetail, PrincipalSummary } from "@/lib/contracts";

/** The route a principal's detail page lives at: `PRINCIPAL_PATH/{key}/{view}`. */
export const PRINCIPAL_PATH = "/identities/principal";

type AnyPrincipal = PrincipalSummary | PrincipalDetail;

/**
 * What to call this principal: display name, account name, qualified (UPN) name, the name
 * an ACE last carried, then the SID.
 *
 * The SID is the last resort rather than an error: an unresolved trustee is a real entry
 * on a real ACL, and rendering it as "(unknown)" would hide the one identifier that can
 * still be searched for.
 */
export function principalLabel(principal: AnyPrincipal): string {
  return (
    principal.display_name ??
    principal.sam_account_name ??
    principal.user_principal_name ??
    principal.last_known_name ??
    principal.sid
  );
}

/** True, false, or null — null is "no run has said", and is never rendered as "user". */
export function isGroup(principal: AnyPrincipal): boolean | null {
  return principal.is_group ?? null;
}

/** The word for what this principal is, honest about not knowing. */
export function principalKindLabel(principal: AnyPrincipal): string {
  if (principal.kind) {
    return principal.kind.replace(/_/g, " ");
  }
  const group = isGroup(principal);
  if (group === true) {
    return "group";
  }
  if (group === false) {
    return "account";
  }
  return "kind unknown";
}

/**
 * The domain part of a SID: everything before the relative identifier.
 *
 * `S-1-5-21-1-2-3-1104` belongs to domain `S-1-5-21-1-2-3`. A SID with no RID to strip —
 * `S-1-1-0`, Everyone — has no domain, and null says so rather than returning the SID
 * itself and implying one.
 */
export function sidDomain(sid: string): string | null {
  const parts = sid.split("-");
  if (parts.length < 5 || parts[0] !== "S") {
    return null;
  }
  return parts.slice(0, -1).join("-");
}

/**
 * What distinguishes this principal from another of the same name.
 *
 * A host-scoped principal is qualified by its host, because that is what makes it a
 * different group. Everything else is qualified by its domain SID.
 */
export function principalQualifier(principal: AnyPrincipal): string | null {
  if (principal.host_key) {
    return `on ${principal.host_key}`;
  }
  const domain = sidDomain(principal.sid);
  return domain === null ? null : `domain ${domain}`;
}

/**
 * The labels that occur more than once in one list, case-insensitively.
 *
 * Case-insensitively because Windows account names are, and because "Finance" and
 * "finance" reading as two distinct entries is the confusion this exists to prevent.
 */
export function ambiguousLabels(principals: readonly AnyPrincipal[]): Set<string> {
  const seen = new Map<string, number>();
  for (const principal of principals) {
    const label = principalLabel(principal).toLowerCase();
    seen.set(label, (seen.get(label) ?? 0) + 1);
  }
  return new Set([...seen].filter(([, count]) => count > 1).map(([label]) => label));
}

/** Whether this principal's name needs its qualifier promoted, given the list it is in. */
export function isAmbiguous(principal: AnyPrincipal, ambiguous: Set<string>): boolean {
  return ambiguous.has(principalLabel(principal).toLowerCase());
}

/**
 * Whether nothing has said what this SID is — no record at all, or a record saying so.
 *
 * Two different absences, one answer. `resolved` is false only when ADG holds no record;
 * a record whose `kind` is `unresolved` exists precisely to say that the collector that
 * saw the SID could not resolve it. Reading `resolved` alone would render that SID as a
 * resolved principal — the false certainty ADG-DEMO-01 removed for SYSTEM.
 */
export function isUnresolved(principal: AnyPrincipal): boolean {
  return principal.resolved === false || principal.kind === "unresolved";
}

/**
 * Things about this principal a reader must not miss.
 *
 * A disabled account still appears on ACLs and still shows in an effective-access answer,
 * because the ACL has not changed; the account state is the reason the answer is not the
 * risk it looks like. Same for a deleted one. Account metadata such as a group's scope is
 * not a note: it is a column where a table needs it, and in the Identity information.
 */
export function principalNotes(principal: AnyPrincipal): string[] {
  const notes: string[] = [];
  if (isUnresolved(principal)) {
    notes.push(
      principal.unresolved_reason
        ? `unresolved (${principal.unresolved_reason.replace(/_/g, " ")})`
        : "unresolved — no run has described this SID",
    );
  }
  if (principal.enabled === false) {
    notes.push("disabled");
  }
  if (principal.is_deleted) {
    notes.push("deleted");
  }
  return notes;
}

/**
 * What ADG knows about whether a principal's account was deleted, from the one fact that says.
 *
 * Two fields can: `is_deleted`, a directory fact only users and groups carry, and
 * `unresolved_reason: "deleted"`, a collector's report that the SID's account is gone (an
 * orphaned ACE). A record of kind `unresolved` never reports `is_deleted`, so its `false` is
 * a column default, not an observation: reading it as "no" contradicts the banner above it.
 */
export function deletedState(principal: PrincipalDetail): "yes" | "reported" | "unknown" | "no" {
  if (principal.is_deleted) {
    return "yes";
  }
  if (isUnresolved(principal)) {
    return principal.unresolved_reason === "deleted" ? "reported" : "unknown";
  }
  return "no";
}

/**
 * The detail page for a principal, keyed by the storage key the API issued — never by a
 * name, which two principals can share.
 *
 * The key is a path segment so that every view of one principal shares one layout: moving
 * between views re-renders the view alone, and the identity above it stays as it was.
 */
export function principalHref(principal: AnyPrincipal, view?: string): string {
  return principalHrefForKey(principal.key, view);
}

/** The same, from a bare key — for a search hit, which carries no principal object. */
export function principalHrefForKey(key: string, view?: string): string {
  const base = `${PRINCIPAL_PATH}/${encodeURIComponent(key)}`;
  return view ? `${base}/${view}` : base;
}

/**
 * The principals along a chain of keys, as the API labelled them.
 *
 * An answer that did not label its chain (an older API, or one that lost a label) still
 * renders: each key becomes an unresolved principal, which shows its SID and links nowhere.
 */
export function chainPrincipals(
  path: readonly string[],
  labelled: readonly PrincipalSummary[] | undefined,
): PrincipalSummary[] {
  if (labelled && labelled.length === path.length) {
    return [...labelled];
  }
  return path.map((key) => unresolvedPrincipal(key));
}

function unresolvedPrincipal(key: string): PrincipalSummary {
  const separator = key.indexOf("|");
  return {
    key,
    sid: separator === -1 ? key : key.slice(separator + 1),
    host_key: separator === -1 ? null : key.slice(0, separator),
    resolved: false,
    kind: null,
    is_group: null,
    display_name: null,
    sam_account_name: null,
    user_principal_name: null,
    distinguished_name: null,
    last_known_name: null,
    enabled: null,
    is_deleted: false,
    group_scope: null,
    group_type: null,
    unresolved_reason: null,
  };
}

/** One relationship view of a principal: a section of its page, and a path segment. */
export interface PrincipalView {
  id: string;
  label: string;
  /** One line explaining what the view is. */
  hint: string;
}

/**
 * The relationship views that mean something for this principal, the default first.
 *
 * Member views exist only where the principal can have members: "Direct members: 0" beside
 * an account invites reading it as a group. A principal of unknown kind is offered every
 * view, because ADG cannot rule one out, and its page says why it does not know.
 */
export function principalViews(principal: AnyPrincipal): PrincipalView[] {
  const group = isGroup(principal);
  const members: PrincipalView[] =
    group === false
      ? []
      : [
          {
            id: "members",
            label: "Direct members",
            hint: "What this group's own membership list names.",
          },
          {
            id: "effective-members",
            label: "Effective members",
            hint: "Everything reached through nesting, with the chain that puts it there.",
          },
        ];
  return [
    ...members,
    {
      id: "groups",
      label: group === true ? "Parent groups" : "Direct groups",
      hint: "Groups whose membership list names this principal directly.",
    },
    {
      id: "effective-groups",
      label: "Effective groups",
      hint: "Every group reached through nesting — the set that becomes token SIDs.",
    },
    {
      id: "shares",
      label: "Reachable shares",
      hint: "Engine answers: the share ACL crossed with the NTFS ACL of the directory it publishes.",
    },
    {
      id: "directories",
      label: "Reachable directories",
      hint: "Engine answers for directories whose ACLs could involve this principal.",
    },
    {
      id: "acl-references",
      label: "Named on share ACLs",
      hint: "Raw entries that name this SID. Being named is not the same as having access.",
    },
  ];
}

/** Every view id any principal can have, for validating a URL segment. */
export const PRINCIPAL_VIEW_IDS: readonly string[] = [
  "members",
  "effective-members",
  "groups",
  "effective-groups",
  "shares",
  "directories",
  "acl-references",
];
