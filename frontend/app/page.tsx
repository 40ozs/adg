import type { Metadata } from "next";
import { Suspense, use, type JSX } from "react";
import Link from "next/link";

import type { ApiResult } from "@/lib/api/client";
import type {
  ChangeSummaryResponse,
  ChangesResponse,
  CollectionStatus,
  QueueResponse,
  RiskRulesResponse,
  RiskSummaryResponse,
  RuntimeInfo,
} from "@/lib/contracts";

import {
  fetchChangeSummary,
  fetchChanges,
  fetchCollectionStatus,
  fetchReviewerQueue,
  fetchRiskRules,
  fetchRiskSummary,
  fetchRuntime,
} from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { RoleAdviceBanner } from "@/components/Banners";
import {
  DataHealth,
  NeedsAttention,
  RecentChanges,
  Reviews,
  evidenceNeedsAttention,
} from "@/components/Overview";
import { RecentlyViewed } from "@/components/RecentlyViewed";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { sectionTitle, visibleNavItems } from "@/lib/nav";
import { recentOwner } from "@/lib/recent-owner";

import styles from "@/components/overview.module.css";

export const metadata: Metadata = { title: sectionTitle("overview") };

/** The change window the Overview counts, in days: the Changes page's own default. */
const CHANGE_DAYS = 7;

/** Recent changes fetched for the section; pairs of one ACL edit collapse into one line. */
const CHANGE_FEED_LIMIT = 6;

/**
 * Overview: what needs attention, what changed, what is waiting, and whether to trust it.
 *
 * An operational starting point rather than a dashboard (ADG-UX-06). Each section is a few
 * counts the API returned, the caveat that decides how to read them, and a link to the
 * records behind them. A section the account cannot open is not rendered, so nothing here
 * is an empty card standing in for a missing role.
 *
 * Data health moves to the top when collection or evaluation is incomplete. A dashboard that
 * opens with "0 critical findings" over a failed scan says the one sentence this product
 * must never say; ADG opens by saying how much of the estate has actually been looked at.
 */
export default async function OverviewPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const held = new Set(viewer.principal.capabilities);
  const can = (capability: string) => held.has(capability);
  const now = new Date();
  const window = {
    from: new Date(now.getTime() - CHANGE_DAYS * 86_400_000).toISOString(),
    to: now.toISOString(),
  };

  // Independent reads, all started here, in parallel; a section the account cannot open is
  // never requested. Each section waits for its own reads only (ADG-UX-PERF-01), so one slow
  // summary holds back its own section rather than the whole page.
  const status = fetchCollectionStatus(token);
  const runtime = fetchRuntime();
  const risk = can("risks:read") ? fetchRiskSummary(token) : null;
  const rules = can("risks:read") ? fetchRiskRules(token) : null;
  const changes = can("changes:read")
    ? {
        summary: fetchChangeSummary(token, window),
        feed: fetchChanges(token, { ...window, limit: CHANGE_FEED_LIMIT }),
      }
    : null;
  const queue = can("governance:read") ? fetchReviewerQueue(token) : null;

  const health = { status, risk, runtime, canOpenCollectors: can("collectors:read") };
  const sections = visibleNavItems(viewer.principal.capabilities).filter(
    (item) => item.id !== "overview",
  );

  return (
    <>
      <h1>Overview</h1>

      <RoleAdviceBanner principal={viewer.principal} />

      {/* Data health has two places and renders in exactly one. Until its reads say which,
          the top one holds it: the caveat is assumed to come first until it is known not to. */}
      <Suspense fallback={<Pending id="health" title="Data health" what="collection status" />}>
        <HealthSlot first {...health} />
      </Suspense>

      {risk !== null && rules !== null && (
        <Suspense fallback={<Pending id="attention" title="Needs attention" what="findings" />}>
          <AttentionSlot risk={risk} rules={rules} />
        </Suspense>
      )}

      {(changes !== null || queue !== null) && (
        <div className={styles.pair}>
          {changes !== null && (
            <Suspense fallback={<Pending id="changes" title="Recent changes" what="changes" />}>
              <ChangesSlot {...changes} status={status} />
            </Suspense>
          )}
          {queue !== null && (
            <Suspense fallback={<Pending id="reviews" title="Reviews" what="your review queue" />}>
              <ReviewsSlot queue={queue} />
            </Suspense>
          )}
        </div>
      )}

      <Suspense fallback={null}>
        <HealthSlot first={false} {...health} />
      </Suspense>

      {/* Personal, filled from this tab's storage after hydration: outside every Suspense slot,
          and below them, so it neither waits for nor shifts the operational sections. */}
      {(can("identities:read") || can("resources:read")) && (
        <RecentlyViewed
          owner={recentOwner(viewer.principal.subject)}
          capabilities={viewer.principal.capabilities}
        />
      )}

      <div className="card">
        <h2>Where to start</h2>
        <p className="muted">
          Sections this account can open. Anything missing here needs a role an
          administrator assigns.
        </p>
        <dl className="facts">
          {sections.map((item) => (
            <div key={item.id} style={{ display: "contents" }}>
              <dt>
                <Link href={item.href}>{item.label}</Link>
              </dt>
              <dd>{item.description}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="card">
        <h2>What ADG will not do</h2>
        <p>
          ADG is read-only. It never writes an ACL, never changes a group, and never deletes
          data on a target system. Remediation is a reserved capability that no role grants.
        </p>
      </div>
    </>
  );
}

type Read<T> = Promise<ApiResult<T>>;

/*
 * Each slot reads its own promises with `use`, inside its own Suspense boundary: the server
 * streams the rest of the page and fills a slot in when its reads settle. A failure or a
 * timeout is an ApiResult like any other, so it lands in the section's own state message.
 */

function HealthSlot({
  first,
  status,
  risk,
  runtime,
  canOpenCollectors,
}: {
  first: boolean;
  status: Read<CollectionStatus>;
  risk: Read<RiskSummaryResponse> | null;
  runtime: Read<RuntimeInfo>;
  canOpenCollectors: boolean;
}): JSX.Element | null {
  const statusResult = use(status);
  const riskResult = risk === null ? null : use(risk);
  const runtimeResult = use(runtime);
  if (evidenceNeedsAttention(statusResult, riskResult) !== first) {
    return null;
  }
  return (
    <DataHealth
      status={statusResult}
      risk={riskResult?.ok ? riskResult.data : null}
      runtime={runtimeResult.ok ? runtimeResult.data : null}
      canOpenCollectors={canOpenCollectors}
    />
  );
}

function AttentionSlot({
  risk,
  rules,
}: {
  risk: Read<RiskSummaryResponse>;
  rules: Read<RiskRulesResponse>;
}): JSX.Element {
  const riskResult = use(risk);
  const rulesResult = use(rules);
  return <NeedsAttention result={riskResult} rules={rulesResult.ok ? rulesResult.data.rules : []} />;
}

function ChangesSlot({
  summary,
  feed,
  status,
}: {
  summary: Read<ChangeSummaryResponse>;
  feed: Read<ChangesResponse>;
  status: Read<CollectionStatus>;
}): JSX.Element {
  const summaryResult = use(summary);
  const feedResult = use(feed);
  // An empty window is worded against collection, so this section also waits for it.
  const statusResult = use(status);
  return (
    <RecentChanges
      summary={summaryResult}
      feed={feedResult}
      collection={statusResult.ok ? statusResult.data : null}
      days={CHANGE_DAYS}
    />
  );
}

function ReviewsSlot({ queue }: { queue: Read<QueueResponse> }): JSX.Element {
  return <Reviews result={use(queue)} />;
}

/**
 * A section whose reads have not settled: its heading, so the region is already named, and a
 * sentence that says loading in words. No number, and nothing that could be read as empty.
 */
function Pending({ id, title, what }: { id: string; title: string; what: string }): JSX.Element {
  return (
    <section className="card" aria-labelledby={`overview-${id}-pending`} aria-busy="true">
      <h2 id={`overview-${id}-pending`}>{title}</h2>
      <p className="muted">Loading {what}…</p>
    </section>
  );
}
