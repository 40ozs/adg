/**
 * The views an access review is read through.
 *
 * Server components: nothing here holds state, and nothing here decides anything. Every
 * verdict, sentence and number is the backend's, and the module that chooses wording is
 * `lib/governance.ts`. The two interactive pieces — the decision form and the bulk bar —
 * are client components in `components/DecisionForm.tsx`.
 *
 * The layout is shaped by one rule: **the frozen grant is the subject and everything else
 * sits beside it.** A reviewer is answering about what was true at the baseline, so the
 * evidence comes first and the current state, the drift verdict, the access answers and the
 * risk findings are context around it — never in its place, and never merged with it.
 */

import type { JSX } from "react";
import Link from "next/link";

import type {
  CampaignStatusResponse,
  CampaignView,
  DecisionView,
  DriftView,
  GrantView,
  ItemContextResponse,
  ItemView,
  QueueEntryView,
  RelatedFindingView,
  ReviewerProgressView,
  RightsView,
} from "@/lib/contracts";
import {
  coverageCaveat,
  decisionLabel,
  driftHeadline,
  driftTone,
  dueWording,
  principalLabel,
  queueHeadline,
  reachVerdict,
  reviewersByAttention,
  targetLabel,
  type Tone,
} from "@/lib/governance";

const TONE_CLASS: Record<Tone, string> = {
  ok: "status-ok",
  warn: "status-warn",
  bad: "status-bad",
  info: "muted",
};

function instant(value: string): string {
  return new Date(value).toISOString().replace("T", " ").slice(0, 16);
}

/* ------------------------------------------------------------------------- campaigns */

export function CampaignTable({ campaigns }: { campaigns: readonly CampaignView[] }): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption className="muted">
          Campaigns, newest first. A campaign is frozen against an instant, so what a
          reviewer certifies is what was true then, whatever the estate does afterwards.
        </caption>
        <thead>
          <tr>
            <th scope="col">Campaign</th>
            <th scope="col">Status</th>
            <th scope="col">Frozen against</th>
            <th scope="col">Due</th>
            <th scope="col">Items</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((campaign) => (
            <tr key={campaign.campaign_id}>
              <th scope="row">
                <Link href={`/governance/campaigns/${campaign.campaign_id}`}>
                  {campaign.name}
                </Link>
                <div className="muted">
                  {campaign.focus === "principal" ? "By principal" : "By resource"} ·{" "}
                  {campaign.scopes.map((scope) => `${scope.kind}:${scope.key}`).join(", ")}
                </div>
              </th>
              <td>{campaign.status}</td>
              <td>{instant(campaign.baseline_at)}</td>
              <td>{campaign.due_at === null ? "—" : instant(campaign.due_at)}</td>
              <td>{campaign.item_count.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A campaign's progress, with the two things that must never be buried beside it.
 *
 * `unassigned_items` is reported on its own line rather than inside the pending total: an
 * item nobody was asked about can never be decided, so a campaign with three thousand of
 * them is stuck rather than slow, and a single "pending" number cannot tell a reader which.
 *
 * The exclusion caveat sits directly under the completion figure for the same reason — "47
 * of 47 certified" must not be readable as coverage of everything when the campaign
 * deliberately skipped inherited entries and well-known trustees.
 */
export function CampaignProgress({
  status,
}: {
  status: CampaignStatusResponse;
}): JSX.Element {
  const caveat = coverageCaveat(status.campaign.excluded_counts);
  const percent = Math.round(status.completion * 100);
  return (
    <section className="card" aria-labelledby="campaign-progress">
      <h2 id="campaign-progress">Progress</h2>
      <p>
        <strong>
          {status.decided_items.toLocaleString()} of {status.total_items.toLocaleString()} decided
        </strong>{" "}
        ({percent}%)
      </p>
      {caveat !== null ? <p className="verdict verdict-info">{caveat}</p> : null}
      {status.unassigned_items > 0 ? (
        <p className="verdict verdict-bad">
          {status.unassigned_items.toLocaleString()} items are assigned to nobody and can never
          be decided. This campaign is stuck, not slow: a governance administrator has to
          assign a reviewer to their scope.
        </p>
      ) : null}
      {status.overdue ? (
        <p className="verdict verdict-bad">
          This campaign is past its due date with work outstanding.
        </p>
      ) : null}
      {status.late_decisions > 0 ? (
        <p className="muted">
          {status.late_decisions.toLocaleString()} decisions were recorded after their
          reviewer&rsquo;s deadline.
        </p>
      ) : null}
      <dl>
        {Object.entries(status.decisions_by_kind)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([kind, count]) => (
            <div key={kind}>
              <dt>{decisionLabel(kind)}</dt>
              <dd>{count.toLocaleString()}</dd>
            </div>
          ))}
      </dl>
    </section>
  );
}

/** Who is behind, and by how much. Overdue first; every number is the backend's. */
export function ReviewerProgressTable({
  reviewers,
}: {
  reviewers: readonly ReviewerProgressView[];
}): JSX.Element {
  const ordered = reviewersByAttention(reviewers);
  if (ordered.length === 0) {
    return (
      <p className="verdict verdict-bad">
        Nobody has been asked to review this campaign. Until somebody is, none of its items
        can be decided.
      </p>
    );
  }
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption className="muted">Reviewers, those behind first.</caption>
        <thead>
          <tr>
            <th scope="col">Reviewer</th>
            <th scope="col">Scope</th>
            <th scope="col">Decided</th>
            <th scope="col">Left</th>
            <th scope="col">Due</th>
            <th scope="col">Late</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((reviewer) => (
            <tr key={reviewer.assignment_id}>
              <th scope="row">
                {reviewer.reviewer_display_name ?? reviewer.reviewer_subject}
                {reviewer.overdue ? (
                  <span className="badge status-bad"> overdue</span>
                ) : null}
              </th>
              <td>
                {reviewer.scope === null
                  ? "the whole campaign"
                  : `${reviewer.scope.kind}:${reviewer.scope.key}`}
              </td>
              <td>
                {reviewer.decided.toLocaleString()} / {reviewer.assigned.toLocaleString()}
              </td>
              <td>{reviewer.pending.toLocaleString()}</td>
              <td>{dueWording(reviewer.due_at, reviewer.overdue)}</td>
              <td>
                {reviewer.late_decisions === 0 ? "—" : reviewer.late_decisions.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ----------------------------------------------------------------------------- queue */

export function ReviewerQueue({
  entries,
}: {
  entries: readonly QueueEntryView[];
}): JSX.Element {
  return (
    <ul className="acl-group">
      {entries.map((entry) => (
        <li key={entry.campaign_id}>
          <h3>
            <Link href={`/governance/campaigns/${entry.campaign_id}?mine=1`}>{entry.name}</Link>
          </h3>
          <p className={entry.overdue ? "status-bad" : undefined}>
            {queueHeadline(entry)} · {dueWording(entry.due_at, entry.overdue)}
          </p>
          <p className="muted">
            Frozen against {instant(entry.baseline_at)}. {entry.decided.toLocaleString()} of{" "}
            {entry.assigned.toLocaleString()} answered.
          </p>
        </li>
      ))}
    </ul>
  );
}

/* ----------------------------------------------------------------------------- drift */

/**
 * What became of one grant, rendered from the sentence the API sent.
 *
 * `drift.summary` is printed rather than reassembled here. It is the one place in this
 * product where "it was removed" and "nobody has looked" have to stay apart, and a client
 * that rebuilt the sentence from the verdict and the counts would be a second implementation
 * of that distinction.
 */
export function DriftNotice({ drift }: { drift: DriftView }): JSX.Element {
  const tone = driftTone(drift.verdict);
  return (
    <section className="card" aria-labelledby="drift">
      <h2 id="drift" className={TONE_CLASS[tone]}>
        {driftHeadline(drift)}
      </h2>
      <p>{drift.summary}</p>
      {drift.changes.length > 0 ? (
        <table className="acl-table">
          <caption className="muted">
            Compared against the estate at {instant(drift.compared_at)}. The frozen evidence
            above is unchanged and is what you are deciding.
          </caption>
          <thead>
            <tr>
              <th scope="col">Change</th>
              <th scope="col">What moved</th>
              <th scope="col">Entry</th>
            </tr>
          </thead>
          <tbody>
            {drift.changes.map((change) => (
              <tr key={`${change.kind}-${change.ace_key}`}>
                <th scope="row">{change.kind}</th>
                <td>
                  {change.field_labels.length === 0
                    ? "the whole entry"
                    : change.field_labels.join(", ")}
                </td>
                <td className="principal-sid">{change.ace_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {drift.target_certainty !== null && drift.target_certainty !== "observed" ? (
        <p className="muted">
          ADG&rsquo;s reading of this target is {drift.target_certainty}, so this compares the
          baseline against the last scan rather than against the estate.
        </p>
      ) : null}
    </section>
  );
}

/** A campaign-wide drift banner. Shows coverage, because the comparison is a page. */
export function DriftSummary({
  counts,
  covered,
  total,
  campaignId,
}: {
  counts: { unchanged: number; modified: number; removed: number; unobserved: number };
  covered: number;
  total: number;
  campaignId: string;
}): JSX.Element {
  const moved = counts.modified + counts.removed;
  return (
    <section className="card" aria-labelledby="drift-summary">
      <h2 id="drift-summary" className={moved > 0 ? "status-warn" : "status-ok"}>
        {moved === 0
          ? "Nothing in this campaign has changed since it was frozen"
          : `${moved.toLocaleString()} items have changed since this campaign was frozen`}
      </h2>
      <p className="muted">
        {covered.toLocaleString()} of {total.toLocaleString()} items compared. Drift is
        computed against live state on each request, so this covers a page rather than the
        whole campaign.
      </p>
      <p>
        {counts.modified.toLocaleString()} changed · {counts.removed.toLocaleString()} no
        longer exist · {counts.unobserved.toLocaleString()} ADG cannot currently say about ·{" "}
        {counts.unchanged.toLocaleString()} unchanged.
      </p>
      <p>
        <Link href={`/governance/campaigns/${campaignId}?view=drift`}>
          Review the items that moved
        </Link>
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------------- the item */

export function GrantEvidenceTable({
  grants,
  caption,
}: {
  grants: readonly GrantView[];
  caption: string;
}): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption className="muted">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Type</th>
            <th scope="col">Rights</th>
            <th scope="col">Set here</th>
            <th scope="col">Observed</th>
            <th scope="col">Certainty</th>
          </tr>
        </thead>
        <tbody>
          {grants.map((grant) => (
            <tr key={grant.ace_key} className={grant.deny ? "ace-deny" : undefined}>
              <th scope="row">
                <span className={grant.deny ? "badge badge-deny" : "badge badge-allow"}>
                  {grant.ace_type}
                </span>
              </th>
              <td>
                {grant.permission ??
                  (grant.access_mask === null
                    ? "—"
                    : `0x${grant.access_mask.toString(16).padStart(8, "0")}`)}
              </td>
              <td>
                <span
                  className={grant.inherited ? "badge badge-inherited" : "badge badge-explicit"}
                >
                  {grant.inherited ? "inherited" : "explicit"}
                </span>
              </td>
              <td>
                {instant(grant.observed_from)} – {instant(grant.last_confirmed_at)}
              </td>
              <td>{grant.certainty}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Rights({ rights }: { rights: RightsView | null }): JSX.Element {
  if (rights === null) {
    return <span className="muted">—</span>;
  }
  return (
    <span>
      {rights.label}
      {rights.is_exact ? null : <span className="muted"> (plus rights the label omits)</span>}
    </span>
  );
}

/**
 * What the grant was worth then, and what it is worth now — side by side, never merged.
 *
 * The baseline answer is the one being certified. The current answer is shown beside it
 * because a reviewer approving access that has since been widened should see that, and
 * because an entry is not an answer: a Full Control allow under a deny that outranks it
 * grants nothing.
 */
export function AccessComparison({
  context,
}: {
  context: ItemContextResponse;
}): JSX.Element {
  const { baseline_access: baseline, current_access: current } = context;
  if (!baseline.available && !current.available) {
    return (
      <section className="card" aria-labelledby="effective">
        <h2 id="effective">Effective access</h2>
        <p className="verdict verdict-info">
          {baseline.unavailable_reason ?? "ADG cannot resolve effective access for this item."}
        </p>
      </section>
    );
  }
  return (
    <section className="card" aria-labelledby="effective">
      <h2 id="effective">Effective access</h2>
      <table className="access-table">
        <thead>
          <tr>
            <th scope="col" />
            <th scope="col">At the baseline — what you are certifying</th>
            <th scope="col">Now</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Rights</th>
            <td>
              <Rights rights={baseline.rights} />
            </td>
            <td>
              <Rights rights={current.rights} />
            </td>
          </tr>
          <tr>
            <th scope="row">Limited by</th>
            <td>{baseline.limiting_layer ?? "—"}</td>
            <td>{current.limiting_layer ?? "—"}</td>
          </tr>
          <tr>
            <th scope="row">Certainty</th>
            <td>{baseline.certainty ?? "—"}</td>
            <td>{current.certainty ?? "—"}</td>
          </tr>
        </tbody>
      </table>
      {context.resolved_resource_key !== null &&
      context.item.target_kind === "share" ? (
        <p className="muted">
          Resolved against {context.resolved_resource_key}, the directory this share
          publishes: effective access is a question about a file-system object reached by a
          path, and the share&rsquo;s own permissions are one of the two layers.
        </p>
      ) : null}
    </section>
  );
}

/**
 * Why they have it, and whether revoking this entry would achieve anything.
 *
 * The part of the screen that stops a reviewer recording something untrue. `reachVerdict`
 * chooses the wording; the judgment — direct versus group-derived, and what a removal would
 * leave behind — is the backend's causal explanation, unchanged.
 */
export function ReachPanel({ context }: { context: ItemContextResponse }): JSX.Element {
  const { reach } = context;
  const verdict = reachVerdict(reach);
  return (
    <section className="card" aria-labelledby="reach">
      <h2 id="reach">Why they have this access</h2>
      <p className={`verdict verdict-${verdict.tone === "bad" ? "bad" : verdict.tone === "ok" ? "ok" : "info"}`}>
        <strong>{verdict.headline}.</strong> {verdict.detail}
      </p>
      {reach.available ? (
        <p className="muted">
          {reach.direct_paths} direct {reach.direct_paths === 1 ? "route" : "routes"} naming
          this principal, {reach.group_paths} through a group or well-known trustee.
        </p>
      ) : null}
      {reach.routes.length > 0 ? (
        <div className="table-scroll">
          <table className="acl-table">
            <caption className="muted">
              Other routes to this target. Removing the reviewed entry does not touch these.
            </caption>
            <thead>
              <tr>
                <th scope="col">Through</th>
                <th scope="col">Layer</th>
                <th scope="col">Rights it contributes</th>
                <th scope="col">Steps</th>
              </tr>
            </thead>
            <tbody>
              {reach.routes.map((route) => (
                <tr key={`${route.principal.key}-${route.layer}`}>
                  <th scope="row">
                    {route.principal.display_name ?? route.principal.sid ?? route.principal.key}
                  </th>
                  <td>{route.layer}</td>
                  <td>
                    <Rights rights={route.rights} />
                  </td>
                  <td>{route.depth}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {reach.removals.length > 0 ? (
        <div className="table-scroll">
          <table className="acl-table">
            <caption className="muted">
              What removing each reviewed entry would do, evaluated one entry at a time. An
              item carrying two entries reports two independent answers; neither describes
              removing both.
            </caption>
            <thead>
              <tr>
                <th scope="col">Entry</th>
                <th scope="col">Rights lost</th>
                <th scope="col">Rights left</th>
                <th scope="col">Ends all access</th>
              </tr>
            </thead>
            <tbody>
              {reach.removals.map((removal) => (
                <tr key={removal.ace_key}>
                  <th scope="row" className="principal-sid">
                    {removal.ace_key}
                  </th>
                  <td>
                    <Rights rights={removal.rights_removed} />
                  </td>
                  <td>
                    <Rights rights={removal.rights_after} />
                  </td>
                  <td className={removal.revokes_all_access ? "status-ok" : "status-warn"}>
                    {removal.revokes_all_access ? "yes" : "no"}
                    {removal.changes_nothing ? " — this entry settles nothing today" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {reach.truncated ? (
        <p className="muted">
          The explanation was truncated, so the routes above are some of them rather than all.
        </p>
      ) : null}
    </section>
  );
}

/** Open risk findings about this grant, this place, or this principal — in that order. */
export function FindingsPanel({
  findings,
  truncated,
}: {
  findings: readonly RelatedFindingView[];
  truncated: boolean;
}): JSX.Element {
  return (
    <section className="card" aria-labelledby="findings">
      <h2 id="findings">Risk findings</h2>
      {findings.length === 0 ? (
        <p className="muted">
          No open finding names this target or this principal. That is what the risk engine
          currently holds, not a statement that nothing is wrong.
        </p>
      ) : (
        <ul>
          {findings.map((finding) => (
            <li key={finding.finding_key}>
              <strong>{finding.rule_id}</strong> — {finding.severity} ({finding.confidence}{" "}
              confidence)
              <div className="muted">
                {finding.relation === "access"
                  ? "About this principal on this target."
                  : finding.relation === "target"
                    ? "About this target, whoever is named on it."
                    : "About this principal, elsewhere in the estate."}{" "}
                Open since {instant(finding.first_detected_at)}.
              </div>
            </li>
          ))}
        </ul>
      )}
      {truncated ? <p className="muted">More findings exist than are listed here.</p> : null}
    </section>
  );
}

/** When this entry last moved, as the change feed classified it. */
export function LastChangePanel({
  context,
}: {
  context: ItemContextResponse;
}): JSX.Element {
  return (
    <section className="card" aria-labelledby="last-change">
      <h2 id="last-change">What has happened to this entry</h2>
      {context.changes.length === 0 ? (
        <p className="muted">
          ADG holds no transition for these entries: they have not changed since it first
          read them.
        </p>
      ) : (
        <ul>
          {context.changes.map((change) => (
            <li key={`${change.key}-${change.changed_at_or_before ?? change.action}`}>
              <strong>{change.action}</strong> — {change.significance}, {change.severity}
              <div className="muted">
                {change.changed_after === null
                  ? "First reading; nothing earlier bounds it."
                  : change.is_exact
                    ? `At ${instant(change.changed_at_or_before ?? change.changed_after)}.`
                    : `Between ${instant(change.changed_after)} and ${instant(
                        change.changed_at_or_before ?? change.changed_after,
                      )} — the window it is known to have happened inside.`}
              </div>
              {change.reasons.length > 0 ? (
                <div className="muted">{change.reasons.join(" ")}</div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {context.changes_truncated ? (
        <p className="muted">Older changes exist than are listed here.</p>
      ) : null}
    </section>
  );
}

/** Every decision ever recorded on this item, oldest first. A superseded one stays. */
export function DecisionHistory({
  decisions,
}: {
  decisions: readonly DecisionView[];
}): JSX.Element {
  if (decisions.length === 0) {
    return <p className="muted">No decision has been recorded on this item.</p>;
  }
  return (
    <ol className="acl-group">
      {decisions.map((decision) => (
        <li key={decision.decision_id}>
          <strong>{decisionLabel(decision.decision)}</strong>
          {decision.current ? null : <span className="badge"> superseded</span>}
          {decision.decided_late ? <span className="badge status-warn"> late</span> : null}
          <div className="muted">
            {decision.decided_by_display_name ?? decision.decided_by_subject} ·{" "}
            {instant(decision.decided_at)}
          </div>
          {decision.rationale === null ? null : <p>{decision.rationale}</p>}
        </li>
      ))}
    </ol>
  );
}

/** One row in an item list, as a heading a reviewer can scan. */
export function ItemHeading({ item }: { item: ItemView }): JSX.Element {
  return (
    <span className="principal">
      <span className="principal-name">{principalLabel(item)}</span>
      <span className="principal-sid">{item.principal_sid}</span>
      <span className="muted"> on {targetLabel(item)}</span>
    </span>
  );
}
