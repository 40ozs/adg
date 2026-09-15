import type { JSX } from "react";
import Link from "next/link";

import { fetchChangeSummary, fetchChangeTimeline, fetchChanges } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { ChangeView } from "@/lib/contracts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation } from "@/lib/paging";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { ChangeSummaryStrip, ChangeTimeline } from "@/components/Changes";
import { PagePosition, Pager } from "@/components/Pager";
import { SignedOutNotice } from "@/components/SignedOutNotice";

const PATH = "/changes";

/** Windows an operator actually asks for, in days. */
const RANGES = [
  { label: "24 hours", days: 1 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
] as const;

const DEFAULT_DAYS = 7;

/**
 * What changed, and whether any of it is a problem.
 *
 * The page is a timeline because that is the shape of the question — *since Friday* — and it
 * is filtered by default because the alternative is not a neutral choice. An unfiltered feed
 * over a real estate is mostly display names and renumbered ACEs, and a reader who has to
 * skim past four hundred of those to find one Deny removal will stop reading the page.
 *
 * So the default shows permission changes and the ones ADG could not classify, and the
 * **summary above it says how many were hidden**. That number is the whole of the honesty
 * here: without it, a clean page is indistinguishable from a page that is clean because of a
 * default — which is this product's own failure mode applied to its own interface.
 *
 * Two things this page deliberately does not do. It does not call a first sighting a
 * creation: an estate's first scan produces one per object, they are excluded from the
 * default view and counted in the summary. And it does not show `at` as the time a change
 * happened; `at` is when a collector looked, and every row carries the window instead
 * (ADR-0019).
 */
export default async function ChangesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const kind = single(params.kind);
  const key = single(params.key);
  if (kind !== undefined && key !== undefined) {
    return <ObjectHistory token={viewer.session.accessToken} kind={kind} objectKey={key} />;
  }

  const days = readDays(single(params.days));
  const now = new Date();
  const to = now.toISOString();
  const from = new Date(now.getTime() - days * 86_400_000).toISOString();
  const scope = readScope(params);
  const everything = single(params.show) === "all";

  const query = {
    from,
    to,
    ...scope.query,
    ...(everything
      ? {
          significance: ["security", "metadata", "noise", "undetermined"],
          action: ["added", "modified", "removed", "first_observed"],
        }
      : {}),
  };

  const trail = decodeTrail(params.trail);
  const token = viewer.session.accessToken;
  const [feed, summary] = await Promise.all([
    fetchChanges(token, { ...query, cursor: cursorFor(trail) }),
    fetchChangeSummary(token, query),
  ]);

  const state = classify(feed, {
    isEmpty: (data) => data.changes.length === 0,
    subject: "changes",
  });

  const linkParams = { days, show: everything ? "all" : null, ...scope.link };
  const navigation = pageNavigation({
    basePath: PATH,
    params: linkParams,
    trailParam: "trail",
    trail,
    nextCursor: feed.ok ? feed.data.page.next_cursor : null,
  });

  return (
    <>
      <h1>Changes</h1>
      <p className="muted">
        Every change is bounded by the observations either side of it, never dated to the
        scan that found it.
      </p>

      <Filters days={days} everything={everything} scope={scope} />

      {summary.ok && <ChangeSummaryStrip summary={summary.data} />}

      <PagePosition trail={trail} firstPageHref={hrefWith(PATH, linkParams)} />

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <>
          {state.data.scan_exhausted && (
            <div className="banner banner-warning" role="status">
              <h2>This page is short for a reason its length does not show</h2>
              <p>
                The server stopped examining changes before the window ran out, so more exist
                beyond this page even though it holds fewer than a full one. Narrow the
                window or name a scope.
              </p>
            </div>
          )}
          {state.data.changes.length === 0 ? (
            <div className="card">
              <h2>Nothing matched</h2>
              <p>
                No change in this window matched the filters in use. That is not the same as
                a quiet window — the summary above counts everything that happened.
              </p>
            </div>
          ) : (
            <ChangeTimeline
              changes={state.data.changes}
              edits={state.data.edits}
              impactHref={impactHref}
              timelineHref={(change) => hrefWith(PATH, { kind: change.kind, key: change.key })}
            />
          )}
          <Pager navigation={navigation} page={state.data.page} subject="changes" />
        </>
      )}
    </>
  );
}

function impactHref(change: ChangeView): string {
  return hrefWith("/changes/impact", { kind: change.kind, key: change.key, at: change.at });
}

/** One object's whole history, as transitions rather than as versions. */
async function ObjectHistory({
  token,
  kind,
  objectKey,
}: {
  token: string;
  kind: string;
  objectKey: string;
}): Promise<JSX.Element> {
  const result = await fetchChangeTimeline(token, { kind, key: objectKey });
  const state = classify(result, {
    isEmpty: (data) => data.changes.length === 0,
    subject: "changes",
  });
  return (
    <>
      <h1>History</h1>
      <p className="muted">
        <code>{objectKey}</code>
      </p>
      <p>
        <Link href={PATH}>Back to changes</Link>
      </p>
      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : state.data.changes.length === 0 ? (
        <div className="card">
          <h2>No transitions recorded</h2>
          <p>
            ADG holds this object and has never seen it change. Its first version is where
            the record begins, which is not a creation — nothing here says when it was made.
          </p>
        </div>
      ) : (
        <>
          {state.data.truncated && (
            <div className="banner banner-info" role="status">
              <p>
                Older versions exist beyond what was read. This history is correct about the
                changes it holds and incomplete about what came before them.
              </p>
            </div>
          )}
          <ChangeTimeline
            changes={state.data.changes}
            edits={state.data.edits}
            impactHref={impactHref}
            timelineHref={() => hrefWith(PATH, { kind, key: objectKey })}
          />
        </>
      )}
    </>
  );
}

interface Scope {
  query: Record<string, string>;
  link: Record<string, string | null>;
  label: string | null;
}

/**
 * At most one scope, because the API accepts at most one.
 *
 * Two at once could mean their intersection or their union; the API refuses rather than
 * picking, so the page reads only the first one present in a fixed order rather than
 * sending both and rendering a 422 at the reader.
 */
function readScope(params: Record<string, string | string[] | undefined>): Scope {
  const names = ["server", "share", "directory", "principal", "group"] as const;
  for (const name of names) {
    const value = single(params[name])?.trim();
    if (value) {
      return {
        query: { [name]: value },
        link: { [name]: value },
        label: `${name}: ${value}`,
      };
    }
  }
  return { query: {}, link: {}, label: null };
}

function readDays(raw: string | undefined): number {
  const parsed = Number(raw);
  const match = RANGES.find((range) => range.days === parsed);
  return match ? match.days : DEFAULT_DAYS;
}

function Filters({
  days,
  everything,
  scope,
}: {
  days: number;
  everything: boolean;
  scope: Scope;
}): JSX.Element {
  const base = { ...scope.link };
  return (
    <nav className="filter-strip" aria-label="Filters">
      <p>
        Window:{" "}
        {RANGES.map((range) => (
          <span key={range.days}>
            {range.days === days ? (
              <strong>{range.label}</strong>
            ) : (
              <Link
                href={hrefWith(PATH, {
                  ...base,
                  days: range.days,
                  show: everything ? "all" : null,
                })}
              >
                {range.label}
              </Link>
            )}{" "}
          </span>
        ))}
      </p>
      <p>
        Showing:{" "}
        {everything ? (
          <>
            <Link href={hrefWith(PATH, { ...base, days })}>permission changes only</Link> ·{" "}
            <strong>everything</strong>
          </>
        ) : (
          <>
            <strong>permission changes only</strong> ·{" "}
            <Link href={hrefWith(PATH, { ...base, days, show: "all" })}>everything</Link>
          </>
        )}
      </p>
      {scope.label !== null && (
        <p>
          Scope: {scope.label} ·{" "}
          <Link href={hrefWith(PATH, { days, show: everything ? "all" : null })}>clear</Link>
        </p>
      )}
      <p className="muted">
        <Link href="/changes/compare">Compare two points in time</Link>
      </p>
    </nav>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
