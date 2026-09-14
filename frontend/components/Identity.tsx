import type { JSX } from "react";
import Link from "next/link";

import type { PrincipalDetail, PrincipalSummary } from "@/lib/contracts";
import {
  isAmbiguous,
  principalHref,
  principalKindLabel,
  principalLabel,
  principalNotes,
  principalQualifier,
} from "@/lib/identity";

type AnyPrincipal = PrincipalSummary | PrincipalDetail;

/**
 * How a principal appears everywhere in this application.
 *
 * The SID is always on screen, under the name, because the name is the part that can be
 * wrong. Two "Administrators" rows in one table are the normal case in a Windows estate,
 * not an error, and the only thing that tells them apart is the key: `fs01|S-1-5-32-544`
 * and `fs02|S-1-5-32-544` are two different groups on two different servers.
 *
 * When the same label appears more than once in the list being rendered, the qualifier —
 * the host for a host-scoped principal, the domain SID otherwise — is promoted next to the
 * name rather than left to be worked out from the SID underneath. `ambiguous` is computed
 * once per list by the caller (`ambiguousLabels`), because whether a name is ambiguous is a
 * property of the list, not of the principal.
 */
export function PrincipalName({
  principal,
  ambiguous,
  link = true,
}: {
  principal: AnyPrincipal;
  /** Labels that occur more than once in this list. From `ambiguousLabels`. */
  ambiguous?: Set<string>;
  link?: boolean;
}): JSX.Element {
  const label = principalLabel(principal);
  const qualifier = principalQualifier(principal);
  const needsQualifier = ambiguous !== undefined && isAmbiguous(principal, ambiguous);
  const notes = principalNotes(principal);
  // An undescribed trustee has no name, so its label already *is* the key. Printing it
  // twice adds a line and no information.
  const keyIsTheName = label === principal.key;

  return (
    <span className="principal">
      <span className="principal-name">
        {link ? <Link href={principalHref(principal)}>{label}</Link> : label}
        {needsQualifier && qualifier && (
          <span className="principal-qualifier"> ({qualifier})</span>
        )}
      </span>
      {!keyIsTheName && <code className="principal-sid">{principal.key}</code>}
      {notes.length > 0 && (
        <span className={principal.resolved ? "principal-notes" : "principal-notes status-warn"}>
          {notes.join(" · ")}
        </span>
      )}
    </span>
  );
}

/**
 * A table cell for a principal, with the kind beside the name.
 *
 * `kind unknown` is shown as itself. A membership edge can reach a SID no run has
 * described, and calling that an account would be an invention this product cannot afford.
 */
export function PrincipalCell({
  principal,
  ambiguous,
}: {
  principal: AnyPrincipal;
  ambiguous?: Set<string>;
}): JSX.Element {
  return (
    <>
      <PrincipalName principal={principal} ambiguous={ambiguous} />
      <span className="principal-kind">{principalKindLabel(principal)}</span>
    </>
  );
}
