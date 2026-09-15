"use client";

import { useState, type JSX } from "react";

import type { SimulationReportView } from "@/lib/contracts";
import type { StoredReportDocument } from "@/lib/simulation";
import { summaryLine } from "@/lib/simulation";
import { SimulationReport } from "@/components/Simulation";
import { SimulationExport } from "@/components/SimulationExport";
import styles from "@/components/simulation.module.css";

/**
 * The stored result, shown as what it is.
 *
 * A stored result names principals by storage key, because the names were never part of the
 * answer: what is persisted is the engine's compact document, and a stored copy of the
 * rendered version would be a second account of one answer ageing independently of the code
 * that computes it. The page says so rather than rendering keys and letting a reader wonder
 * whether ADG has lost the names.
 *
 * Every field that could be missing is rendered as missing. "This result recorded no count"
 * and "this result counted nothing" are different statements, and inventing the second is how
 * an old document read by a newer build turns into a clean bill of health.
 */
export function StoredResultPanel({
  report,
  computedAt,
}: {
  report: StoredReportDocument;
  computedAt: string;
}): JSX.Element {
  const summary = report.summary;
  return (
    <div className="card">
      <h2>What it was found to do</h2>
      <p className="muted">Measured {computedAt}.</p>

      {report.inert && (
        <p className="verdict verdict-info">
          Nothing in this proposal applied. Every change was already in place or named
          something that is no longer there — which is not the same as a change that is safe.
        </p>
      )}

      {summary ? (
        <>
          <p className={summary.evaluated === summary.unchanged ? "verdict verdict-ok" : "verdict verdict-warn"}>
            {summaryLine(summary)}
          </p>
          <ul>
            <li>{summary.principals_losing.length} principals lose access or rights</li>
            <li>{summary.principals_gaining.length} principals gain access or rights</li>
            <li>{summary.resources_affected.length} directories affected</li>
            <li>{summary.alternate_paths_retained} alternate routes survive the change</li>
          </ul>
        </>
      ) : (
        <p className="status-warn">
          This result recorded no summary. It was written by a different build of ADG; re-run
          the proposal to get a result this screen can read.
        </p>
      )}

      {report.complete === false && (
        <div className="banner banner-warning" role="alert">
          <h2>This result is part of the answer, not the whole of it</h2>
          <ul>
            {(report.truncation ?? []).map((code) => (
              <li key={code}>
                <code>{code}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.deltas && report.deltas.length > 0 && (
        <div className="table-scroll">
          <table className="access-table">
            <thead>
              <tr>
                <th scope="col">Principal</th>
                <th scope="col">Directory</th>
                <th scope="col">Today</th>
                <th scope="col">Would be</th>
                <th scope="col">Change</th>
              </tr>
            </thead>
            <tbody>
              {report.deltas.map((delta) => (
                <tr key={`${delta.subject_key}|${delta.resource_key}|${delta.path}`}>
                  <td>
                    <code>{delta.subject_key}</code>
                  </td>
                  <td>
                    <code>{delta.resource_key}</code>
                  </td>
                  <td>
                    <code>{delta.rights_before}</code>
                  </td>
                  <td>
                    <code>{delta.rights_after}</code>
                  </td>
                  <td>
                    {delta.direction}
                    {delta.caveats.map((code) => (
                      <p key={code} className="status-warn" role="note">
                        {code}
                      </p>
                    ))}
                    {delta.retained_path_count > 0 && (
                      <p className="muted">
                        {delta.retained_path_count} route
                        {delta.retained_path_count === 1 ? "" : "s"} survive this change
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="muted">
        Principals are named by storage key here: a stored result holds the engine&rsquo;s own
        compact document, and the display names were never part of the answer. Run the proposal
        again for the resolved, explained version.
      </p>
    </div>
  );
}

/**
 * Running a stored proposal again.
 *
 * The answer to a stale baseline, and the reason staleness is a banner rather than a refusal:
 * the proposal is still the proposal, and re-measuring it is one button. The result is
 * **added** to the proposal's history rather than replacing the last one, because "this change
 * was safe on Monday and takes access away today" is the sentence that history exists to make
 * available.
 *
 * The button says what it will not do, beside it, at the same size.
 */
export function SimulationRerun({
  simulationId,
  name,
  stale,
}: {
  simulationId: string;
  name: string;
  stale: boolean;
}): JSX.Element {
  const [report, setReport] = useState<SimulationReportView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/simulations/${encodeURIComponent(simulationId)}/evaluations`, {
        method: "POST",
      });
      const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : `The API refused the request (HTTP ${response.status}).`,
        );
      }
      setReport(payload as unknown as SimulationReportView);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <h2>Run this again</h2>
        <p className="muted">
          {stale
            ? "The estate has moved since this proposal was measured. Running it again answers against what is there now."
            : "Measures the proposal against the estate as it stands now, and keeps the result beside the others."}
        </p>
        <div className={styles.actions}>
          <button type="button" className="button" disabled={busy} onClick={() => void run()}>
            {busy ? "Evaluating…" : "Run this proposal again"}
          </button>
          <span className="muted">
            Reads ADG&rsquo;s collected facts and records an answer. Changes no permission.
          </span>
        </div>
        {error && (
          <p className="status-bad" role="alert">
            {error}
          </p>
        )}
      </div>

      {report && (
        <>
          <SimulationReport report={report} heading="What it would do, measured just now" />
          <SimulationExport report={report} name={name} />
        </>
      )}
    </>
  );
}
