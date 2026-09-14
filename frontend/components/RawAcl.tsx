import type { JSX } from "react";

import { aceNotes, aceOrigin, formatMask } from "@/lib/acl";
import type { NtfsAceView, ShareAceView } from "@/lib/contracts";
import { ambiguousLabels } from "@/lib/identity";
import { PrincipalName } from "@/components/Identity";

/**
 * A share-level ACL, entry by entry.
 *
 * The right is rendered in whichever form the SMB server reported — a level (`change`) or
 * a mask (`0x001301bf`) — and never converted between them, because they are different
 * readings and converting one into the other would invent precision the source did not
 * have.
 *
 * Order is shown because order decides the answer: an Allow ahead of a Deny grants. Where
 * the source reported no position, the column says so rather than implying the list order
 * is the DACL order.
 */
export function ShareAceTable({ entries }: { entries: readonly ShareAceView[] }): JSX.Element {
  const ambiguous = ambiguousLabels(entries.map((entry) => entry.trustee));
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Type</th>
            <th scope="col">Trustee</th>
            <th scope="col">Right</th>
            <th scope="col">Last observed</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.ace_key} className={entry.ace_type === "deny" ? "ace-deny" : undefined}>
              <td>{entry.order_index === null ? <span className="muted">unordered</span> : entry.order_index}</td>
              <td>
                <span className={entry.ace_type === "deny" ? "badge badge-deny" : "badge badge-allow"}>
                  {entry.ace_type}
                </span>
              </td>
              <th scope="row">
                <PrincipalName principal={entry.trustee} ambiguous={ambiguous} />
              </th>
              <td>
                <code>{entry.right}</code>
                {entry.permission && entry.access_mask !== null && (
                  <span className="muted"> (level and mask both reported)</span>
                )}
              </td>
              <td className="muted">
                <time dateTime={entry.provenance.last_observed_at}>
                  {entry.provenance.last_observed_at}
                </time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * An NTFS DACL, entry by entry.
 *
 * Three columns exist because of specific ways this table gets misread:
 *
 * - **Source** (explicit / inherited) is where to make a change. A fix applied to an
 *   inherited entry on the wrong directory changes nothing.
 * - **Applies here** is false for an INHERIT_ONLY entry, which is on the list, names a
 *   trustee, carries a mask, and grants nothing on this directory.
 * - **Mask** is authoritative and the named rights are a rendering of it, so both are
 *   shown. `unrecognized_bits` is surfaced rather than dropped: a bit ADG cannot name is
 *   still a bit Windows will honour.
 */
export function NtfsAceTable({ entries }: { entries: readonly NtfsAceView[] }): JSX.Element {
  const ambiguous = ambiguousLabels(entries.map((entry) => entry.trustee));
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Type</th>
            <th scope="col">Trustee</th>
            <th scope="col">Rights</th>
            <th scope="col">Mask</th>
            <th scope="col">Source</th>
            <th scope="col">Applies here</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const notes = aceNotes(entry);
            return (
              <tr key={entry.ace_key} className={entry.ace_type === "deny" ? "ace-deny" : undefined}>
                <td>
                  {entry.order_index === null ? <span className="muted">unordered</span> : entry.order_index}
                </td>
                <td>
                  <span className={entry.ace_type === "deny" ? "badge badge-deny" : "badge badge-allow"}>
                    {entry.ace_type}
                  </span>
                </td>
                <th scope="row">
                  <PrincipalName principal={entry.trustee} ambiguous={ambiguous} />
                </th>
                <td>
                  {entry.rights.length === 0 ? (
                    <span className="muted">no named right in this mask</span>
                  ) : (
                    entry.rights.join(", ")
                  )}
                  {notes.length > 0 && <div className="ace-notes">{notes.join(" · ")}</div>}
                </td>
                <td>
                  <code>{formatMask(entry.access_mask)}</code>
                </td>
                <td>
                  <span
                    className={
                      aceOrigin(entry) === "inherited" ? "badge badge-inherited" : "badge badge-explicit"
                    }
                  >
                    {aceOrigin(entry)}
                  </span>
                </td>
                <td>
                  {entry.applies_to_this_object ? (
                    "yes"
                  ) : (
                    <span className="status-warn">no — inherit-only</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
