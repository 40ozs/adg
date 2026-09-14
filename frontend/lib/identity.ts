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
 * So the SID is always available beside the name, and when a name is ambiguous *within the
 * list being shown* the qualifier is promoted next to it rather than left to be hunted for.
 * Nothing here decides access, and nothing here resolves a SID: this module reads what the
 * API sent and never fills a gap in it.
 */

import type { PrincipalDetail, PrincipalSummary } from "@/lib/contracts";
import { hrefWith } from "@/lib/paging";

/** The route a principal's detail page lives at. */
export const PRINCIPAL_PATH = "/identities/principal";

type AnyPrincipal = PrincipalSummary | PrincipalDetail;

/**
 * What to call this principal.
 *
 * The SID is the last resort rather than an error: an unresolved trustee is a real entry
 * on a real ACL, and rendering it as "(unknown)" would hide the one identifier that can
 * still be searched for.
 */
export function principalLabel(principal: AnyPrincipal): string {
  return (
    principal.display_name ??
    principal.sam_account_name ??
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
 * Things about this principal a reader must not miss.
 *
 * A disabled account still appears on ACLs and still shows in an effective-access answer,
 * because the ACL has not changed; the account state is the reason the answer is not the
 * risk it looks like. Same for a deleted one.
 */
export function principalNotes(principal: AnyPrincipal): string[] {
  const notes: string[] = [];
  if (principal.resolved === false) {
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
  if (principal.group_scope) {
    notes.push(principal.group_scope.replace(/_/g, " "));
  }
  return notes;
}

/** The detail page for a principal, keyed by the storage key the API issued. */
export function principalHref(principal: AnyPrincipal, extra: Record<string, string> = {}): string {
  return hrefWith(PRINCIPAL_PATH, { key: principal.key, ...extra });
}

/** The same, from a bare key — for a search hit, which carries no principal object. */
export function principalHrefForKey(key: string, extra: Record<string, string> = {}): string {
  return hrefWith(PRINCIPAL_PATH, { key, ...extra });
}
