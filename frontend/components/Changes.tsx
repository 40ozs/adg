import type { JSX } from "react";
import Link from "next/link";

import type {
  AceEditView,
  ChangeSummaryResponse,
  ChangeView,
  FieldDeltaView,
} from "@/lib/contracts";
import {
  actionWording,
  directionWording,
  exclusionNotice,
  groupChanges,
  headline,
  impactCaveat,
  kindWording,
  reconstructionCaveat,
  renderValue,
  severityTone,
  significanceWording,
  truncationNotice,
  visibleDeltas,
  windowSpan,
  windowWording,
} from "@/lib/changes";

/**
 * Rendering a change so that the three things an operator asks are answerable by looking.
 *
 * *What happened*, *when*, and *does it matter* — in that order, and none of them decided
 * here. Every judgment comes off the response; this file chooses layout and wording.
 *
 * The two renderings that would be wrong in a way nobody would notice:
 *
 * - **The time column is the window, not `at`.** `at` is when a collector looked. Showing
 *   it as the change time dates every incident to a scan schedule, which is the model
 *   ADR-0019 rejected. The window is shown with both ends and the width of the ignorance.
 * - **The two halves of an ACL edit are shown together.** An entry rewritten from Full
 *   Control to Read & Execute is stored as a removal plus an addition; shown apart, a
 *   reader sees two unrelated events and acts on at most one.
 */

export function SeverityBadge({ severity }: { severity: ChangeView["severity"] }): JSX.Element {
  const tone = severityTone(severity);
  return (
    <span className={`badge status-${tone}`} title={`Severity: ${severity}`}>
      {severity}
    </span>
  );
}

/**
 * The counts above a filtered list.
 *
 * The number that matters is `excluded`. Without it, a page that is clean because of a
 * default is indistinguishable from a quiet week — which is this product's own failure
 * mode, turned on its own interface.
 */
export function ChangeSummaryStrip({
  summary,
}: {
  summary: ChangeSummaryResponse;
}): JSX.Element {
  const hidden = exclusionNotice(summary);
  const truncated = truncationNotice(summary);
  return (
    <section className="card" aria-label="Summary">
      <h2>In this window</h2>
      <p>
        <strong>{summary.total}</strong> changes recorded,{" "}
        <strong>{summary.returned}</strong> shown.
        {summary.highest_severity !== null && (
          <>
            {" "}
            Most severe: <SeverityBadge severity={summary.highest_severity} />.
          </>
        )}
      </p>
      {hidden !== null && (
        <p className="muted" role="status">
          {hidden}
        </p>
      )}
      {summary.reconstructed > 0 && (
        <p className="muted">
          {summary.reconstructed} rest on a version reconstructed when history was
          introduced, which conceals anything that happened inside its interval.
        </p>
      )}
      {truncated !== null && (
        <div className="banner banner-warning" role="status">
          <p>{truncated}</p>
        </div>
      )}
      <CountTable heading="By severity" counts={summary.by_severity} />
      <CountTable heading="By what happened" counts={summary.by_action} labels={actionLabel} />
      <CountTable heading="By kind" counts={summary.by_kind} labels={kindWording} />
    </section>
  );
}

function actionLabel(action: string): string {
  return actionWording(action as ChangeView["action"]).label;
}

function CountTable({
  heading,
  counts,
  labels,
}: {
  heading: string;
  counts: Record<string, number>;
  labels?: (key: string) => string;
}): JSX.Element | null {
  const rows = Object.entries(counts).filter(([, value]) => value > 0);
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="acl-group">
      <h3>{heading}</h3>
      <ul>
        {rows.map(([name, value]) => (
          <li key={name}>
            {labels ? labels(name) : name}: {value}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The feed itself, as a timeline.
 *
 * Grouped so an ACL rewrite is one entry with two rows inside it, and never re-sorted:
 * the response is newest first, and a list that quietly reorders makes "what happened most
 * recently" unanswerable by looking at the top.
 */
export function ChangeTimeline({
  changes,
  edits,
  impactHref,
  timelineHref,
}: {
  changes: readonly ChangeView[];
  edits: readonly AceEditView[];
  /** Builds the "why access changed" link for one change, or null to omit it. */
  impactHref: ((change: ChangeView) => string) | null;
  timelineHref: (change: ChangeView) => string;
}): JSX.Element {
  const groups = groupChanges(changes, edits);
  return (
    <ol className="acl-group" aria-label="Changes">
      {groups.map((group, index) => (
        <li key={`${group.edit?.index ?? "solo"}-${index}`}>
          {group.edit !== null && (
            <p>
              <strong>{group.edit.summary}</strong>{" "}
              <SeverityBadge severity={group.edit.severity} />{" "}
              <span className="badge">{directionWording(group.edit.direction)}</span>
            </p>
          )}
          {group.changes.map((change) => (
            <ChangeCard
              key={`${change.kind}:${change.key}:${change.at}`}
              change={change}
              edits={edits}
              impactHref={impactHref}
              timelineHref={timelineHref}
              inEdit={group.edit !== null}
            />
          ))}
        </li>
      ))}
    </ol>
  );
}

export function ChangeCard({
  change,
  edits,
  impactHref,
  timelineHref,
  inEdit = false,
}: {
  change: ChangeView;
  edits: readonly AceEditView[];
  impactHref: ((change: ChangeView) => string) | null;
  timelineHref: (change: ChangeView) => string;
  inEdit?: boolean;
}): JSX.Element {
  const action = actionWording(change.action);
  const reconstruction = reconstructionCaveat(change);
  return (
    <article className="card">
      <h3>
        {action.label} — {kindWording(change.kind)} <SeverityBadge severity={change.severity} />
      </h3>
      <p className="muted">
        <code>{change.key}</code>
      </p>
      {!inEdit && <p>{headline(change, edits)}</p>}
      <p className="verdict-note">
        <span className="badge">{significanceWording(change.significance)}</span>{" "}
        <span className="badge">{directionWording(change.direction)}</span>{" "}
        <span className="badge" title="How wide the uncertainty about when is">
          ±{windowSpan(change.window)}
        </span>
      </p>
      <p>{windowWording(change.window)}</p>
      <p className="muted">
        Recorded at {change.at}, which is when a collector looked rather than when the
        change happened.
      </p>
      {change.action === "first_observed" && <p className="muted">{action.explanation}</p>}
      {change.action === "removed" && <p className="muted">{action.explanation}</p>}
      {reconstruction !== null && (
        <div className="banner banner-info" role="status">
          <p>{reconstruction}</p>
        </div>
      )}
      {change.subject.container_key !== null && (
        <p className="muted">
          On <code>{change.subject.container_key}</code>
          {change.subject.related_key !== null && (
            <>
              {" "}
              for <code>{change.subject.related_key}</code>
            </>
          )}
        </p>
      )}
      <DeltaTable deltas={change.deltas} />
      <BeforeAfter change={change} />
      <p>
        <Link href={timelineHref(change)}>Full history of this object</Link>
        {impactHref !== null && (
          <>
            {" · "}
            <Link href={impactHref(change)}>Why access changed</Link>
          </>
        )}
      </p>
      {change.rule_ids.length > 0 && (
        <p className="muted">Rule: {change.rule_ids.join(", ")}</p>
      )}
    </article>
  );
}

/**
 * The diff view: one row per field that moved.
 *
 * Provenance-only differences are dropped — every superseding observation carries a new
 * one, and a diff whose first row is always "source_key changed" trains the reader to skim.
 * Nothing else is hidden: a field ADG could not classify is shown, labelled, and is the
 * first thing worth looking at.
 */
export function DeltaTable({ deltas }: { deltas: readonly FieldDeltaView[] }): JSX.Element | null {
  const rows = visibleDeltas(deltas);
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption>What moved</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Before</th>
            <th scope="col">After</th>
            <th scope="col">Means</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((delta) => (
            <tr key={delta.field}>
              <th scope="row">{delta.field}</th>
              <td>{renderValue(delta.before)}</td>
              <td>{renderValue(delta.after)}</td>
              <td>{delta.significance}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The two states either side of a change, as they were stored.
 *
 * A removal shows a present state on the left and nothing on the right, and the right-hand
 * side says *measured absence* rather than being left blank: a blank cell reads as "we have
 * no idea", which is the one thing a tombstone is not.
 */
export function BeforeAfter({ change }: { change: ChangeView }): JSX.Element | null {
  if (change.before === null && change.after.state === null) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption>Before and after</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Before</th>
            <th scope="col">After</th>
          </tr>
        </thead>
        <tbody>
          {fieldNames(change).map((name) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td>
                {change.before === null ? (
                  <span className="muted">not recorded</span>
                ) : (
                  renderValue(change.before.state?.[name])
                )}
              </td>
              <td>
                {change.after.state === null ? (
                  <span className="muted">gone — a scan looked and did not find it</span>
                ) : (
                  renderValue(change.after.state[name])
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fieldNames(change: ChangeView): string[] {
  const names = new Set<string>();
  for (const state of [change.before?.state, change.after.state]) {
    if (state) {
      for (const name of Object.keys(state)) {
        names.add(name);
      }
    }
  }
  return [...names].sort();
}

/** The caveat that must accompany an inconclusive access delta. See `impactCaveat`. */
export function ImpactCaveat({
  direction,
  conclusive,
}: {
  direction: ChangeView["direction"];
  conclusive: boolean;
}): JSX.Element | null {
  const text = impactCaveat(direction, conclusive);
  if (text === null) {
    return null;
  }
  return (
    <div className="banner banner-warning" role="status">
      <h3>Not conclusive</h3>
      <p>{text}</p>
    </div>
  );
}
