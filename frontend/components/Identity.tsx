import type { JSX } from "react";
import { Fragment } from "react";
import Link from "next/link";

import type { PrincipalDetail, PrincipalSummary } from "@/lib/contracts";
import { Sid } from "@/components/Facts";
import {
  isAmbiguous,
  isUnresolved,
  principalHref,
  principalKindLabel,
  principalLabel,
  principalNotes,
  principalQualifier,
} from "@/lib/identity";

type AnyPrincipal = PrincipalSummary | PrincipalDetail;

/**
 * How a principal appears everywhere in this application: by its name, on one line.
 *
 * The SID is not printed under the name. It stays behind it — the link opens the principal
 * by its key and carries that key as its `title`, and the principal's Identity information
 * shows SID and key in full — and a table that needs it on screen gives it a column of its
 * own (`Identifier`). One exception: a principal nothing has resolved (`isUnresolved`) shown
 * under a last known name. That name is a hint an ACE carried, not an identity; the SID is
 * the identity, so it stays on screen above the warning (ADG-DEMO-01), on one line like any SID.
 *
 * Two "Administrators" rows in one table are the normal case in a Windows estate, not an
 * error: `fs01|S-1-5-32-544` and `fs02|S-1-5-32-544` are two different groups on two
 * different servers. When the same label appears more than once in the list being rendered,
 * the qualifier — the host for a host-scoped principal, the domain SID otherwise — is
 * promoted next to the name. `ambiguous` is computed once per list by the caller
 * (`ambiguousLabels`), because whether a name is ambiguous is a property of the list, not
 * of the principal.
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
  // An undescribed trustee can have no name but its SID, held to one line and keeping its end.
  const labelClass = label === principal.sid ? "identifier sid" : undefined;
  const unresolved = isUnresolved(principal);

  return (
    <span className="principal">
      <span className="principal-name">
        {/* No prefetch: a page of principals would otherwise ask for every one of them. */}
        {link ? (
          <Link
            href={principalHref(principal)}
            prefetch={false}
            title={principal.key}
            className={labelClass}
          >
            {label}
          </Link>
        ) : (
          label
        )}
        {needsQualifier && qualifier && (
          <span className="principal-qualifier"> ({qualifier})</span>
        )}
      </span>
      {unresolved && label !== principal.sid && <Sid value={principal.sid} />}
      {notes.length > 0 && (
        <span className={unresolved ? "principal-notes status-warn" : "principal-notes"}>
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

/**
 * A membership chain, as names that open the principal they name.
 *
 * Each link is built from the node's storage key, never from its name: two principals can
 * share a name, and only the key opens the right one; the key is the link's `title`, never
 * text in the chain. A node nothing has said what it is — no record, or a record of kind
 * `unresolved` (`isUnresolved`, ADG-DEMO-01) — stays text: its SID, or the name an ACE last
 * carried, never a guessed name and never a link presenting it as a resolved principal.
 * Prefetching is off: a page of chains is hundreds of links, and prefetching each one would
 * ask the server for hundreds of pages nobody opened.
 */
export function PrincipalChain({
  principals,
}: {
  principals: readonly PrincipalSummary[];
}): JSX.Element {
  return (
    <span className="principal-chain">
      {principals.map((principal, index) => (
        <Fragment key={`${index}:${principal.key}`}>
          {index > 0 && " → "}
          {!isUnresolved(principal) ? (
            <Link href={principalHref(principal)} prefetch={false} title={principal.key}>
              {principalLabel(principal)}
            </Link>
          ) : (
            <code
              className={principalLabel(principal) === principal.sid ? "identifier sid" : "identifier"}
              title={
                principal.resolved
                  ? `${principal.key}: a run recorded this SID as unresolved`
                  : `${principal.key}: no run has described this SID`
              }
            >
              {principalLabel(principal)}
            </code>
          )}
        </Fragment>
      ))}
    </span>
  );
}
