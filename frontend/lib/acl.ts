/**
 * Reading a raw ACL on screen without implying an access answer.
 *
 * This is the module requirement 7 lives in. A raw ACE is a fact about a descriptor: this
 * directory's DACL, at this position, names this SID with this mask. It is **not** a
 * statement that the SID can reach the data, and four ordinary situations make the two
 * diverge:
 *
 * - the share ACL above it is narrower, so the file-system grant is unreachable remotely;
 * - a Deny earlier in the DACL removes what this entry grants;
 * - the entry is INHERIT_ONLY, so it applies to children and grants nothing here;
 * - the trustee is a group, and who is actually inside it is a different question.
 *
 * So everything here describes the entry and stops. The effective-access answer comes from
 * the backend engine, which is checked against Windows' own `AuthzAccessCheck`; a second
 * reading of the same ACEs in a browser could only ever disagree with it.
 *
 * The inheritance helpers exist for the same reason in the other direction: a directory
 * that blocks inheritance is where a permission change actually happened, and it is the
 * thing an administrator has to find.
 */

import type {
  AclHashView,
  NtfsAceView,
  NtfsResourceDetailView,
  NtfsResourceSummary,
} from "@/lib/contracts";

/** Wording used wherever raw entries are shown, so the caveat cannot drift between pages. */
export const RAW_ACL_NOTICE =
  "These are the entries on the descriptor, exactly as collected. An entry here is not " +
  "access: the other layer, an earlier Deny, an inherit-only flag, or an empty group can " +
  "each make it grant nothing.";

/** The matching wording wherever an engine answer is shown. */
export const EFFECTIVE_ACCESS_NOTICE =
  "These are access-check results from the ADG engine: the share ACL and the NTFS ACL " +
  "evaluated in order against a token built from the principal's memberships. Each answer " +
  "carries its own certainty.";

export type Tone = "ok" | "warn" | "bad" | "info";

export interface Note {
  tone: Tone;
  headline: string;
  detail: string;
}

/** Where to go and make a change: the ACL of this object, or an ancestor's. */
export function aceOrigin(ace: NtfsAceView): "explicit" | "inherited" {
  return ace.source === "inherited" ? "inherited" : "explicit";
}

/**
 * What a reader must know about this entry beyond its trustee and mask.
 *
 * The inherit-only case is the one that matters most: such an entry is in the list, names
 * a trustee, carries a mask — and grants nothing on the directory being looked at. Shown
 * without a note it reads as a grant that does not exist.
 */
export function aceNotes(ace: NtfsAceView): string[] {
  const notes: string[] = [];
  if (!ace.applies_to_this_object) {
    notes.push("inherit-only — grants nothing on this directory");
  }
  if (ace.is_inheritable) {
    notes.push("inheritable by children");
  }
  if (ace.inherited_from) {
    notes.push(`inherited from ${ace.inherited_from}`);
  }
  if (ace.unrecognized_bits !== 0) {
    notes.push(`unrecognized mask bits ${formatMask(ace.unrecognized_bits)}`);
  }
  return notes;
}

/** A mask as Windows writes it, so it can be compared with an `icacls` output by eye. */
export function formatMask(mask: number): string {
  return `0x${(mask >>> 0).toString(16).toUpperCase().padStart(8, "0")}`;
}

/**
 * Whether this directory takes its parent's entries, and whether the two fields agree.
 *
 * `dacl_protected` is the SE_DACL_PROTECTED bit off the descriptor; `inheritance_enabled`
 * is the collector's own statement. They are stored separately and are normally two
 * readings of one fact, so a disagreement is reported rather than resolved — silently
 * preferring one would hide a collector defect behind a confident answer.
 */
export function inheritanceNotes(resource: NtfsResourceSummary): Note[] {
  const notes: Note[] = [];

  if (resource.dacl_protected) {
    notes.push({
      tone: "warn",
      headline: "Inheritance is blocked here",
      detail:
        "SE_DACL_PROTECTED is set: this directory does not receive its parent's " +
        "inheritable entries. Permissions below this point are governed here, not above.",
    });
  } else {
    notes.push({
      tone: "ok",
      headline: "Inheritance is enabled",
      detail: "This directory receives its parent's inheritable entries.",
    });
  }

  if (resource.dacl_protected === resource.inheritance_enabled) {
    notes.push({
      tone: "warn",
      headline: "The two inheritance fields disagree",
      detail:
        `The descriptor reports dacl_protected=${resource.dacl_protected} and the ` +
        `collector reports inheritance_enabled=${resource.inheritance_enabled}. These are ` +
        "normally two readings of one fact; treat this directory's inheritance state as " +
        "unconfirmed.",
    });
  }

  if (resource.is_acl_boundary) {
    notes.push({
      tone: "info",
      headline: "This is an ACL boundary",
      detail: resource.boundary_reason
        ? `The collector's reason: ${resource.boundary_reason.replace(/_/g, " ")}.`
        : "This directory's DACL differs from its parent's. The collector that wrote this " +
          "row predates the reason field, so no reason was recorded.",
    });
  }

  return notes;
}

/**
 * The two DACL states that are not an entry list at all.
 *
 * A NULL DACL and an empty DACL both render as a table with no rows and mean opposite
 * things — everyone has full access, and nobody has any. Neither may be left to be
 * inferred from an empty table.
 */
export function daclNotes(resource: NtfsResourceSummary): Note[] {
  const notes: Note[] = [];

  if (!resource.dacl_present) {
    notes.push({
      tone: "bad",
      headline: "NULL DACL — everyone has full access",
      detail:
        "This directory has no DACL. Windows grants every requester full access, and the " +
        "empty entry list below is not a restriction. This is always a finding.",
    });
  } else if (resource.denies_everyone) {
    notes.push({
      tone: "warn",
      headline: "Empty DACL — nobody has access through it",
      detail:
        "The DACL is present and contains no entries, so it grants nothing. The owner " +
        "keeps READ_CONTROL and WRITE_DAC regardless, and can therefore restore access.",
    });
  }

  return notes;
}

/**
 * Entries the descriptor declared and ADG does not hold.
 *
 * A coverage gap, reported rather than reconciled away: the list on screen is a subset,
 * and the absence of an entry from it is not the absence of that entry from the ACL.
 */
export function storedEntryNotes(resource: NtfsResourceDetailView): Note[] {
  if (resource.stored_ace_count >= resource.declared_ace_count) {
    return [];
  }
  return [
    {
      tone: "warn",
      headline: "Some entries were never stored",
      detail:
        `The descriptor declared ${resource.declared_ace_count} entries and ADG holds ` +
        `${resource.stored_ace_count}. What is listed is a subset; do not read the absence ` +
        "of an entry as its absence from the ACL.",
    },
  ];
}

/**
 * What the ACL hash says about the reading ADG holds.
 *
 * The digest is over a normalized document that includes evaluation order, so an unordered
 * digest can never equal an ordered one. Null `agrees` is unknown, and is worded as
 * unknown: a collector that computed no digest has not disagreed with anything.
 */
export function aclHashNotes(hash: AclHashView | null | undefined): Note[] {
  if (!hash) {
    return [];
  }
  const notes: Note[] = [];

  if (hash.agrees === false) {
    notes.push({
      tone: "bad",
      headline: "The stored ACL does not match what the collector read",
      detail:
        `The collector reported ${hash.reported ?? "a digest"} and ADG computes ` +
        `${hash.computed} over the entries it holds. The list below is not the descriptor ` +
        "the collector saw.",
    });
  } else if (hash.agrees === null) {
    notes.push({
      tone: "info",
      headline: "No digest was reported",
      detail:
        "The collector computed no ACL digest, so ADG cannot confirm that the entries it " +
        "holds are the whole descriptor. This is unknown, not a disagreement.",
    });
  }

  if (!hash.ordered) {
    notes.push({
      tone: "warn",
      headline: "Entry order was not recorded",
      detail:
        "At least one entry arrived without its position in the DACL. Order decides " +
        "whether a Deny precedes an Allow, so this digest cannot be compared with one " +
        "taken over an ordered ACL.",
    });
  }

  if (!hash.ace_count_agrees) {
    notes.push({
      tone: "warn",
      headline: "Entry counts disagree",
      detail:
        `The descriptor declared ${hash.declared_ace_count} entries; ADG holds ` +
        `${hash.stored_ace_count}.`,
    });
  }

  return notes;
}

/**
 * Whether the boundary verdict ADG computed matches the one the collector reported.
 *
 * A disagreement is not automatically a defect — the collector compared against the parent
 * as it stood at scan time, and ADG compares against the parent it holds — so the
 * explanation says which parent each side used rather than declaring a winner.
 */
export function boundaryNotes(resource: NtfsResourceDetailView): Note[] {
  const boundary = resource.boundary;
  const notes: Note[] = [];

  if (!boundary.parent_observed) {
    notes.push({
      tone: "info",
      headline: resource.is_share_root ? "No parent inside the share" : "The parent has not been read",
      detail: resource.is_share_root
        ? "This is the directory the share publishes; its parent lies outside the share."
        : "No run has read this directory's parent, so ADG cannot check the boundary " +
          "verdict for itself. The collector's own verdict is shown above.",
    });
    return notes;
  }

  if (boundary.agrees === false) {
    notes.push({
      tone: "warn",
      headline: "ADG and the collector disagree about the boundary",
      detail:
        `The collector reported ${boundary.reported ? "a boundary" : "no boundary"}; ADG ` +
        `computes ${boundary.computed ? "a boundary" : "no boundary"} from the parent it ` +
        `holds${boundary.computed_reason ? ` (${boundary.computed_reason.replace(/_/g, " ")})` : ""}. ` +
        (boundary.parent_acl_hash_agrees === false
          ? "The parent ADG holds is a different reading from the one the collector " +
            "compared against, which is enough to explain this on its own."
          : "Both sides compared against the same parent reading."),
    });
  }

  if (!boundary.projection_available) {
    notes.push({
      tone: "info",
      headline: "The parent's entries were not in reach to compare",
      detail:
        "ADG could not project what a cleanly inheriting child of the parent would carry, " +
        "so the comparison below is the collector's alone.",
    });
  }

  return notes;
}
