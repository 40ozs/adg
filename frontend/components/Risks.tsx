import type { JSX } from "react";
import Link from "next/link";

import type {
  FindingDetailView,
  FindingSummaryView,
  RiskConfigurationView,
  RiskCoverageView,
  RiskRuleView,
} from "@/lib/contracts";
import {
  ageWording,
  configurationNotice,
  confidenceWording,
  coverageNotice,
  evidenceKindWording,
  eventWording,
  hiddenByStatus,
  recurrenceNotice,
  ruleTallies,
  severityTiles,
  severityTone,
  subjectLabel,
} from "@/lib/risks";

/**
 * Rendering a risk report so that an empty one cannot be mistaken for a clean estate.
 *
 * Every judgment on this page comes off the response — severity, confidence, whether a
 * finding reproduces. What this file chooses is *which caveats are impossible to miss*, and
 * there are three, each of which a reader would otherwise have to know to ask about:
 *
 * - **What the last pass covered.** Shown above the counts, always, including when it is
 *   reassuring. A caveat that appears only when things are bad teaches a reader to skip it.
 * - **What the configuration is hiding.** A disabled rule reports nothing, and that silence
 *   is a decision somebody made rather than a result.
 * - **What the filter is hiding.** The default view is open findings; the strip says how
 *   many resolved ones it is not showing.
 */

export function SeverityBadge({ severity }: { severity: string }): JSX.Element {
  return (
    <span className={`badge status-${severityTone(severity)}`} title={`Severity: ${severity}`}>
      {severity}
    </span>
  );
}

/** The coverage caveat. Rendered in all three states, never only the bad ones. */
export function CoverageBanner({ coverage }: { coverage: RiskCoverageView }): JSX.Element {
  const notice = coverageNotice(coverage);
  const banner =
    notice.tone === "warning" ? "banner-warning" : notice.tone === "ok" ? "banner-info" : "banner-info";
  return (
    <div className={`banner ${banner}`} role="status">
      <h2>{notice.headline}</h2>
      <p>{notice.explanation}</p>
      {coverage.rules_skipped.length > 0 && (
        <p>
          Rules that did not run: {coverage.rules_skipped.join(", ")}. Anything they would
          have found is not here.
        </p>
      )}
    </div>
  );
}

/**
 * Severity counts, the rule tally, and what is not being shown.
 *
 * Every severity appears, zeros included. A tile list that omitted the empty ones would
 * make a report with no critical findings look like a report that has no critical category.
 */
export function RiskSummaryStrip({
  severityCounts,
  ruleCounts,
  statusCounts,
  shownStatuses,
  configuration,
  rules,
}: {
  severityCounts: Record<string, number>;
  ruleCounts: Record<string, number>;
  statusCounts: Record<string, number>;
  shownStatuses: string[];
  configuration: RiskConfigurationView;
  rules: RiskRuleView[];
}): JSX.Element {
  const hidden = hiddenByStatus(statusCounts, shownStatuses);
  const configured = configurationNotice(configuration);
  const tallies = ruleTallies(ruleCounts, rules);
  return (
    <section className="card" aria-label="Summary">
      <h2>Findings</h2>
      <ul className="filter-strip" aria-label="Severity counts">
        {severityTiles(severityCounts).map((tile) => (
          <li key={tile.severity}>
            <span className={`badge status-${tile.tone}`}>{tile.severity}</span>{" "}
            <strong>{tile.count}</strong>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <p className="muted" role="status">
          {hidden} finding(s) are not shown because of the status filter in use. A finding
          that was true in March is a fact about March; resolved ones are kept, never deleted.
        </p>
      )}
      {tallies.length > 0 && (
        <details>
          <summary>By rule</summary>
          <ul>
            {tallies.map((tally) => (
              <li key={tally.ruleId}>
                {tally.title} — <strong>{tally.count}</strong>{" "}
                <span className="muted">({tally.ruleId})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {configured !== null && (
        <p className="verdict-note" role="status">
          {configured}
        </p>
      )}
    </section>
  );
}

/** One page of findings, most consequential first. */
export function FindingTable({
  findings,
  drawerHref,
  openKey,
}: {
  findings: FindingSummaryView[];
  drawerHref: (finding: FindingSummaryView) => string;
  openKey: string | null;
}): JSX.Element {
  const now = new Date();
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption className="visually-hidden">
          Risk findings, most consequential first. Each row links to the records it was
          matched on.
        </caption>
        <thead>
          <tr>
            <th scope="col">Severity</th>
            <th scope="col">Finding</th>
            <th scope="col">Where</th>
            <th scope="col">Confidence</th>
            <th scope="col">Seen</th>
            <th scope="col">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((finding) => (
            <tr key={finding.key}>
              <td>
                <SeverityBadge severity={finding.severity} />
              </td>
              <td>
                {finding.title}
                <br />
                <span className="muted">{finding.rule_id}</span>
                {finding.status === "resolved" && (
                  <>
                    {" "}
                    <span className="badge status-ok">resolved</span>
                  </>
                )}
              </td>
              <td>
                <code>{subjectLabel(finding)}</code>
                {finding.subject.principal_key !== null &&
                  finding.subject.resource_key !== null && (
                    <>
                      <br />
                      <span className="muted">for {finding.subject.principal_key}</span>
                    </>
                  )}
              </td>
              <td title={confidenceWording(finding.confidence)}>{finding.confidence}</td>
              <td className="muted">{ageWording(finding, now)}</td>
              <td>
                {openKey === finding.key ? (
                  <strong>shown below</strong>
                ) : (
                  <Link href={drawerHref(finding)}>
                    {finding.evidence_count} record(s)
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The evidence drawer: the whole records, the timeline, and what to do about it.
 *
 * `evidence` is the records themselves and not a rendering of them. "Everyone → Modify" is
 * a sentence; it cannot be rebuilt into facts, and an auditor asked to accept a finding on a
 * sentence is being asked to accept ADG's word for it. The whole records can be re-derived,
 * and `reproduces` says that they were.
 */
export function EvidenceDrawer({
  detail,
  closeHref,
}: {
  detail: FindingDetailView;
  closeHref: string;
}): JSX.Element {
  const { finding, rule } = detail;
  const recurrence = recurrenceNotice(finding);
  return (
    <section className="card" aria-label="Evidence">
      <h2>
        <SeverityBadge severity={finding.severity} /> {finding.title}
      </h2>
      <p className="muted">
        <code>{subjectLabel(finding)}</code> · <Link href={closeHref}>close</Link>
      </p>

      <p>{rule.detects}</p>
      <p className="verdict-note">{rule.matters_because}</p>
      <p className="muted">{confidenceWording(finding.confidence)}</p>
      {finding.qualifiers.length > 0 && (
        <ul className="muted">
          {finding.qualifiers.map((qualifier) => (
            <li key={qualifier}>{qualifier}</li>
          ))}
        </ul>
      )}
      {recurrence !== null && (
        <div className="banner banner-info" role="status">
          <p>{recurrence}</p>
        </div>
      )}

      <h3>What it was matched on</h3>
      <p className="muted">
        The stored records themselves, not a summary of them — which is what makes this
        checkable rather than something to be taken on trust.
      </p>
      {detail.evidence.map((item) => (
        <details key={`${item.kind}:${item.key}`} className="acl-group">
          <summary>
            {evidenceKindWording(item.kind)} — <code>{item.key}</code>
          </summary>
          <dl>
            {Object.entries(item.record).map(([field, value]) => (
              <div key={field}>
                <dt>{field}</dt>
                <dd>
                  <code>{renderValue(value)}</code>
                </dd>
              </div>
            ))}
          </dl>
        </details>
      ))}

      <h3>Whether it still re-derives</h3>
      <p className={detail.reproduces ? "status-ok" : "status-warn"}>
        {detail.reproduction_note}
      </p>

      <h3>History</h3>
      <ol className="acl-group">
        {detail.events.map((item) => (
          <li key={`${item.event_type}:${item.occurred_at}`}>
            <strong>{eventWording(item.event_type)}</strong>{" "}
            <span className="muted">{item.occurred_at}</span>
            {item.severity !== null && <> · {item.severity}</>}
          </li>
        ))}
      </ol>

      <h3>What to do about it</h3>
      <p>{rule.remediation.summary}</p>
      <ol>
        {rule.remediation.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {rule.remediation.caution !== null && (
        <div className="banner banner-warning" role="status">
          <h2>Before you change anything</h2>
          <p>{rule.remediation.caution}</p>
        </div>
      )}
    </section>
  );
}

/** A stored value, rendered without pretending to interpret it. */
function renderValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
