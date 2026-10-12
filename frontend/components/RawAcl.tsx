import type { JSX } from "react";

import { aceNotes, aceOrigin, formatMask } from "@/lib/acl";
import type { NtfsAceView, ShareAceView } from "@/lib/contracts";
import { ambiguousLabels, principalLabel } from "@/lib/identity";
import { PrincipalName } from "@/components/Identity";
import { SimulateAction } from "@/components/SimulateLink";
import { SortableTable } from "@/components/SortableTable";

/** Sorting reorders the display; the evaluation order stays the # column. */
const ORDER_CAPTION = "Sorting orders the rows on this page. # is the order Windows evaluates them in.";

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
export function ShareAceTable({
  entries,
  shareKey,
}: {
  entries: readonly ShareAceView[];
  /**
   * The share these entries belong to. Given, each row offers a `Simulate` action that opens
   * the proposal editor with a removal of that entry already written; omitted, the column is
   * not rendered at all. Optional because two callers list entries from several shares at
   * once, and a row that could not say which share it came from must not offer to change one.
   */
  shareKey?: string;
}): JSX.Element {
  const ambiguous = ambiguousLabels(entries.map((entry) => entry.trustee));
  return (
    <SortableTable
      className="acl-table"
      caption={ORDER_CAPTION}
      rowHeader={2}
      columns={[
        { label: "#", type: "number" },
        { label: "Type" },
        { label: "Trustee" },
        { label: "Right" },
        { label: "Last observed", type: "date", className: "muted" },
        ...(shareKey ? [{ label: "What if", sortable: false }] : []),
      ]}
      rows={entries.map((entry) => ({
        key: entry.ace_key,
        className: entry.ace_type === "deny" ? "ace-deny" : undefined,
        sort: [
          entry.order_index,
          entry.ace_type,
          principalLabel(entry.trustee),
          entry.right,
          entry.provenance.last_observed_at,
        ],
        cells: [
          entry.order_index === null ? <span className="muted">unordered</span> : entry.order_index,
          <TypeBadge key="type" type={entry.ace_type} />,
          <PrincipalName key="trustee" principal={entry.trustee} ambiguous={ambiguous} />,
          <>
            <code>{entry.right}</code>
            {entry.permission && entry.access_mask !== null && (
              <span className="muted"> (level and mask both reported)</span>
            )}
          </>,
          <time key="seen" dateTime={entry.provenance.last_observed_at}>
            {entry.provenance.last_observed_at}
          </time>,
          ...(shareKey
            ? [
                <SimulateAction key="simulate"
                  seed={{ kind: "remove_share_ace", share_key: shareKey, ace_key: entry.ace_key }}
                  title="Measure what removing this entry would do. Nothing is applied."
                />,
              ]
            : []),
        ],
      }))}
    />
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
export function NtfsAceTable({
  entries,
  resourceKey,
}: {
  entries: readonly NtfsAceView[];
  /** The directory these entries belong to; see the note on {@link ShareAceTable}. */
  resourceKey?: string;
}): JSX.Element {
  const ambiguous = ambiguousLabels(entries.map((entry) => entry.trustee));
  return (
    <SortableTable
      className="acl-table"
      caption={ORDER_CAPTION}
      rowHeader={2}
      columns={[
        { label: "#", type: "number" },
        { label: "Type" },
        { label: "Trustee" },
        { label: "Rights" },
        { label: "Mask" },
        { label: "Source" },
        { label: "Applies here" },
        ...(resourceKey ? [{ label: "What if", sortable: false }] : []),
      ]}
      rows={entries.map((entry) => {
        const notes = aceNotes(entry);
        return {
          key: entry.ace_key,
          className: entry.ace_type === "deny" ? "ace-deny" : undefined,
          sort: [
            entry.order_index,
            entry.ace_type,
            principalLabel(entry.trustee),
            entry.rights.join(", ") || null,
            entry.access_mask,
            aceOrigin(entry),
            entry.applies_to_this_object,
          ],
          cells: [
            entry.order_index === null ? <span className="muted">unordered</span> : entry.order_index,
            <TypeBadge key="type" type={entry.ace_type} />,
            <PrincipalName key="trustee" principal={entry.trustee} ambiguous={ambiguous} />,
            <>
              {entry.rights.length === 0 ? (
                <span className="muted">no named right in this mask</span>
              ) : (
                entry.rights.join(", ")
              )}
              {notes.length > 0 && <div className="ace-notes">{notes.join(" · ")}</div>}
            </>,
            <code key="mask">{formatMask(entry.access_mask)}</code>,
            <span key="source"
              className={
                aceOrigin(entry) === "inherited" ? "badge badge-inherited" : "badge badge-explicit"
              }
            >
              {aceOrigin(entry)}
            </span>,
            entry.applies_to_this_object ? (
              "yes"
            ) : (
              <span className="status-warn">no — inherit-only</span>
            ),
            ...(resourceKey
              ? [
                  <SimulateAction key="simulate"
                    seed={{ kind: "remove_ntfs_ace", resource_key: resourceKey, ace_key: entry.ace_key }}
                    title="Measure what removing this entry would do. Nothing is applied."
                  />,
                ]
              : []),
          ],
        };
      })}
    />
  );
}

/** Allow or deny, as a badge. */
function TypeBadge({ type }: { type: string }): JSX.Element {
  return <span className={type === "deny" ? "badge badge-deny" : "badge badge-allow"}>{type}</span>;
}
