import Link from "next/link";

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
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { visibleNavItems } from "@/lib/nav";

import styles from "@/components/overview.module.css";

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

  // Independent reads, in parallel; a section the account cannot open is never requested.
  const [status, runtime, risk, rules, changeSummary, changeFeed, queue] = await Promise.all([
    fetchCollectionStatus(token),
    fetchRuntime(),
    can("risks:read") ? fetchRiskSummary(token) : null,
    can("risks:read") ? fetchRiskRules(token) : null,
    can("changes:read") ? fetchChangeSummary(token, window) : null,
    can("changes:read") ? fetchChanges(token, { ...window, limit: CHANGE_FEED_LIMIT }) : null,
    can("governance:read") ? fetchReviewerQueue(token) : null,
  ]);

  const health = (
    <DataHealth
      status={status}
      risk={risk?.ok ? risk.data : null}
      runtime={runtime.ok ? runtime.data : null}
      canOpenCollectors={can("collectors:read")}
    />
  );
  const healthFirst = evidenceNeedsAttention(status, risk);
  const sections = visibleNavItems(viewer.principal.capabilities).filter(
    (item) => item.id !== "overview",
  );

  return (
    <>
      <h1>Overview</h1>

      <RoleAdviceBanner principal={viewer.principal} />

      {healthFirst && health}

      {risk !== null && <NeedsAttention result={risk} rules={rules?.ok ? rules.data.rules : []} />}

      {(changeSummary !== null || queue !== null) && (
        <div className={styles.pair}>
          {changeSummary !== null && changeFeed !== null && (
            <RecentChanges
              summary={changeSummary}
              feed={changeFeed}
              collection={status.ok ? status.data : null}
              days={CHANGE_DAYS}
            />
          )}
          {queue !== null && <Reviews result={queue} />}
        </div>
      )}

      {!healthFirst && health}

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
