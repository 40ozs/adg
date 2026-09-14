/**
 * Reading a raw descriptor without turning it into an access claim.
 *
 * Every assertion below is about a situation where the obvious rendering is wrong: an
 * inherit-only entry that grants nothing, an empty table that means "everyone", another
 * empty table that means "nobody", an unknown digest treated as agreement, and a boundary
 * disagreement explained away instead of shown.
 */

import { describe, expect, it } from "vitest";

import {
  aceNotes,
  aceOrigin,
  aclHashNotes,
  boundaryNotes,
  daclNotes,
  formatMask,
  inheritanceNotes,
  storedEntryNotes,
} from "@/lib/acl";
import { aclHash, boundary, ntfsAce, resourceDetail, resourceSummary } from "./factories";

const headlines = (notes: { headline: string }[]): string[] => notes.map((note) => note.headline);

describe("one entry", () => {
  it("says where to make a change", () => {
    expect(aceOrigin(ntfsAce({ source: "inherited" }))).toBe("inherited");
    expect(aceOrigin(ntfsAce({ source: "explicit" }))).toBe("explicit");
  });

  it("warns that an inherit-only entry grants nothing here", () => {
    // The entry is on the list, names a trustee and carries a mask. Without this note it
    // reads as a grant that does not exist.
    expect(aceNotes(ntfsAce({ applies_to_this_object: false }))).toContain(
      "inherit-only — grants nothing on this directory",
    );
  });

  it("keeps mask bits it cannot name visible", () => {
    expect(aceNotes(ntfsAce({ unrecognized_bits: 0x40000000 }))).toContain(
      "unrecognized mask bits 0x40000000",
    );
  });

  it("renders a mask the way Windows writes one", () => {
    expect(formatMask(0x001200a9)).toBe("0x001200A9");
    // -1 as a signed 32-bit integer is GENERIC_ALL | everything; it must not render as
    // "0x-1" or overflow into a longer string.
    expect(formatMask(0xffffffff)).toBe("0xFFFFFFFF");
    expect(formatMask(0)).toBe("0x00000000");
  });
});

describe("inheritance", () => {
  it("reports a protected DACL as a block, and as the place permissions are governed", () => {
    const notes = inheritanceNotes(resourceSummary({ dacl_protected: true, inheritance_enabled: false }));

    expect(headlines(notes)).toContain("Inheritance is blocked here");
  });

  it("reports the ordinary case as enabled", () => {
    expect(headlines(inheritanceNotes(resourceSummary()))).toContain("Inheritance is enabled");
  });

  it("shows a disagreement between the two fields rather than picking one", () => {
    // They are two readings of one fact. Silently preferring one hides a collector defect
    // behind a confident answer.
    const notes = inheritanceNotes(
      resourceSummary({ dacl_protected: true, inheritance_enabled: true }),
    );

    expect(headlines(notes)).toContain("The two inheritance fields disagree");
  });

  it("does not claim a disagreement when the fields are consistent", () => {
    expect(headlines(inheritanceNotes(resourceSummary()))).not.toContain(
      "The two inheritance fields disagree",
    );
  });

  it("names a boundary and carries the collector's reason", () => {
    const notes = inheritanceNotes(
      resourceSummary({ is_acl_boundary: true, boundary_reason: "explicit_ace_added" }),
    );

    expect(notes.at(-1)?.detail).toContain("explicit ace added");
  });
});

describe("the two DACL states that are not an entry list", () => {
  it("announces a NULL DACL as full access for everyone", () => {
    const notes = daclNotes(resourceSummary({ dacl_present: false, declared_ace_count: 0 }));

    expect(headlines(notes)).toEqual(["NULL DACL — everyone has full access"]);
    expect(notes[0].tone).toBe("bad");
  });

  it("announces an empty DACL as the opposite", () => {
    // Both render as a table with no rows and mean opposite things.
    const notes = daclNotes(resourceSummary({ denies_everyone: true, declared_ace_count: 0 }));

    expect(headlines(notes)).toEqual(["Empty DACL — nobody has access through it"]);
  });

  it("says nothing about an ordinary DACL", () => {
    expect(daclNotes(resourceSummary())).toEqual([]);
  });

  it("reports entries that were declared and never stored", () => {
    const notes = storedEntryNotes(resourceDetail({ declared_ace_count: 9, stored_ace_count: 4 }));

    expect(headlines(notes)).toEqual(["Some entries were never stored"]);
  });

  it("does not report a gap when there is none", () => {
    expect(storedEntryNotes(resourceDetail())).toEqual([]);
  });
});

describe("the ACL digest", () => {
  it("reports a mismatch as the list not being the descriptor", () => {
    const notes = aclHashNotes(aclHash({ agrees: false, reported: "sha256:other" }));

    expect(headlines(notes)).toContain("The stored ACL does not match what the collector read");
  });

  it("words an absent digest as unknown, never as agreement", () => {
    const notes = aclHashNotes(aclHash({ agrees: null, reported: null }));

    expect(headlines(notes)).toContain("No digest was reported");
    expect(notes[0].detail).toContain("unknown, not a disagreement");
  });

  it("says nothing when the digests agree", () => {
    expect(aclHashNotes(aclHash())).toEqual([]);
  });

  it("warns that an unordered digest cannot be compared with an ordered one", () => {
    expect(headlines(aclHashNotes(aclHash({ ordered: false })))).toContain(
      "Entry order was not recorded",
    );
  });

  it("returns nothing at all when there is no digest object", () => {
    expect(aclHashNotes(null)).toEqual([]);
  });
});

describe("the boundary verdict", () => {
  it("explains a share root's missing parent as outside the share", () => {
    const notes = boundaryNotes(
      resourceDetail({ is_share_root: true, boundary: boundary({ parent_observed: false }) }),
    );

    expect(headlines(notes)).toEqual(["No parent inside the share"]);
  });

  it("distinguishes an unread parent from a share root", () => {
    const notes = boundaryNotes(
      resourceDetail({ is_share_root: false, boundary: boundary({ parent_observed: false }) }),
    );

    expect(headlines(notes)).toEqual(["The parent has not been read"]);
  });

  it("shows a disagreement and names the parent each side used", () => {
    const notes = boundaryNotes(
      resourceDetail({
        boundary: boundary({ agrees: false, reported: true, computed: false, parent_acl_hash_agrees: false }),
      }),
    );

    expect(headlines(notes)).toContain("ADG and the collector disagree about the boundary");
    expect(notes[0].detail).toContain("different reading");
  });

  it("says nothing when the two verdicts agree on the same parent", () => {
    expect(boundaryNotes(resourceDetail())).toEqual([]);
  });
});
