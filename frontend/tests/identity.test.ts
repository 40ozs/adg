/**
 * Naming a principal, and refusing to guess.
 *
 * Two families of failure are pinned here. The first is invention: rendering an undescribed
 * SID as an account, or a missing name as "unknown". The second is collision: two
 * `BUILTIN\Administrators` rows that look like one group, which is the normal state of any
 * estate with more than one file server.
 */

import { describe, expect, it } from "vitest";

import {
  ambiguousLabels,
  chainPrincipals,
  deletedState,
  isAmbiguous,
  isGroup,
  principalHref,
  principalHrefForKey,
  principalKindLabel,
  principalLabel,
  principalNotes,
  principalQualifier,
  principalViews,
  sidDomain,
} from "@/lib/identity";
import { principal, principalDetail } from "./factories";

describe("what to call a principal", () => {
  it("prefers the display name", () => {
    expect(
      principalLabel(principal({ display_name: "Finance", sam_account_name: "finance" })),
    ).toBe("Finance");
  });

  it("falls back through the account name and the last name ever seen", () => {
    expect(principalLabel(principal({ sam_account_name: "finance" }))).toBe("finance");
    expect(principalLabel(principal({ last_known_name: "CORP\\finance" }))).toBe("CORP\\finance");
  });

  it("ends at the SID rather than at a placeholder", () => {
    // An orphaned trustee is a real entry on a real ACL. "(unknown)" would hide the one
    // identifier that can still be searched for.
    expect(principalLabel(principal({ sid: "S-1-5-21-9-9-9-500" }))).toBe("S-1-5-21-9-9-9-500");
  });
});

describe("what kind of principal it is", () => {
  it("never turns 'nobody said' into an account", () => {
    expect(isGroup(principal())).toBeNull();
    expect(principalKindLabel(principal())).toBe("kind unknown");
  });

  it("uses the API's own word when there is one", () => {
    expect(principalKindLabel(principal({ kind: "service_account" }))).toBe("service account");
  });

  it("falls back to the group flag", () => {
    expect(principalKindLabel(principal({ is_group: true }))).toBe("group");
    expect(principalKindLabel(principal({ is_group: false }))).toBe("account");
  });
});

describe("the domain half of a SID", () => {
  it("is everything before the relative identifier", () => {
    expect(sidDomain("S-1-5-21-1-2-3-1104")).toBe("S-1-5-21-1-2-3");
  });

  it("is null for a SID with no domain to strip", () => {
    // S-1-1-0 is Everyone. Returning the SID itself would imply a domain that does not exist.
    expect(sidDomain("S-1-1-0")).toBeNull();
    expect(sidDomain("not-a-sid")).toBeNull();
  });
});

describe("disambiguating a name", () => {
  const fs01 = principal({
    key: "fs01|S-1-5-32-544",
    sid: "S-1-5-32-544",
    host_key: "fs01",
    display_name: "Administrators",
  });
  const fs02 = principal({
    key: "fs02|S-1-5-32-544",
    sid: "S-1-5-32-544",
    host_key: "fs02",
    display_name: "Administrators",
  });
  const finance = principal({ display_name: "Finance" });

  it("finds labels that occur more than once in the list", () => {
    expect(ambiguousLabels([fs01, fs02, finance])).toEqual(new Set(["administrators"]));
  });

  it("matches case-insensitively, as Windows account names do", () => {
    const lower = principal({ key: "fs03|S-1-5-32-544", display_name: "administrators" });

    expect(ambiguousLabels([fs01, lower]).has("administrators")).toBe(true);
  });

  it("is a property of the list, not of the principal", () => {
    // The same group is unambiguous on its own page and ambiguous in a list beside its twin.
    expect(isAmbiguous(fs01, ambiguousLabels([fs01]))).toBe(false);
    expect(isAmbiguous(fs01, ambiguousLabels([fs01, fs02]))).toBe(true);
  });

  it("qualifies a host-scoped principal by its host", () => {
    expect(principalQualifier(fs01)).toBe("on fs01");
    expect(principalQualifier(fs02)).toBe("on fs02");
  });

  it("qualifies everything else by its domain SID", () => {
    expect(principalQualifier(finance)).toBe("domain S-1-5-21-1-2-3");
  });
});

describe("notes beside a name", () => {
  it("says an unresolved SID is unresolved, with the reason when there is one", () => {
    expect(principalNotes(principal({ resolved: false, unresolved_reason: "not_collected" }))).toEqual(
      ["unresolved (not collected)"],
    );
  });

  it("keeps a disabled account visible rather than hiding it", () => {
    // A disabled account is still on the ACL. The state is why the finding is not the risk
    // it looks like, so it belongs beside the name and not instead of it.
    expect(principalNotes(principal({ enabled: false }))).toContain("disabled");
  });

  it("says nothing when there is nothing to say", () => {
    expect(principalNotes(principal({ enabled: true }))).toEqual([]);
  });
});

describe("the link to a principal", () => {
  it("carries the storage key, encoded", () => {
    expect(principalHref(principal({ key: "fs01|S-1-5-32-544" }))).toBe(
      "/identities/principal/fs01%7CS-1-5-32-544",
    );
  });
});

describe("naming a principal", () => {
  it("prefers the display name, then the account, the UPN, the last known name, the SID", () => {
    const sid = "S-1-5-21-1-2-3-1104";
    expect(principalLabel(principal({ sid, display_name: "Ada", sam_account_name: "ada" }))).toBe("Ada");
    expect(principalLabel(principal({ sid, sam_account_name: "ada", user_principal_name: "ada@corp" }))).toBe("ada");
    expect(principalLabel(principal({ sid, user_principal_name: "ada@corp", last_known_name: "OLD\\ada" }))).toBe(
      "ada@corp",
    );
    expect(principalLabel(principal({ sid, last_known_name: "OLD\\ada" }))).toBe("OLD\\ada");
    expect(principalLabel(principal({ sid }))).toBe(sid);
  });
});

describe("a shortest chain", () => {
  const ada = principal({ key: "S-1-5-21-1-2-3-1104", display_name: "Ada" });
  const finance = principal({ key: "S-1-5-21-1-2-3-1200", display_name: "Finance" });

  it("uses the API's labels, in the path's order", () => {
    expect(chainPrincipals([ada.key, finance.key], [ada, finance]).map(principalLabel)).toEqual([
      "Ada",
      "Finance",
    ]);
  });

  it("falls back to unresolved keys when the labels do not match the path", () => {
    const chain = chainPrincipals(["fs01|S-1-5-32-544", finance.key], undefined);
    expect(chain.map((hop) => [hop.key, hop.sid, hop.host_key, hop.resolved])).toEqual([
      ["fs01|S-1-5-32-544", "S-1-5-32-544", "fs01", false],
      [finance.key, finance.key, null, false],
    ]);
    expect(chain.map(principalLabel)).toEqual(["S-1-5-32-544", finance.key]);
  });
});

describe("a principal's relationship views", () => {
  it("offers a group its members first", () => {
    expect(principalViews(principal({ is_group: true })).map((view) => view.label)).toEqual([
      "Direct members",
      "Effective members",
      "Parent groups",
      "Effective groups",
      "Reachable shares",
      "Reachable directories",
      "Named on share ACLs",
    ]);
  });

  it("offers an account no member views", () => {
    const views = principalViews(principal({ is_group: false })).map((view) => view.id);
    expect(views[0]).toBe("groups");
    expect(views).not.toContain("members");
    expect(views).not.toContain("effective-members");
  });

  it("offers a principal of unknown kind everything", () => {
    expect(principalViews(principal({ is_group: null }))).toHaveLength(7);
  });

  it("addresses each view under the key", () => {
    expect(principalHrefForKey("fs01|S-1-5-32-544", "effective-members")).toBe(
      "/identities/principal/fs01%7CS-1-5-32-544/effective-members",
    );
  });
});

describe("whether an account was deleted", () => {
  it("reads is_deleted on a directory principal", () => {
    expect(deletedState(principalDetail({ kind: "user", is_deleted: true }))).toBe("yes");
    expect(deletedState(principalDetail({ kind: "user", is_deleted: false }))).toBe("no");
  });

  it("reads an unresolved record's reason, not its is_deleted column default", () => {
    // The banner says "(deleted)" from the reason; the facts must not answer "no" beside it.
    const recorded = { kind: "unresolved", is_deleted: false } as const;
    expect(deletedState(principalDetail({ ...recorded, unresolved_reason: "deleted" }))).toBe("reported");
    expect(deletedState(principalDetail({ ...recorded, unresolved_reason: "lookup_failed" }))).toBe("unknown");
    expect(deletedState(principalDetail({ resolved: false, kind: null, unresolved_reason: null }))).toBe("unknown");
  });
});
