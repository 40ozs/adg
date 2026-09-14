import type { JSX } from "react";
import Link from "next/link";

import { accessVerdict, limitingLayerLabel, rightsNotes } from "@/lib/access";
import type {
  AccessCertainty,
  EnumerationView,
  FindingView,
  PrincipalAccessView,
  ResourceAccessView,
  RightsView,
  TokenView,
} from "@/lib/contracts";
import { explainHref } from "@/lib/explanation";
import { ambiguousLabels } from "@/lib/identity";
import { directoryHref, shareHref } from "@/lib/resources";
import { PrincipalName } from "@/components/Identity";

/**
 * The verdict cell.
 *
 * The certainty is never dropped and never abbreviated into the word alone: "None
 * established" and "No" are two different answers and are written as two different
 * answers.
 */
export function Verdict({
  access,
  certainty,
}: {
  access: boolean;
  certainty: AccessCertainty;
}): JSX.Element {
  const verdict = accessVerdict(access, certainty);
  return (
    <>
      <span className={`verdict verdict-${verdict.tone}`}>{verdict.word}</span>
      <span className="verdict-note">{verdict.explanation}</span>
    </>
  );
}

/** A rights mask: the label for reading, the mask for comparing, the notes for both. */
export function Rights({ rights }: { rights: RightsView }): JSX.Element {
  const notes = rightsNotes(rights);
  return (
    <>
      <span>{rights.label}</span> <code>{rights.mask}</code>
      {notes.length > 0 && <div className="ace-notes status-warn">{notes.join(" · ")}</div>}
    </>
  );
}

/**
 * Directories or shares one principal can reach.
 *
 * Rows that grant nothing are here on purpose: the API returns every candidate the ACLs
 * named, and "on the ACL and holding no access" is the distinction the engine exists to
 * draw. Filtering them out in the browser would also make the pager a claim about a
 * different set than the one being paged.
 */
export function ResourceAccessTable({
  items,
  subject,
  explainFor,
}: {
  items: readonly ResourceAccessView[];
  /** "share" or "directory" — what each row names. */
  subject: "share" | "directory";
  /**
   * The principal these rows are about, as its storage key. Given, every row carries a link
   * to the full derivation for that pair.
   *
   * Optional so that a caller which does not know whose answer it is showing cannot produce
   * a link to the wrong pair — an explanation of the wrong principal is worse than no link.
   */
  explainFor?: string;
}): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="access-table">
        <thead>
          <tr>
            <th scope="col">{subject === "share" ? "Share" : "Directory"}</th>
            <th scope="col">Access</th>
            <th scope="col">Rights</th>
            <th scope="col">Narrower layer</th>
            {explainFor !== undefined && <th scope="col">Why</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.resource.key}|${item.share?.key ?? ""}`}>
              <th scope="row">
                {subject === "share" && item.share ? (
                  <Link href={shareHref(item.share.key)}>{item.share.name ?? item.share.key}</Link>
                ) : (
                  <Link href={directoryHref(item.resource.key)}>
                    {item.resource.path ?? item.resource.key}
                  </Link>
                )}
                {!item.resource.observed && (
                  <div className="ace-notes status-warn">
                    no run has read this directory&apos;s descriptor
                  </div>
                )}
                {item.conditions.length > 0 && (
                  <div className="ace-notes">{item.conditions.join(" · ")}</div>
                )}
              </th>
              <td>
                <Verdict access={item.access} certainty={item.certainty} />
              </td>
              <td>
                <Rights rights={item.rights} />
              </td>
              <td className="muted">{limitingLayerLabel(item.limiting_layer)}</td>
              {explainFor !== undefined && (
                <td>
                  <Link href={explainHref(explainFor, item.resource.key)}>Explain</Link>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every principal the engine can show holding rights on one directory.
 *
 * The `via` column is the point of the table: a principal is usually here because of a
 * group, and the chain that put it here is the thing an administrator removes. A principal
 * the ACL names directly has an empty chain, and that says "directly" rather than nothing.
 */
export function PrincipalAccessTable({
  items,
  explainOn,
}: {
  items: readonly PrincipalAccessView[];
  /**
   * The directory these rows are about, as its canonical UNC path. Given, every row carries
   * a link to the full derivation for that pair — the `via` column says *which* groups, and
   * the derivation says which entry, on which ACL, and what removing it would do.
   *
   * The share page passes the directory it publishes, not the share: an explanation is
   * always anchored on a directory, and both layers are in the answer either way.
   */
  explainOn?: string;
}): JSX.Element {
  const ambiguous = ambiguousLabels(items.map((item) => item.principal));
  return (
    <div className="table-scroll">
      <table className="access-table">
        <thead>
          <tr>
            <th scope="col">Principal</th>
            <th scope="col">Access</th>
            <th scope="col">Rights</th>
            <th scope="col">Reaches it through</th>
            {explainOn !== undefined && <th scope="col">Why</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.principal.key}>
              <th scope="row">
                <PrincipalName principal={item.principal} ambiguous={ambiguous} />
              </th>
              <td>
                <Verdict access={item.access} certainty={item.certainty} />
                {item.conditions.length > 0 && (
                  <div className="ace-notes">{item.conditions.join(" · ")}</div>
                )}
              </td>
              <td>
                <Rights rights={item.rights} />
              </td>
              <td>
                {item.via.length === 0 ? (
                  <span className="muted">the ACL names this principal directly</span>
                ) : (
                  <ul className="via-list">
                    {item.via.map((entry) => (
                      <li key={entry.principal.key}>
                        <PrincipalName principal={entry.principal} />
                        <span className="muted">
                          {entry.path.length > 1 ? ` — ${entry.path.join(" → ")}` : ""}
                          {entry.assumed ? " (assumed, not observed)" : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </td>
              {explainOn !== undefined && (
                <td>
                  <Link href={explainHref(item.principal.key, explainOn)}>Explain</Link>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * How the subject's access token was built.
 *
 * `membership_complete: false` means the traversal hit a limit, so the token is a lower
 * bound and so is every answer computed from it. That has to be on the page with the
 * answers, not somewhere else.
 */
export function TokenSummary({ token }: { token: TokenView }): JSX.Element {
  return (
    <div className={token.membership_complete ? "banner banner-info" : "banner banner-warning"} role={token.membership_complete ? "status" : "alert"}>
      <h2>The token these answers were computed against</h2>
      <p>
        {token.entries.length} SID(s) in the token, for access over{" "}
        <code>{token.access_path}</code> with the <code>{token.assumption}</code> assumption.
      </p>
      {!token.membership_complete && (
        <p>
          The membership traversal hit a limit, so this token is a lower bound: the subject
          may belong to groups that are not in it, and every answer below may therefore be
          narrower than the truth.
        </p>
      )}
    </div>
  );
}

/** Whether the principal list is everyone, and which trustees could not be expanded. */
export function EnumerationNotice({
  enumeration,
}: {
  enumeration: EnumerationView;
}): JSX.Element | null {
  if (enumeration.complete && !enumeration.trustees_truncated) {
    return null;
  }
  return (
    <div className="banner banner-warning" role="alert">
      <h2>This is not everybody</h2>
      {enumeration.trustees_truncated && (
        <p>
          The ACL names more distinct trustees than the engine expands in one answer, so
          some were not evaluated at all.
        </p>
      )}
      {enumeration.unenumerable_trustees.length > 0 && (
        <p>
          Members could not be listed for:{" "}
          {enumeration.unenumerable_trustees
            .map((trustee) => trustee.display_name ?? trustee.sid)
            .join(", ")}
          . Each one means principals hold rights here that are not in the list below.
        </p>
      )}
      {enumeration.unenumerable_trustees.length === 0 && !enumeration.trustees_truncated && (
        <p>The engine could not establish that the list below is complete.</p>
      )}
    </div>
  );
}

/** Gaps in what was collected, each with the direction it could push an answer. */
export function FindingsNotice({ findings }: { findings: readonly FindingView[] }): JSX.Element | null {
  if (findings.length === 0) {
    return null;
  }
  return (
    <div className="banner banner-warning" role="alert">
      <h2>What could make these answers wrong</h2>
      <ul>
        {findings.map((finding) => (
          <li key={finding.condition}>
            {finding.message}{" "}
            <span className="muted">
              (
              {[
                finding.may_overstate ? "could overstate access" : null,
                finding.may_understate ? "could understate access" : null,
              ]
                .filter(Boolean)
                .join("; ") || "recorded for context"}
              )
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
