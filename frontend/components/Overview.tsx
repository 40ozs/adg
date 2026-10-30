import type { JSX } from "react";
import Link from "next/link";

import type { ApiResult } from "@/lib/api/client";
import type {
  ChangeSummaryResponse,
  ChangesResponse,
  CollectionHealth,
  CollectionStatus,
  QueueResponse,
  RiskRuleView,
  RiskSummaryResponse,
  RuntimeInfo,
} from "@/lib/contracts";
import { groupChanges, headline, truncationNotice } from "@/lib/changes";
import { totalsNotice, type Notice, type StateWords } from "@/lib/coverage";
import { dueWording, queueHeadline } from "@/lib/governance";
import { NAV_ITEMS } from "@/lib/nav";
import { formatUtc } from "@/lib/operations";
import { hrefWith } from "@/lib/paging";
import { coverageNotice, ruleTallies } from "@/lib/risks";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { SeverityBadge } from "@/components/Changes";
import { StateBadge } from "@/components/Coverage";

import styles from "./overview.module.css";

/**
 * The Overview's sections (ADG-UX-06): what needs attention, what changed, what is waiting
 * for this reviewer, and whether the evidence behind all of it can be trusted.
 *
 * Every number is one the API returned, and every number links to the records behind it.
 * Nothing here adds severities together or turns coverage into a percentage. Where the API
 * cannot say something — "new since the last evaluation", "due soon" — the section does not
 * say it either.
 */

/**
 * A section's route, read from the navigation by id, so a route or label decision made there
 * (ADG-UX-01) reaches these links without a second copy of the path.
 */
export function sectionHref(id: string): string {
  return NAV_ITEMS.find((item) => item.id === id)?.href ?? "/";
}

function plural(count: number, singular: string, many = `${singular}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? singular : many}`;
}

/** How the risk counts may be read: the API's interpretation, or the evaluation-only notice. */
function riskReading(summary: RiskSummaryResponse): Notice {
  return totalsNotice(summary) ?? coverageNotice(summary.coverage);
}

/**
 * Whether the evidence is in a state that should be read before anything else. Data health
 * moves above the other sections when it is, because a count read before its caveat is a
 * count read as a total.
 */
export function evidenceNeedsAttention(
  status: ApiResult<CollectionStatus>,
  risk: ApiResult<RiskSummaryResponse> | null,
): boolean {
  if (!status.ok || status.data.health !== "healthy") {
    return true;
  }
  return risk !== null && risk.ok && riskReading(risk.data).tone !== "ok";
}

/* ------------------------------------------------------------------ needs attention */

const ATTENTION_SEVERITIES = ["critical", "high"] as const;

export function NeedsAttention({
  result,
  rules,
}: {
  result: ApiResult<RiskSummaryResponse>;
  rules: readonly RiskRuleView[];
}): JSX.Element {
  // Never empty: the summary states its own coverage, and the wording below handles zero.
  const state = classify(result, { isEmpty: () => false, subject: "findings" });
  return (
    <section className="card" aria-labelledby="overview-attention">
      <h2 id="overview-attention">Needs attention</h2>
      {state.kind === "ready" ? (
        <AttentionBody summary={state.data} rules={rules} />
      ) : (
        <StateMessage state={state} />
      )}
    </section>
  );
}

function AttentionBody({
  summary,
  rules,
}: {
  summary: RiskSummaryResponse;
  rules: readonly RiskRuleView[];
}): JSX.Element {
  const findings = sectionHref("risks");
  const reading = riskReading(summary);
  const isTotal = reading.tone === "ok";

  if (!summary.coverage.has_ever_run) {
    return (
      <>
        <p>
          <StateBadge words={{ word: "Not evaluated", symbol: "∅", tone: "warn" }} />{" "}
          <strong>Findings have not been evaluated yet.</strong> No count here would mean the
          estate is clean, because nothing has looked at it.
        </p>
        <p>
          <Link href={findings}>See what an evaluation needs</Link>
        </p>
      </>
    );
  }

  const caveat = isTotal ? null : (
    <p role="status">
      <StateBadge words={{ word: "Not a total", symbol: "⚠", tone: "warn" }} />{" "}
      <strong>{reading.headline}.</strong> {reading.explanation}
    </p>
  );

  if (summary.total === 0) {
    return (
      <>
        <p>
          {isTotal
            ? "No open findings in the latest completed evaluation."
            : "No open findings were returned, but that is not a complete answer."}
        </p>
        {caveat}
        <p>
          <Link href={hrefWith(findings, { status: "all" })}>View resolved findings</Link>
        </p>
      </>
    );
  }

  const common = ruleTallies(summary.rule_counts, [...rules])
    .filter((tally) => tally.count > 0)
    .slice(0, 3);

  return (
    <>
      <ul className={styles.counts} aria-label="Open findings by severity">
        {ATTENTION_SEVERITIES.map((severity) => (
          <li key={severity}>
            <Link href={hrefWith(findings, { severity })}>
              <strong>{(summary.severity_counts[severity] ?? 0).toLocaleString("en-US")}</strong>{" "}
              {severity} {summary.severity_counts[severity] === 1 ? "finding" : "findings"}
            </Link>
          </li>
        ))}
        <li>
          <Link href={findings}>
            <strong>{summary.total.toLocaleString("en-US")}</strong> open{" "}
            {summary.total === 1 ? "finding" : "findings"} in all
          </Link>
        </li>
      </ul>
      {caveat}
      {common.length > 0 && (
        <>
          <h3>Most common</h3>
          <ul aria-label="Most common open findings">
            {common.map((tally) => (
              <li key={tally.ruleId}>
                <Link href={hrefWith(findings, { rule: tally.ruleId })}>{tally.title}</Link> —{" "}
                {tally.count.toLocaleString("en-US")}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ recent changes */

/** How many recent changes the section names. The rest are one link away. */
const RECENT_LIMIT = 3;

export function RecentChanges({
  summary,
  feed,
  collection,
  days,
}: {
  summary: ApiResult<ChangeSummaryResponse>;
  feed: ApiResult<ChangesResponse>;
  collection: CollectionStatus | null;
  days: number;
}): JSX.Element {
  const changes = sectionHref("changes");
  // The coverage verdict words an empty window: "no changes" is only an answer when
  // collection is current, because a change is something a collector saw.
  const state = classify(summary, {
    isEmpty: (data) => data.total === 0,
    coverage: collection,
    subject: `changes in the last ${days} days`,
  });
  const groups = feed.ok
    ? groupChanges(feed.data.changes, feed.data.edits).slice(0, RECENT_LIMIT)
    : [];
  return (
    <section className="card" aria-labelledby="overview-changes">
      <h2 id="overview-changes">Recent changes</h2>
      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <>
          <p>
            <strong>{plural(state.data.returned, "permission or unclassified change")}</strong>{" "}
            in the last {days} days
            {state.data.highest_severity !== null && (
              <>
                ; most severe <SeverityBadge severity={state.data.highest_severity} />
              </>
            )}
            .
          </p>
          {state.data.excluded > 0 && (
            <p className="muted">
              {plural(state.data.excluded, "more change")} — description edits, rewrites that
              changed nothing, and first sightings — are outside that view.
            </p>
          )}
          {truncationNotice(state.data) !== null && (
            <p className="muted">{truncationNotice(state.data)}</p>
          )}
          {feed.ok && groups.length > 0 && (
            <ul aria-label="Most recent changes">
              {groups.map((group, index) => {
                const change = group.changes[0];
                return (
                  <li key={`${change.kind}|${change.key}|${index}`}>
                    <SeverityBadge severity={group.edit?.severity ?? change.severity} />{" "}
                    <Link href={hrefWith(changes, { kind: change.kind, key: change.key })}>
                      {headline(change, feed.data.edits)}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <p>
        <Link href={hrefWith(changes, { days })}>View changes from the last {days} days</Link>
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ reviews */

export function Reviews({ result }: { result: ApiResult<QueueResponse> }): JSX.Element {
  const reviews = sectionHref("governance");
  return (
    <section className="card" aria-labelledby="overview-reviews">
      <h2 id="overview-reviews">Reviews</h2>
      {!result.ok ? (
        <StateMessage state={classify(result, { isEmpty: () => false, subject: "reviews" })} />
      ) : result.data.entries.length === 0 ? (
        // Not the shared empty state: an empty queue says nothing about collection.
        <p>
          Nothing is assigned to you. A queue holds what was asked of you, not what you may
          read.
        </p>
      ) : (
        <>
          <p>
            <strong>{plural(result.data.total_pending, "item")}</strong> assigned to you across{" "}
            {plural(result.data.entries.length, "campaign")}
            {result.data.overdue_campaigns > 0 && (
              <span className="status-bad">
                {" "}
                — {plural(result.data.overdue_campaigns, "campaign")} past due
              </span>
            )}
            .
          </p>
          <ul aria-label="Campaigns in your queue">
            {result.data.entries.slice(0, RECENT_LIMIT).map((entry) => (
              <li key={entry.campaign_id}>
                <Link href={`${reviews}/campaigns/${entry.campaign_id}?mine=1`}>{entry.name}</Link>{" "}
                <span className={entry.overdue ? "status-bad" : "muted"}>
                  {queueHeadline(entry)} · {dueWording(entry.due_at, entry.overdue)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p>
        <Link href={reviews}>
          {result.ok && result.data.entries.length > 0 ? "Open my review queue" : "See review campaigns"}
        </Link>
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ data health */

const HEALTH_WORDS: Record<CollectionHealth, StateWords> = {
  healthy: { word: "Complete", symbol: "✓", tone: "ok" },
  incomplete: { word: "Incomplete", symbol: "◐", tone: "warn" },
  failed: { word: "Failed", symbol: "✕", tone: "bad" },
  no_data: { word: "Never collected", symbol: "∅", tone: "warn" },
};

/** How many concerns the section names before pointing at the collector page. */
const CONCERN_LIMIT = 3;

export function DataHealth({
  status,
  risk,
  runtime,
  canOpenCollectors,
}: {
  status: ApiResult<CollectionStatus>;
  risk: RiskSummaryResponse | null;
  runtime: RuntimeInfo | null;
  canOpenCollectors: boolean;
}): JSX.Element {
  const state = classify(status, { isEmpty: () => false, subject: "collectors" });
  return (
    <section className="card" aria-labelledby="overview-health">
      <h2 id="overview-health">Data health</h2>
      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <HealthFacts status={state.data} risk={risk} runtime={runtime} />
      )}
      {canOpenCollectors && (
        <p>
          <Link href={sectionHref("collectors")}>Check collection status</Link>
        </p>
      )}
    </section>
  );
}

function HealthFacts({
  status,
  risk,
  runtime,
}: {
  status: CollectionStatus;
  risk: RiskSummaryResponse | null;
  runtime: RuntimeInfo | null;
}): JSX.Element {
  const scopes = status.collectors.length;
  const trusted = status.collectors.filter((scope) => scope.trustworthy).length;
  const reading = risk === null ? null : riskReading(risk);
  const extra = status.concerns.length - CONCERN_LIMIT;
  return (
    <>
      <dl className="facts">
        <dt>Collection</dt>
        <dd>
          <StateBadge words={HEALTH_WORDS[status.health]} /> {status.summary}
        </dd>
        {scopes > 0 && (
          <>
            <dt>Scopes</dt>
            <dd>
              {trusted.toLocaleString("en-US")} of {plural(scopes, "collection scope")} have a
              latest run that succeeded with no errors
            </dd>
          </>
        )}
        {risk?.collection && (
          <>
            <dt>Coverage</dt>
            <dd>{risk.collection.headline}</dd>
          </>
        )}
        {risk !== null && reading !== null && (
          <>
            <dt>Risk evaluation</dt>
            <dd>
              {risk.coverage.has_ever_run ? (
                <>
                  <StateBadge
                    words={
                      reading.tone === "ok"
                        ? { word: "Total", symbol: "✓", tone: "ok" }
                        : { word: "Not a total", symbol: "⚠", tone: "warn" }
                    }
                  />{" "}
                  Last ran {formatUtc(risk.coverage.evaluated_at)}
                  {risk.coverage.trigger !== null && <> ({risk.coverage.trigger})</>}.{" "}
                  {reading.tone !== "ok" && reading.headline}
                </>
              ) : (
                <>
                  <StateBadge words={{ word: "Not evaluated", symbol: "∅", tone: "warn" }} /> The
                  rules have never been evaluated.
                </>
              )}
            </dd>
          </>
        )}
        {runtime?.runtime_mode === "demo" && (
          <>
            <dt>Source</dt>
            <dd>
              Synthetic demo data
              {runtime.dataset ? ` for ${runtime.dataset.company_name}` : ""}. No production
              directory or file server is connected.
            </dd>
          </>
        )}
      </dl>
      {status.concerns.length > 0 && (
        <ul aria-label="Collection concerns">
          {status.concerns.slice(0, CONCERN_LIMIT).map((concern) => (
            <li key={concern}>{concern}</li>
          ))}
          {extra > 0 && <li className="muted">and {plural(extra, "more concern")}</li>}
        </ul>
      )}
    </>
  );
}
