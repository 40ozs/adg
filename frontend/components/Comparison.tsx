import type { JSX } from "react";

import type { ChangeComparisonResponse } from "@/lib/contracts";
import {
  comparisonDiagnostics,
  comparisonOutcome,
  kindWords,
  outcomeNotice,
  outcomeWords,
  recoveryLabel,
} from "@/lib/coverage";
import { ComparisonLink } from "@/components/ComparisonLink";
import { StateBadge } from "@/components/Coverage";

/**
 * How a point-in-time comparison ended, and what to do when it did not complete (ADG-DEMO-03).
 *
 * The outcome is the API's. This panel states it in words and a symbol, announces it to
 * assistive technology (`role="status"`), and offers the recovery actions the API listed --
 * each one a real request with its own parameters, never a relabelling of the answer given.
 */
export function ComparisonOutcomePanel({
  comparison,
  hrefFor,
}: {
  comparison: ChangeComparisonResponse;
  hrefFor: (parameters: Record<string, string>) => string;
}): JSX.Element {
  const outcome = comparisonOutcome(comparison);
  const notice = outcomeNotice(comparison);
  const banner = notice.tone === "ok" ? "banner-info" : "banner-warning";
  const diagnostics = comparisonDiagnostics(comparison);
  const recovery = comparison.recovery ?? [];
  return (
    <section className="card" id="comparison-outcome" aria-labelledby="comparison-outcome-heading">
      <h2 id="comparison-outcome-heading">How this comparison ended</h2>
      <div className={`banner ${banner}`} role="status" aria-live="polite">
        <h3>
          <StateBadge words={outcomeWords(outcome)} /> {notice.headline}
        </h3>
        <p>{notice.explanation}</p>
      </div>
      {recovery.length > 0 && (
        <nav aria-label="Recovery actions">
          <h3>Get a complete comparison</h3>
          <ul>
            {recovery.map((action) => (
              <li key={`${action.action}-${action.parameters.kind ?? ""}`}>
                <ComparisonLink href={hrefFor(action.parameters)}>{recoveryLabel(action)}</ComparisonLink>
              </li>
            ))}
          </ul>
          <p className="muted">
            Each runs a new comparison and names this one as the attempt it recovers from. Or
            narrow the scope below to one server, share, folder, person or group.
          </p>
        </nav>
      )}
      {diagnostics.length > 0 && (
        <details>
          <summary>Diagnostics</summary>
          <ul>
            {diagnostics.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

const KINDS = [
  "principal",
  "membership_edge",
  "server",
  "smb_share",
  "smb_ace",
  "ntfs_resource",
  "ntfs_ace",
] as const;

/** Narrow a comparison: the same two instants, a smaller scope. A plain GET form. */
export function NarrowComparisonForm({
  action,
  from,
  to,
  retryOf,
  current,
}: {
  action: string;
  from: string;
  to: string;
  retryOf: string | null;
  current: { scope: string; key: string; kind: string };
}): JSX.Element {
  return (
    <form method="get" action={action} className="card" aria-labelledby="narrow-heading">
      <h2 id="narrow-heading">Narrow the comparison</h2>
      <input type="hidden" name="from" value={from} />
      <input type="hidden" name="to" value={to} />
      {retryOf !== null && <input type="hidden" name="retry_of" value={retryOf} />}
      <p>
        <label htmlFor="narrow-scope">Scope </label>
        <select id="narrow-scope" name="scope" defaultValue={current.scope}>
          <option value="">The whole estate</option>
          <option value="server">One server</option>
          <option value="share">One share (server|share)</option>
          <option value="directory">One folder (UNC path)</option>
          <option value="principal">One person or group, as a trustee or member (SID)</option>
          <option value="group">One group&apos;s membership (SID)</option>
        </select>{" "}
        <label htmlFor="narrow-key">named </label>
        <input id="narrow-key" name="key" defaultValue={current.key} size={32} />
      </p>
      <p>
        <label htmlFor="narrow-kind">Objects </label>
        <select id="narrow-kind" name="kind" defaultValue={current.kind}>
          <option value="">Every kind</option>
          {KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {kindWords(kind)}
            </option>
          ))}
        </select>
      </p>
      <p>
        <button type="submit">Compare</button>
      </p>
    </form>
  );
}
