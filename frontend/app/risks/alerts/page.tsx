import type { JSX } from "react";
import Link from "next/link";

import { fetchAlert, fetchAlertQueue, fetchAlerts, fetchWatches } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { AlertView } from "@/lib/contracts";
import {
  deliveryDestinationNotice,
  queueNotice,
  statusTone,
  statusWording,
  suppressionNotice,
  suppressionReasonWording,
  triggerWording,
} from "@/lib/alerts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation } from "@/lib/paging";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { PagePosition, Pager } from "@/components/Pager";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { WatchManager } from "@/components/Watches";

const PATH = "/risks/alerts";

/**
 * Alerts: what ADG told somebody, what it held back, and whether any of it arrived.
 *
 * The three things above the feed are not decoration. An alert feed's failure mode is that
 * it goes quiet and nobody can tell whether the estate did or the pipeline did, so this page
 * answers that before it shows a single alert:
 *
 * - **The delivery queue.** Depth, staleness and abandonment. An abandoned delivery is an
 *   alert somebody was meant to receive and did not; a stale one usually means nothing is
 *   draining the queue.
 * - **Whether there is anywhere to deliver.** An installation with every destination
 *   disabled has a queue that only grows and no other field would say why.
 * - **The watches.** With none configured, three of the four triggers produce nothing at
 *   all — which is a configuration state and not a quiet estate.
 *
 * Unlike the risk report, the default here is **every status**. A transient alert never
 * resolves — an access control list edit cannot un-happen — so filtering to open alerts by
 * default would be a filter that does nothing for three of the four triggers while looking
 * like it does something.
 */
export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const canManage = viewer.principal.capabilities.includes("alerts:manage");
  const trigger = many(params.trigger);
  const openKey = single(params.alert) ?? null;
  const trail = decodeTrail(params.trail);

  const [feed, queue, watches, detail] = await Promise.all([
    fetchAlerts(token, {
      ...(trigger.length > 0 ? { trigger } : {}),
      cursor: cursorFor(trail),
    }),
    fetchAlertQueue(token),
    fetchWatches(token),
    openKey !== null ? fetchAlert(token, openKey) : Promise.resolve(null),
  ]);

  // Never "empty" for `classify`'s purposes, for the reason the risks page gives: the
  // question "can this emptiness be believed?" is answered here by the delivery queue and
  // the watch list, not by whether a collector ran.
  const state = classify(feed, { isEmpty: () => false, subject: "alerts" });

  const linkParams = { trigger };
  const navigation = pageNavigation({
    basePath: PATH,
    params: linkParams,
    trailParam: "trail",
    trail,
    nextCursor: feed.ok ? feed.data.page.next_cursor : null,
  });

  const queueState = queue.ok ? queueNotice(queue.data) : null;
  const nowhere = queue.ok ? deliveryDestinationNotice(queue.data) : null;

  return (
    <>
      <h1>Alerts</h1>
      <p className="muted">
        <Link href="/risks">Back to risks</Link>
      </p>

      {queueState !== null && (
        <div
          className={`banner ${
            queueState.tone === "error"
              ? "banner-error"
              : queueState.tone === "warning"
                ? "banner-warning"
                : "banner-info"
          }`}
          role="status"
        >
          <h2>{queueState.headline}</h2>
          <p>{queueState.explanation}</p>
          {nowhere !== null && <p>{nowhere}</p>}
        </div>
      )}

      {watches.ok && (
        <WatchManager
          watches={watches.data.watches}
          kinds={watches.data.kinds}
          defaultCooldownSeconds={watches.data.default_cooldown_seconds}
          canManage={canManage}
        />
      )}

      <nav className="filter-strip" aria-label="Filters">
        <p>
          Trigger:{" "}
          {trigger.length === 0 ? (
            <strong aria-current="true">any</strong>
          ) : (
            <Link href={PATH}>any</Link>
          )}
          {feed.ok &&
            Object.entries(feed.data.trigger_counts).map(([name, count]) => (
              <span key={name}>
                {" · "}
                {trigger.includes(name) ? (
                  <strong aria-current="true">
                    {triggerWording(name)} ({count})
                  </strong>
                ) : (
                  <Link href={hrefWith(PATH, { trigger: [...trigger, name] })}>
                    {triggerWording(name)} ({count})
                  </Link>
                )}
              </span>
            ))}
        </p>
      </nav>

      <PagePosition trail={trail} firstPageHref={hrefWith(PATH, linkParams)} />

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : state.data.items.length === 0 ? (
        <div className="card">
          <h2>No alerts</h2>
          <p>
            Nothing has been raised. Read that together with the delivery queue above and the
            watches: with no watch configured, three of the four triggers cannot fire at all.
          </p>
        </div>
      ) : (
        <>
          <AlertTable
            alerts={state.data.items}
            detailHref={(alert) => hrefWith(PATH, { trigger, alert: alert.alert_key })}
          />
          <Pager navigation={navigation} page={state.data.page} subject="alerts" />
        </>
      )}

      {detail !== null && detail.ok && (
        <section className="card" aria-label="Alert detail">
          <h2>{detail.data.alert.summary}</h2>
          <p className="muted">
            {detail.data.alert.trigger_description} ·{" "}
            <Link href={hrefWith(PATH, { trigger })}>close</Link>
          </p>

          <h3>Every occurrence</h3>
          <p className="muted">
            Including the ones that were not delivered. A cooldown that left no record would
            be indistinguishable afterwards from a pipeline that dropped them.
          </p>
          <div className="table-scroll">
            <table className="acl-table">
              <caption className="visually-hidden">Occurrences of this alert</caption>
              <thead>
                <tr>
                  <th scope="col">What happened</th>
                  <th scope="col">When</th>
                  <th scope="col">Delivered</th>
                  <th scope="col">Stands for</th>
                  <th scope="col">Why not</th>
                </tr>
              </thead>
              <tbody>
                {detail.data.events.map((event) => (
                  <tr key={event.event_id}>
                    <td>{event.transition}</td>
                    <td className="muted">{event.occurred_at}</td>
                    <td className={event.notified ? "status-ok" : "status-warn"}>
                      {event.notified ? "yes" : "no"}
                    </td>
                    <td>{event.folds}</td>
                    <td className="muted">
                      {suppressionReasonWording(event.suppression_reason) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3>Where it went</h3>
          {detail.data.deliveries.length === 0 ? (
            <p className="muted">
              Nothing was queued for delivery, which means every occurrence was suppressed.
            </p>
          ) : (
            <ul>
              {detail.data.deliveries.map((delivery) => (
                <li key={delivery.delivery_id}>
                  <strong>{delivery.sink_name}</strong> — {delivery.status} after{" "}
                  {delivery.attempts} attempt(s)
                  {delivery.last_error !== null && (
                    <>
                      {" "}
                      · <span className="status-bad">{delivery.last_error}</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}

function AlertTable({
  alerts,
  detailHref,
}: {
  alerts: AlertView[];
  detailHref: (alert: AlertView) => string;
}): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption className="visually-hidden">Alerts, newest first</caption>
        <thead>
          <tr>
            <th scope="col">State</th>
            <th scope="col">What</th>
            <th scope="col">Watch</th>
            <th scope="col">Last raised</th>
            <th scope="col">Occurrences</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => {
            const held = suppressionNotice(alert);
            return (
              <tr key={alert.alert_key}>
                <td>
                  <span className={`badge status-${statusTone(alert)}`}>
                    {statusWording(alert)}
                  </span>
                </td>
                <td>
                  <Link href={detailHref(alert)}>{alert.summary}</Link>
                  <br />
                  <span className="muted">{triggerWording(alert.trigger)}</span>
                </td>
                <td className="muted">{alert.watch_label ?? "estate-wide"}</td>
                <td className="muted">{alert.last_raised_at}</td>
                <td>
                  {alert.occurrence_count}
                  {held !== null && (
                    <>
                      <br />
                      <span className="status-warn" title={held}>
                        {alert.suppressed_total} not delivered
                      </span>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function many(value: string | string[] | undefined): string[] {
  if (value === undefined) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}
