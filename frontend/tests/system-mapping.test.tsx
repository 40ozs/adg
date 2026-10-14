// @vitest-environment jsdom
/**
 * ADG-DEMO-01 in the browser: a SYSTEM explanation never reads as two different answers.
 *
 * The engine reports which identity a SYSTEM subject stands for as one `system_mapping_*`
 * finding. These tests hold the screen and the text export to it:
 *
 * - only a `confirmed` mapping is ever worded as a resolved identity;
 * - an unconfirmed one never words its answer as a gap in collection, nor as "no access";
 * - `no_grant` where one layer granted never says that nothing named the principal;
 * - a stored-but-unresolved SID is never rendered as resolved.
 *
 * All identifiers are the synthetic ones from `explanation-factories.ts`.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VerdictPanel } from "@/components/Explanation";
import { PrincipalName } from "@/components/Identity";
import {
  cautions,
  explanationText,
  explanationWording,
  outcomeWording,
  systemMapping,
} from "@/lib/explanation";
import { isUnresolved, principalNotes } from "@/lib/identity";
import {
  ALICE_SID,
  explanation,
  finding,
  noGrant,
  noGrantBetweenLayers,
  principal,
  systemLocally,
  systemMappingFinding,
  systemOverShare,
} from "./explanation-factories";

/** The fixed sentences the defective build printed beside evidence that contradicted them. */
const NOTHING_REACHES = /Nothing on either ACL reaches this principal/;
const COLLECTION_GAP = /collection is incomplete/;

describe("reading the mapping the engine reported", () => {
  it.each(["confirmed", "ambiguous", "missing", "conflicting"] as const)(
    "reads the %s state from its finding",
    (state) => {
      const data = { ...explanation(), warnings: [systemMappingFinding(state)] };

      expect(systemMapping(data)?.state).toBe(state);
    },
  );

  it("is null for any other subject", () => {
    expect(systemMapping(explanation())).toBeNull();
  });

  it("does not guess at a state it does not know", () => {
    const data = {
      ...explanation(),
      warnings: [finding({ condition: "system_mapping_renamed_tomorrow" })],
    };

    expect(systemMapping(data)).toBeNull();
  });

  it("carries the absence of a description as null, not as a kind", () => {
    const data = { ...explanation(), warnings: [systemMappingFinding("missing", null)] };

    expect(systemMapping(data)?.describedAs).toBeNull();
  });
});

describe("the wording agrees with the evidence", () => {
  it("words SYSTEM over a share as ambiguous, not as a gap in collection", () => {
    const wording = explanationWording(systemOverShare());

    expect(wording.word).toBe("Unknown");
    expect(wording.conclusive).toBe(false);
    expect(wording.guidance).toContain("ambiguous");
    expect(wording.guidance).toContain("not a finding of no access");
    expect(wording.guidance).toContain("computer account");
    expect(wording.guidance).not.toMatch(COLLECTION_GAP);
  });

  it.each(["missing", "conflicting"] as const)(
    "never words a %s mapping as a resolved identity, even when rights survive",
    (state) => {
      const data = { ...systemLocally(), warnings: [systemMappingFinding(state)] };
      const wording = explanationWording(data);

      expect(wording.word).toBe("Access");
      expect(wording.guidance).toContain(`here is ${state}`);
      expect(wording.guidance).toContain("cannot be attributed to one account");
    },
  );

  it("leaves a confirmed mapping's granted wording exactly as the outcome's", () => {
    const data = systemLocally();

    expect(explanationWording(data)).toEqual(outcomeWording(data.verdict));
  });

  it("never words an empty confirmed answer as a gap in collection, nor as a no", () => {
    const data = { ...systemOverShare(), access_path: "local", warnings: [systemMappingFinding("confirmed")] };
    const wording = explanationWording(data);
    const note = cautions(data).find((entry) => entry.id === "inconclusive");

    expect(wording.word).toBe("Unknown");
    expect(wording.guidance).toContain("LocalSystem");
    expect(wording.guidance).toContain("not a finding of no access");
    expect(wording.guidance).not.toMatch(COLLECTION_GAP);
    expect(note?.detail).toContain("LocalSystem");
    expect(note?.detail).not.toMatch(COLLECTION_GAP);
  });

  it("does not say nothing reached the principal when the NTFS ACL granted it", () => {
    const wording = explanationWording(noGrantBetweenLayers());

    expect(wording.word).toBe("No access");
    expect(wording.guidance).not.toMatch(NOTHING_REACHES);
    expect(wording.guidance).toContain("The NTFS ACL grants this principal rights");
    expect(wording.guidance).toContain("share ACL gives it none");
  });

  it("keeps the plain no-grant wording when nothing named the principal at all", () => {
    expect(explanationWording(noGrant()).guidance).toMatch(NOTHING_REACHES);
  });

  it("explains an inconclusive SYSTEM answer by its mapping, not by collection", () => {
    const note = cautions(systemOverShare()).find((entry) => entry.id === "inconclusive");

    expect(note?.detail).toContain("ambiguous");
    expect(note?.detail).not.toMatch(COLLECTION_GAP);
  });
});

describe("the text export says what the screen says", () => {
  it("names the mapping, and does not print 'No access' for an unknown answer", () => {
    const text = explanationText(systemOverShare());

    expect(text).toContain("SYSTEM mapping: ambiguous");
    expect(text).toContain("Effective rights: none established");
    expect(text).not.toContain("Effective rights: No access");
    expect(text).toContain("no network session carries SYSTEM's own SID");
  });

  it("carries the guidance that agrees with the layers", () => {
    const text = explanationText(noGrantBetweenLayers());

    expect(text).not.toMatch(NOTHING_REACHES);
    expect(text).toContain("widening the share ACL would make the grant real");
  });
});

describe("the verdict panel", () => {
  it("shows the ambiguous mapping beside an answer that withholds a conclusion", () => {
    render(<VerdictPanel explanation={systemOverShare()} />);

    expect(screen.getByText("Unknown", { selector: ".verdict" })).toBeInTheDocument();
    expect(screen.getByText("SYSTEM mapping")).toBeInTheDocument();
    expect(screen.getByText("ambiguous", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText("None established")).toBeInTheDocument();
    expect(screen.queryByText(NOTHING_REACHES)).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Not a finding of no access");
  });

  it("states a confirmed mapping", () => {
    render(<VerdictPanel explanation={systemLocally()} />);

    expect(screen.getByText("confirmed", { selector: "code" })).toBeInTheDocument();
  });

  it("shows no mapping row for any other subject", () => {
    render(<VerdictPanel explanation={explanation()} />);

    expect(screen.queryByText("SYSTEM mapping")).toBeNull();
  });

  it("never prints the no-entry sentence beside a layer that granted", () => {
    render(<VerdictPanel explanation={noGrantBetweenLayers()} />);

    expect(screen.queryByText(NOTHING_REACHES)).toBeNull();
    expect(screen.getByText(/The NTFS ACL grants this principal rights/)).toBeInTheDocument();
  });
});

describe("a SID a run saw and could not resolve", () => {
  const unresolvedRow = principal({
    key: "S-1-5-18",
    sid: "S-1-5-18",
    kind: "unresolved",
    resolved: true,
    display_name: null,
    unresolved_reason: "lookup_failed",
    last_known_name: "SYSTEM",
  });

  it("is unresolved although ADG holds a record for it", () => {
    expect(isUnresolved(unresolvedRow)).toBe(true);
    expect(isUnresolved(principal({ key: ALICE_SID }))).toBe(false);
    expect(isUnresolved(principal({ resolved: false }))).toBe(true);
  });

  it("says so beside its name, even when an earlier name is shown", () => {
    expect(principalNotes(unresolvedRow)).toEqual(["unresolved (lookup failed)"]);

    render(<PrincipalName principal={unresolvedRow} link={false} />);

    expect(screen.getByText("SYSTEM")).toBeInTheDocument();
    expect(screen.getByText("unresolved (lookup failed)")).toHaveClass("status-warn");
  });
});
