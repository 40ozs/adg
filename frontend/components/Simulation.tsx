import type { JSX } from "react";
import Link from "next/link";

import type {
  SimulationApplicationView,
  SimulationBaselineView,
  SimulationDeltaView,
  SimulationReportView,
  StoredSimulationView,
} from "@/lib/contracts";
import { principalLabel } from "@/lib/identity";
import { impactHeadline, orderedDeltas, summaryLine } from "@/lib/simulation";

/**
 * Rendering a what-if.
 *
 * Two rules run through every component here.
 *
 * **Nothing was applied, and the page says so before it says anything else.** The notice is
 * the first thing on every simulation surface, it is `role="alert"`, and it is the sentence
 * the API sends rather than one written here — so the three places a simulation is rendered
 * cannot each word it their own way, and none of them can soften it.
 *
 * **A caveat is not a footnote.** `loss_may_not_hold` is the field that stands between a
 * report and a remediation that achieves nothing, and a UI that rendered it as a small grey
 * icon would have thrown the phase away. It is rendered as a warning beside the row it
 * qualifies, in the API's own words.
 *
 * Nothing here computes anything about rights. Every mask, every direction, every count came
 * from the engine; a browser-side second opinion could only ever disagree with the answer
 * this product exists to give.
 */

export function SimulationNotice({ notice }: { notice: string }): JSX.Element {
  return (
    <div className="banner banner-warning" role="alert">
      <h2>No changes will be applied</h2>
      <p>{notice}</p>
    </div>
  );
}

export function BaselinePanel({
  baseline,
  stale,
}: {
  baseline: SimulationBaselineView;
  stale?: boolean;
}): JSX.Element {
  const isStale = stale ?? baseline.stale;
  return (
    <div className="card">
      <h2>What this was measured against</h2>
      <p className="muted">
        {baseline.kind === "as_of" && baseline.at
          ? `The estate as it was at ${baseline.at}.`
          : "The estate as it stands now."}{" "}
        Collection state <code>{baseline.token}</code>, captured {baseline.captured_at}.
      </p>
      {baseline.is_empty && (
        <p className="status-warn">
          Nothing has been collected. Every answer over an empty estate is &ldquo;no
          access&rdquo;, so this proposal reports no impact truthfully and uselessly.
        </p>
      )}
      {isStale && (
        <p className="status-warn">
          A collector has written something since this was measured. The current state is{" "}
          <code>{baseline.current_token}</code>. The proposal is unchanged; what has moved is
          the estate. Run it again to answer against what is there now.
        </p>
      )}
    </div>
  );
}

export function ImpactPanel({ report }: { report: SimulationReportView }): JSX.Element {
  const headline = impactHeadline(report);
  const className =
    headline.tone === "loss"
      ? "verdict verdict-bad"
      : headline.tone === "gain"
        ? "verdict verdict-warn"
        : headline.tone === "inert"
          ? "verdict verdict-info"
          : headline.tone === "mixed"
            ? "verdict verdict-warn"
            : "verdict verdict-ok";
  const { summary } = report;
  return (
    <div className="card">
      <h2>What it would do</h2>
      <p className={className}>{headline.headline}</p>
      <p>{headline.detail}</p>
      <dl className="simulation-counts">
        <div>
          <dt>Principals affected</dt>
          <dd>{summary.principals_affected}</dd>
        </div>
        <div>
          <dt>Pairs evaluated</dt>
          <dd>{summary.evaluated}</dd>
        </div>
        <div>
          <dt>Directories affected</dt>
          <dd>{summary.resources_affected.length}</dd>
        </div>
        <div>
          <dt>Alternate routes retained</dt>
          <dd>{summary.alternate_paths_retained}</dd>
        </div>
      </dl>
      {summary.sensitive_resources_affected.length > 0 && (
        <p className="status-warn">
          Sensitive directories affected:{" "}
          {summary.sensitive_resources_affected.map((key) => (
            <code key={key}>{key} </code>
          ))}
        </p>
      )}
      {summary.watched_resources_affected === null ? (
        <p className="muted">
          Whether a watch covers these directories is not shown: it needs{" "}
          <code>alerts:read</code>, which this account does not hold.
        </p>
      ) : (
        summary.watched_resources_affected.length > 0 && (
          <p className="status-warn">
            Watched directories affected:{" "}
            {summary.watched_resources_affected.map((key) => (
              <code key={key}>{key} </code>
            ))}
          </p>
        )
      )}
    </div>
  );
}

/**
 * What became of each proposed change.
 *
 * Rendered above the impact list, not below it. An empty impact list means something
 * completely different depending on this panel, and a reader who scrolls past the counts and
 * stops has to have seen it.
 */
export function ApplicationsPanel({
  applications,
}: {
  applications: readonly SimulationApplicationView[];
}): JSX.Element {
  return (
    <div className="card">
      <h2>What was proposed</h2>
      <ul className="simulation-changes">
        {applications.map((application, index) => (
          <li key={`${application.change.kind}-${index}`}>
            <p>
              <strong>{application.change.description}</strong>
            </p>
            <p className={application.applied ? "muted" : "status-warn"}>
              <span className={application.applied ? "badge badge-allow" : "badge badge-deny"}>
                {application.outcome}
              </span>{" "}
              {application.outcome_description}
            </p>
            {(application.change.member ?? application.change.trustee) && (
              <p className="muted">
                {application.change.member && (
                  <>Member: {principalLabel(application.change.member)}. </>
                )}
                {application.change.group && (
                  <>Group: {principalLabel(application.change.group)}. </>
                )}
                {application.change.trustee && (
                  <>Trustee: {principalLabel(application.change.trustee)}.</>
                )}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TruncationPanel({ report }: { report: SimulationReportView }): JSX.Element | null {
  if (report.complete) {
    return null;
  }
  return (
    <div className="banner banner-warning" role="alert">
      <h2>This is part of the answer, not the whole of it</h2>
      <ul>
        {report.truncation.map((reason) => (
          <li key={reason.code}>
            <strong>{reason.code}</strong> — {reason.description}
          </li>
        ))}
      </ul>
      <p className="muted">
        Principals or directories exist that this report does not mention. Do not read the list
        below as complete.
      </p>
    </div>
  );
}

/**
 * One row per principal and directory, with the whole answer either side of the change.
 *
 * Unchanged rows are kept. They are the evidence that the simulation looked at somebody and
 * found nothing — which is a different statement from not having looked, and the one an
 * operator would otherwise infer from a list that quietly omitted them.
 */
export function DeltaTable({ report }: { report: SimulationReportView }): JSX.Element {
  const deltas = orderedDeltas(report.deltas);
  if (deltas.length === 0) {
    return (
      <div className="card">
        <h2>Who it affects</h2>
        <p className="muted">
          No principal/directory pair was evaluated. With nothing applied there is nothing to
          evaluate; see what became of each change above.
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <h2>Who it affects</h2>
      <p className="muted">
        Every pair evaluated, unchanged ones included. A pair that is not listed was not
        evaluated.
      </p>
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
            {deltas.map((delta) => (
              <DeltaRow
                key={`${delta.subject.key}|${delta.resource.resource_key}|${delta.access_path}`}
                delta={delta}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DeltaRow({ delta }: { delta: SimulationDeltaView }): JSX.Element {
  const toneClass =
    delta.direction === "lost_access" || delta.direction === "reduced"
      ? "status-bad"
      : delta.direction === "gained_access" || delta.direction === "expanded"
        ? "status-warn"
        : "muted";
  return (
    <tr>
      <td>{principalLabel(delta.subject)}</td>
      <td>
        <code>{delta.resource.path ?? delta.resource.resource_key}</code>
        {delta.resource.sensitive && (
          <>
            {" "}
            <span className="badge badge-deny">sensitive</span>
          </>
        )}
        {delta.resource.watched === true && (
          <>
            {" "}
            <span className="badge">watched</span>
          </>
        )}
        {delta.resource.sensitivity_labels.length > 0 && (
          <p className="muted">{delta.resource.sensitivity_labels.join("; ")}</p>
        )}
      </td>
      <td>
        <code>{delta.rights_before.mask}</code>
        <p className="muted">{delta.rights_before.label}</p>
      </td>
      <td>
        <code>{delta.rights_after.mask}</code>
        <p className="muted">{delta.rights_after.label}</p>
      </td>
      <td>
        <p className={toneClass}>{delta.direction}</p>
        <p className="muted">{delta.direction_description}</p>
        {delta.caveats.map((caveat) => (
          <p key={caveat.code} className="status-warn" role="note">
            <strong>{caveat.code}</strong> — {caveat.description}
          </p>
        ))}
        {delta.retained_routes.length > 0 && (
          <div className="simulation-routes">
            <p className="muted">Routes that survive this change:</p>
            <ul>
              {delta.retained_routes.map((route, index) => (
                <li key={`${route.ace_key ?? "route"}-${index}`}>
                  {route.chain.map((item) => principalLabel(item)).join(" → ")}{" "}
                  <span className="muted">
                    ({route.layer}
                    {route.inherited ? ", inherited" : ""}
                    {route.assumed ? ", via an assumed token SID" : ""}, {route.rights.mask})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </td>
    </tr>
  );
}

export function CostFooter({ report }: { report: SimulationReportView }): JSX.Element {
  return (
    <details className="card">
      <summary>What this cost, and the limits it ran under</summary>
      <p className="muted">
        {report.cost.pairs_evaluated} pairs, {report.cost.resolutions} access checks,{" "}
        {report.cost.explanations} alternate-path analyses, {report.cost.edges_read} membership
        edges read, in {report.cost.elapsed_ms} ms.
      </p>
      <p className="muted">
        Limits: {report.bounds.max_principals} principals, {report.bounds.max_resources}{" "}
        directories, {report.bounds.max_pairs} pairs, {report.bounds.max_explanations}{" "}
        alternate-path analyses, {report.bounds.time_budget_ms} ms. Scope:{" "}
        <code>{report.scope.kind}</code>, access path <code>{report.scope.path}</code>.
      </p>
    </details>
  );
}

/** The whole report, in the order somebody has to read it. */
export function SimulationReport({
  report,
  heading,
}: {
  report: SimulationReportView;
  heading?: string;
}): JSX.Element {
  return (
    <>
      <SimulationNotice notice={report.notice} />
      {heading && <h2>{heading}</h2>}
      <ApplicationsPanel applications={report.applications} />
      <TruncationPanel report={report} />
      <ImpactPanel report={report} />
      <DeltaTable report={report} />
      <BaselinePanel baseline={report.baseline} />
      <CostFooter report={report} />
    </>
  );
}

export function StoredSimulationsTable({
  simulations,
}: {
  simulations: readonly StoredSimulationView[];
}): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="access-table">
        <thead>
          <tr>
            <th scope="col">Proposal</th>
            <th scope="col">Changes</th>
            <th scope="col">Written</th>
            <th scope="col">Baseline</th>
          </tr>
        </thead>
        <tbody>
          {simulations.map((item) => (
            <tr key={item.simulation_id}>
              <td>
                <Link href={`/simulations/${item.simulation_id}`}>{item.name}</Link>
                {item.description && <p className="muted">{item.description}</p>}
              </td>
              <td>
                {item.change_count}
                <p className="muted">{item.changes[0]?.description}</p>
              </td>
              <td>
                {item.created_at}
                {item.created_by && <p className="muted">{item.created_by}</p>}
              </td>
              <td>
                {item.baseline.stale ? (
                  <span className="status-warn">the estate has moved since</span>
                ) : (
                  <span className="muted">current</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvaluationHistory({
  evaluations,
}: {
  evaluations: readonly { evaluation_id: string; computed_at: string; scope_kind: string; pairs_evaluated: number; complete: boolean; stale_baseline: boolean; duration_ms: number }[];
}): JSX.Element {
  return (
    <div className="card">
      <h2>Every time this was run</h2>
      <p className="muted">
        Re-running a proposal adds a result rather than replacing one. &ldquo;This change was
        safe on Monday and takes access away today&rdquo; is the sentence a proposal&rsquo;s
        history exists to make available.
      </p>
      <ul>
        {evaluations.map((item) => (
          <li key={item.evaluation_id}>
            {item.computed_at} — <code>{item.scope_kind}</code> scope, {item.pairs_evaluated}{" "}
            pairs, {item.duration_ms} ms
            {item.complete ? "" : ", incomplete"}
            {item.stale_baseline ? ", measured against a state the proposal did not name" : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

export { summaryLine };
