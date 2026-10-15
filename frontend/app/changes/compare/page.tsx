import type { JSX } from "react";
import Link from "next/link";

import { compareInstants } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { ChangeComparisonResponse } from "@/lib/contracts";
import { comparisonOutcome, kindWords } from "@/lib/coverage";
import { hrefWith } from "@/lib/paging";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { ChangeSummaryStrip, ChangeTimeline } from "@/components/Changes";
import { ComparisonOutcomePanel, NarrowComparisonForm } from "@/components/Comparison";
import { ComparisonLink } from "@/components/ComparisonLink";
import { SignedOutNotice } from "@/components/SignedOutNotice";

const PATH = "/changes/compare";

/**
 * What is different between two points in time — a different question from the feed.
 *
 * An entry added on Wednesday and removed on Thursday appears in a Tuesday-to-Friday feed
 * twice and here not at all. Both answers are right, and offering only one of them would
 * quietly answer the other question badly:
 *
 * - the **feed** answers *what happened*, which is what an incident review needs;
 * - a **comparison** answers *what is different*, which is what a change-control review
 *   needs — "is the estate back the way it was before the maintenance window?"
 *
 * The number this page exists to show honestly is neither of the two lists. It is
 * ``unobserved_at_from`` and ``unobserved_at_to``: objects one of the two instants has no
 * version for. They are counted and named as gaps in observation, never folded into the
 * additions and removals, because rendering "we were not watching" as "this was deleted"
 * would revoke access on the strength of nobody having looked.
 *
 * **How it ended comes first (ADG-DEMO-03).** A comparison is bounded: by objects per
 * instant (`incomplete`, compared up to a named boundary) and by an interactive window
 * (`timed_out`, nothing presented). The outcome panel says which, before any list, and
 * offers the recovery actions the API listed. An incomplete list is labelled partial; a
 * timed-out comparison shows no list; only a complete one is presented as the answer. A
 * client-side timeout is its own state, not "ADG could not reach its API".
 */
export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const from = single(params.from)?.trim() ?? "";
  const to = single(params.to)?.trim() ?? "";

  if (from === "" || to === "") {
    return <Prompt />;
  }

  const request = comparisonRequest(params);
  const result = await compareInstants(viewer.session.accessToken, {
    from,
    to,
    ...request.scope,
    ...(request.kind !== "" ? { kind: [request.kind] } : {}),
    ...(request.window !== "" ? { time_budget_ms: request.window } : {}),
    ...(request.retryOf !== "" ? { retry_of: request.retryOf } : {}),
    significance: ["security", "undetermined"],
  });
  const state = classify(result, {
    // Never "nothing is different" for a list the comparison did not finish: an incomplete
    // or timed-out answer is rendered by its outcome, not by the emptiness of its list.
    isEmpty: () => false,
    subject: "differences",
  });

  const base = {
    from,
    to,
    ...request.scope,
    kind: request.kind,
    time_budget_ms: request.window,
  };
  const hrefFor = (parameters: Record<string, string>): string =>
    hrefWith(PATH, { ...base, ...parameters });
  const narrowForm = (
    <NarrowComparisonForm
      action={PATH}
      from={from}
      to={to}
      retryOf={state.kind === "ready" ? (state.data.comparison_id ?? null) : null}
      current={{ scope: request.scopeName, key: request.scopeKey, kind: request.kind }}
    />
  );

  return (
    <>
      <h1>Compare two points in time</h1>
      <p className="muted">
        {from} versus {to}
        {request.scopeName !== "" && (
          <>
            {" "}
            · {request.scopeName} <code>{request.scopeKey}</code>
          </>
        )}
        {request.kind !== "" && <> · {kindWords(request.kind)} only</>}
      </p>
      <p>
        <Link href="/changes">Back to changes</Link>
      </p>

      {state.kind !== "ready" ? (
        <>
          {result.ok === false && result.failure.kind === "timeout" ? (
            <ClientTimeout retryHref={hrefFor({ time_budget_ms: "" })} />
          ) : (
            <StateMessage state={state} />
          )}
          {narrowForm}
        </>
      ) : (
        <ComparisonResult
          comparison={state.data}
          hrefFor={hrefFor}
          narrowForm={narrowForm}
        />
      )}
    </>
  );
}

function ComparisonResult({
  comparison,
  hrefFor,
  narrowForm,
}: {
  comparison: ChangeComparisonResponse;
  hrefFor: (parameters: Record<string, string>) => string;
  narrowForm: JSX.Element;
}): JSX.Element {
  const outcome = comparisonOutcome(comparison);
  const presented = outcome === "complete" || outcome === "incomplete";
  return (
    <>
      <ComparisonOutcomePanel comparison={comparison} hrefFor={hrefFor} />
      {outcome !== "complete" && narrowForm}

      {presented && (
        <section className="card" aria-labelledby="compared-heading">
          <h2 id="compared-heading">
            {outcome === "complete"
              ? "What this comparison could and could not compare"
              : "What was compared before the comparison stopped (partial)"}
          </h2>
          <p>
            <strong>{comparison.unchanged}</strong> objects were present and identical at both
            instants{outcome === "incomplete" ? ", in the part that was compared" : ""}.
          </p>
          {(comparison.unobserved_at_from > 0 || comparison.unobserved_at_to > 0) && (
            <div className="banner banner-info" role="status">
              <h3>Not every object could be compared</h3>
              <p>
                {comparison.unobserved_at_from} objects have no version at all at the earlier
                instant and {comparison.unobserved_at_to} have none at the later one. They are
                counted here and reported in neither list: a gap in observation is not a
                change, and rendering one as a creation or a deletion would state something
                nothing supports.
              </p>
            </div>
          )}
        </section>
      )}

      {presented && <ChangeSummaryStrip summary={comparison.summary} />}

      {presented &&
        (comparison.changes.length === 0 ? (
          <div className="card">
            <h2>
              {outcome === "complete"
                ? "Nothing is different"
                : "Nothing is different in the part that was compared"}
            </h2>
            <p>
              {outcome === "complete" ? (
                <>
                  Every object covered at both instants holds the same state. Anything that
                  changed and changed back inside the interval is, correctly, not here — the{" "}
                  <Link href="/changes">change list</Link> is what reports that.
                </>
              ) : (
                <>
                  This says nothing about the objects past the point the comparison reached.
                  Use a recovery action above for a complete answer.
                </>
              )}
            </p>
          </div>
        ) : (
          <section aria-label={outcome === "complete" ? "Differences" : "Differences (partial)"}>
            {outcome === "incomplete" && (
              <p className="verdict-note" role="note">
                Partial list: the differences found before the comparison stopped. It is not
                the whole comparison.
              </p>
            )}
            <ChangeTimeline
              changes={comparison.changes}
              edits={comparison.edits}
              impactHref={(change) =>
                hrefWith("/changes/impact", {
                  kind: change.kind,
                  key: change.key,
                  at: change.at,
                })
              }
              timelineHref={(change) => hrefWith("/changes", { kind: change.kind, key: change.key })}
            />
          </section>
        ))}
    </>
  );
}

/** The web tier gave up before the API answered. Typed: not an outage, and not an answer. */
function ClientTimeout({ retryHref }: { retryHref: string }): JSX.Element {
  return (
    <div className="banner banner-warning" role="status" aria-live="polite">
      <h2>
        <span aria-hidden="true">⏱</span> Timed out — no comparison is presented
      </h2>
      <p>
        The comparison did not answer within this page&apos;s ten-second limit. That is a
        question too large to answer interactively, not an outage, and nothing partial is shown
        as an answer.{" "}
        <ComparisonLink href={retryHref}>Retry inside the standard window</ComparisonLink>, or
        narrow the scope below.
      </p>
    </div>
  );
}

interface ComparisonRequest {
  scope: Partial<Record<ScopeName, string>>;
  scopeName: ScopeName | "";
  scopeKey: string;
  kind: string;
  window: string;
  retryOf: string;
}

type ScopeName = "server" | "share" | "directory" | "principal" | "group";
const SCOPES: readonly ScopeName[] = ["server", "share", "directory", "principal", "group"];

/**
 * The comparison the URL asks for. A scope may arrive as its own parameter (`server=FS01`,
 * from a link) or as the narrowing form's `scope` and `key` pair; the API takes one at most.
 */
function comparisonRequest(params: Record<string, string | string[] | undefined>): ComparisonRequest {
  let scopeName: ScopeName | "" = "";
  let scopeKey = "";
  const chosen = single(params.scope)?.trim() ?? "";
  const key = single(params.key)?.trim() ?? "";
  if ((SCOPES as readonly string[]).includes(chosen) && key !== "") {
    scopeName = chosen as ScopeName;
    scopeKey = key;
  } else {
    for (const name of SCOPES) {
      const value = single(params[name])?.trim();
      if (value) {
        scopeName = name;
        scopeKey = value;
        break;
      }
    }
  }
  return {
    scope: scopeName === "" ? {} : { [scopeName]: scopeKey },
    scopeName,
    scopeKey,
    kind: single(params.kind)?.trim() ?? "",
    window: single(params.time_budget_ms)?.trim() ?? "",
    retryOf: single(params.retry_of)?.trim() ?? "",
  };
}

function Prompt(): JSX.Element {
  const now = new Date();
  const week = new Date(now.getTime() - 7 * 86_400_000);
  return (
    <>
      <h1>Compare two points in time</h1>
      <div className="card">
        <h2>Pick two instants</h2>
        <p>
          A comparison answers <em>what is different</em>, which is not the same question as{" "}
          <em>what happened</em>. An entry added and removed inside the interval appears in
          the <Link href="/changes">change list</Link> twice and in a comparison not at all.
        </p>
        <p>
          <Link href={hrefWith(PATH, { from: week.toISOString(), to: now.toISOString() })}>
            Compare now against a week ago
          </Link>
        </p>
        <p className="muted">
          Both instants must carry a UTC offset. An estate-wide comparison is limited to 400
          days apart; name a share or directory to compare any two instants.
        </p>
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
