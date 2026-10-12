import type { JSX } from "react";

import { fetchCollectionOperations, fetchScanRuns } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { StateMessage } from "@/components/Banners";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { SortableTable, type SortableRow } from "@/components/SortableTable";
import { classify } from "@/lib/state";
import type { RunOutcome, ScopeOperations } from "@/lib/contracts";
import {
  completenessTone,
  countRows,
  coverageSentence,
  delivery,
  formatUtc,
  isAlarming,
  orderedErrors,
  scopeSentence,
  tallyScopes,
} from "@/lib/operations";

/**
 * Collectors — the operator page.
 *
 * Every empty table in ADG points here, and this page answers the question that makes an
 * empty table safe to read: *did anything actually look, and what could it not see?*
 *
 * Four panels, in the order an operator needs them:
 *
 * 1. **Coverage** — how many scopes are complete, out of how many.
 * 2. **Scopes** — one row per collector and target, each carrying the last success *and*
 *    the last failure. Those are two facts. A page showing only the latest run says
 *    "failed" and hides that yesterday's data is still on screen; one showing only the last
 *    success hides that it is stale. Both, always.
 * 3. **What is stored** — object counts. A collector that ran cleanly and wrote nothing
 *    reports `succeeded` everywhere else in the product; a count of zero is the only place
 *    it shows.
 * 4. **Errors** — grouped by code, widespread first. Twelve `access_denied` on one share is
 *    a permission on that share; twelve across twelve servers is the service account.
 *
 * The run history stays at the bottom: it is the audit trail, not the summary.
 *
 * Nothing here computes a verdict. `completeness`, `note`, `shortfall`, `stale_success` and
 * `health` all arrive from `/api/v1/collection/operations`; `lib/operations.ts` holds the
 * presentation decisions; this file renders.
 */
export default async function CollectorsPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const [operations, runs] = await Promise.all([
    fetchCollectionOperations(token),
    fetchScanRuns(token, { limit: 25 }),
  ]);

  if (!operations.ok) {
    return (
      <>
        <h1>Collectors</h1>
        <StateMessage
          state={classify(operations, { isEmpty: () => false, subject: "collector status" })}
        />
      </>
    );
  }

  const report = operations.data;
  // One instant for every row on the page. Two rows computed from two different "now"s can
  // disagree by a minute and read as a bug.
  const now = new Date();
  const tally = tallyScopes(report.scopes);

  const runState = classify(runs, {
    isEmpty: (data) => data.items.length === 0,
    // The coverage shape `classify` wants is the same `health` and `summary` this report
    // carries, so the empty state is worded from the same verdict rather than a second one.
    coverage: { health: report.health, summary: report.summary, concerns: [], collectors: [] },
    subject: "scan runs",
  });

  return (
    <>
      <h1>Collectors</h1>

      <div
        className={
          report.health === "failed"
            ? "banner banner-error"
            : isAlarming(report)
              ? "banner banner-warning"
              : "banner banner-info"
        }
        role={isAlarming(report) ? "alert" : "status"}
      >
        <h2>
          Collection: <span className="visually-hidden">status </span>
          {report.health.replace("_", " ")}
        </h2>
        <p>{coverageSentence(tally)}</p>
        <p className="muted">{report.summary}</p>
      </div>

      {report.notes.length > 0 && (
        <div className="card">
          <h2>What needs attention</h2>
          <ul>
            {report.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <h2>Collection scopes</h2>
      {report.scopes.length === 0 ? (
        <p className="muted">
          No collector has ever reported. Every list in ADG is empty because nothing ran, not
          because nothing is there.
        </p>
      ) : (
        <SortableTable
          caption={
            <>
              One row per collector and target. A collector kind that scans four servers
              appears four times, and a failure on one of them is not hidden by a success on
              another.
            </>
          }
          columns={[
            { label: "Scope" },
            { label: "Completeness" },
            { label: "Last successful scan", type: "date" },
            { label: "Last failed scan", type: "date" },
            { label: "Delivered", type: "number", className: "muted" },
          ]}
          rows={report.scopes.map((scope) => scopeRow(scope, now))}
        />
      )}

      <h2>What is stored</h2>
      <div className="table-scroll">
        <table>
          <caption>
            What the collectors actually wrote. A run that succeeded and stored nothing
            reports success everywhere else in ADG; this is where it shows.
          </caption>
          <thead>
            <tr>
              <th scope="col">Object</th>
              <th scope="col">Stored</th>
              <th scope="col">Note</th>
            </tr>
          </thead>
          <tbody>
            {countRows(report.counts).map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                <td className={row.concern ? "status-warn" : undefined}>{row.value}</td>
                <td className="muted">{row.concern ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Reported errors</h2>
      {report.errors.length === 0 ? (
        <p className="muted">
          No collector reported a failure. That is not the same as complete coverage — a
          scope that has never run reports nothing at all.
        </p>
      ) : (
        <SortableTable
          caption={
            <>
              {report.total_errors} reported failure(s), grouped by code. Widespread codes
              come first: the same failure on many targets is rarely a problem with one
              object.
            </>
          }
          columns={[
            { label: "Code" },
            { label: "Count", type: "number" },
            { label: "Collectors" },
            { label: "Most recent", type: "date" },
            { label: "Examples", className: "muted" },
          ]}
          rows={orderedErrors(report.errors).map((group) => ({
            key: group.code,
            sort: [
              group.code,
              group.count,
              group.collectors.join(", "),
              group.latest_occurred_at,
              group.sample_targets.join(", ") || null,
            ],
            cells: [
              <>
                <code>{group.code}</code>
                {group.is_widespread && <div className="badge badge-deny">widespread</div>}
              </>,
              group.count,
              group.collectors.join(", "),
              formatUtc(group.latest_occurred_at),
              group.sample_targets.length === 0 ? (
                "—"
              ) : (
                <ul key="examples" className="via-list">
                  {group.sample_targets.map((target) => (
                    <li key={target}>
                      <code>{target}</code>
                    </li>
                  ))}
                </ul>
              ),
            ],
          }))}
        />
      )}

      <h2>Run history</h2>
      <StateMessage state={runState} />

      {runState.kind === "ready" && (
        <SortableTable
          caption={
            <>
              {runState.data.page.total ?? runState.data.items.length} run(s), newest first.
              {runState.data.page.has_more && " More pages are available through the API."}{" "}
              Sorting orders the rows on this page.
            </>
          }
          rowHeader={null}
          columns={[
            { label: "Started", type: "date" },
            { label: "Collector" },
            { label: "Host" },
            { label: "Status" },
            { label: "Observations", type: "number" },
            { label: "Errors", type: "number" },
            { label: "Target", className: "muted" },
          ]}
          rows={runState.data.items.map((run) => ({
            key: run.run_id,
            sort: [
              run.started_at,
              run.collector,
              run.collector_host,
              run.status,
              run.observation_count_applied,
              run.error_count,
              run.target,
            ],
            cells: [
              formatUtc(run.started_at),
              run.collector,
              run.collector_host,
              <span key="status" className={statusClass(run.status)}>
                {run.status}
                {run.downgrade_reason && (
                  <>
                    {" "}
                    <span className="muted">({run.downgrade_reason})</span>
                  </>
                )}
              </span>,
              run.observation_count_applied,
              run.error_count,
              run.target ?? "—",
            ],
          }))}
        />
      )}
    </>
  );
}

function scopeRow(scope: ScopeOperations, now: Date): SortableRow {
  const tone = completenessTone(scope.completeness);
  return {
    key: `${scope.collector}|${scope.target ?? ""}`,
    sort: [
      scope.label,
      tone.label,
      ranAt(scope.last_success),
      ranAt(scope.last_failure),
      scope.latest.observations_applied,
    ],
    cells: [
      <>
        {scope.label}
        <div className="ace-notes muted">{scopeSentence(scope, now)}</div>
      </>,
      <>
        <span className={tone.className}>{tone.label}</span>
        {scope.latest.shortfall && (
          <div className="ace-notes status-warn">{scope.latest.shortfall}</div>
        )}
      </>,
      <>
        <RunCell run={scope.last_success} absent="never" />
        {scope.stale_success && (
          <div className="ace-notes status-warn">
            Not the latest attempt — the data on screen is from this run.
          </div>
        )}
      </>,
      <RunCell key="failure" run={scope.last_failure} absent="none" />,
      delivery(scope.latest),
    ],
  };
}

/** The instant a run cell shows, as its sort value. */
function ranAt(run: RunOutcome | null): string | null {
  return run === null ? null : (run.completed_at ?? run.started_at);
}

/**
 * One run in a cell, or the word for its absence.
 *
 * "never" and "none" are different absences and are worded differently on purpose: a scope
 * that has never succeeded is the worst state on this page, and one that has never failed
 * is the best.
 */
function RunCell({ run, absent }: { run: RunOutcome | null; absent: string }): JSX.Element {
  if (run === null) {
    return <span className={absent === "never" ? "status-bad" : "muted"}>{absent}</span>;
  }
  return (
    <>
      {formatUtc(run.completed_at ?? run.started_at)}
      <div className="ace-notes muted">
        {run.collector_host} · {run.status}
        {run.error_count > 0 && ` · ${run.error_count} error(s)`}
      </div>
    </>
  );
}

function statusClass(status: string): string {
  if (status === "succeeded") {
    return "status-ok";
  }
  if (status === "failed") {
    return "status-bad";
  }
  return "status-warn";
}
