import type { JSX } from "react";
import Link from "next/link";

import type {
  CollectionCoverage,
  CoverageSummary,
  RiskSummaryResponse,
  ScopeCoverage,
} from "@/lib/contracts";
import {
  coverageLine,
  evidenceAgeWords,
  evidenceWords,
  incompleteScopes,
  scopeStateWords,
  snapshotWords,
  totalsNotice,
  verdictWords,
  type StateWords,
} from "@/lib/coverage";

/**
 * Collection coverage, rendered beside the numbers it qualifies (ADG-DEMO-03).
 *
 * Nothing here decides anything. The API states each scope's latest attempt and the age of its
 * evidence, whether a risk total is exhaustive, and whether an incomplete scope could change an
 * access answer; these components put those statements where a reviewer reads the number.
 * Every state is a word and a symbol beside its color.
 */

export function StateBadge({ words }: { words: StateWords }): JSX.Element {
  return (
    <span className={`badge status-${words.tone}`}>
      <span aria-hidden="true">{words.symbol}</span> {words.word}
    </span>
  );
}

/**
 * How the risk counts may be read, and why. Replaces the evaluation-only banner when the API
 * sends an interpretation, because "a full pass covered the estate" is only half of whether a
 * count is a total.
 */
export function RiskTotalsReading({ summary }: { summary: RiskSummaryResponse }): JSX.Element | null {
  const notice = totalsNotice(summary);
  if (notice === null) {
    return null;
  }
  const held = (summary.interpretation?.held ?? []).filter((item) => item.evidence !== "current");
  const banner = notice.tone === "warning" ? "banner-warning" : "banner-info";
  return (
    <div className={`banner ${banner}`} role="status" id="risk-reading">
      <h2>{notice.headline}</h2>
      <p>{notice.explanation}</p>
      {held.length > 0 && (
        <>
          <p>
            Findings a failed scan could not re-check are still counted, from the scan that last
            read them. A failed scan closes nothing:
          </p>
          <ul aria-label="Findings held from older evidence">
            {held.map((item) => (
              <li key={item.scope}>
                <strong>{item.scope}</strong>: {item.open_findings.toLocaleString("en-US")} open
                finding(s) — {evidenceAgeWords(item.evidence, item.evidence_collected_at)}
              </li>
            ))}
          </ul>
        </>
      )}
      {summary.coverage.rules_skipped.length > 0 && (
        <p>
          Rules that did not run: {summary.coverage.rules_skipped.join(", ")}. Anything they would
          have found is not here.
        </p>
      )}
    </div>
  );
}

/** The coverage line beside the severity counts, and the scopes behind it one click away. */
export function RiskCoverageLine({
  collection,
  exhaustive,
}: {
  collection: CoverageSummary;
  exhaustive: boolean;
}): JSX.Element {
  return (
    <section className="card" id="risk-coverage" aria-labelledby="risk-coverage-heading">
      <h2 id="risk-coverage-heading">Collection behind these counts</h2>
      <p>
        <StateBadge words={verdictWords(collection.verdict)} /> {coverageLine(collection)}
      </p>
      <p>
        <strong>{exhaustive ? "Exhaustive:" : "Not exhaustive:"}</strong>{" "}
        {exhaustive
          ? "every scope was read completely and the counts are totals."
          : "read these counts as what ADG holds, not as everything the estate contains."}
      </p>
      <p className="muted">{snapshotWords(collection.snapshot)}</p>
      {collection.concerns.length > 0 && (
        <details>
          <summary>Which scopes are not complete ({collection.concerns.length})</summary>
          <ul>
            {collection.concerns.map((concern) => (
              <li key={concern}>{concern}</li>
            ))}
          </ul>
        </details>
      )}
      <p>
        <Link href="/collectors">Every scope, on the collector status page</Link>
      </p>
    </section>
  );
}

function ScopeItem({ scope }: { scope: ScopeCoverage }): JSX.Element {
  return (
    <li>
      <StateBadge words={scopeStateWords(scope.state)} /> <strong>{scope.label}</strong> —{" "}
      {evidenceWords(scope)}
      {scope.returned_nothing && <> (the scan found nothing to report)</>}
    </li>
  );
}

/**
 * The investigation's collection context: the scopes this answer reads, whether any is
 * incomplete, and the estate around it. Context beside the answer -- it does not change the
 * answer's own certainty, which section 3 states.
 */
export function AnswerCoveragePanel({ coverage }: { coverage: CollectionCoverage }): JSX.Element | null {
  const relevant = coverage.relevant;
  if (relevant === null) {
    return null;
  }
  const elsewhere = incompleteScopes(coverage.scopes).filter(
    (scope) => !relevant.scopes.some((item) => item.label === scope.label),
  );
  return (
    <section className="card" id="collection-coverage" aria-labelledby="collection-coverage-heading">
      <h2 id="collection-coverage-heading">6. How complete is the evidence behind this answer?</h2>
      <p>
        <StateBadge words={verdictWords(relevant.verdict)} />{" "}
        <strong>
          {relevant.could_change_answer
            ? "Missing or older collection could change this answer."
            : "Missing collection elsewhere cannot change this answer."}
        </strong>
      </p>
      <p>{relevant.statement}</p>
      <h3>The scopes this answer reads</h3>
      <ul aria-label="Collection scopes this answer reads">
        {relevant.scopes.map((scope) => (
          <ScopeItem key={scope.label} scope={scope} />
        ))}
      </ul>
      <h3>The estate around it</h3>
      <p>
        <StateBadge words={verdictWords(coverage.verdict)} /> {coverage.headline}.
      </p>
      {elsewhere.length > 0 && (
        <details>
          <summary>Incomplete scopes elsewhere ({elsewhere.length})</summary>
          <ul aria-label="Incomplete scopes this answer does not read">
            {elsewhere.map((scope) => (
              <li key={scope.label}>
                <StateBadge words={scopeStateWords(scope.state)} /> {scope.sentence}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="muted">
        Context beside the answer, not part of it: the answer&apos;s own certainty and open
        questions are in sections 1 and 3. {snapshotWords(coverage.snapshot)}
      </p>
      <p>
        <Link href="/risks#risk-coverage">Risk totals and their coverage</Link> ·{" "}
        <Link href="/collectors">Every scope, on the collector status page</Link>
      </p>
    </section>
  );
}
