import type { JSX } from "react";

import type { AppliedAceView, AccessExplanationResponse, RemovalTargetView } from "@/lib/derived";
import {
  certaintyNote,
  describeRemoval,
  alternateRouteWarning,
  cautions,
  outcomeWording,
  sufficientRemovals,
} from "@/lib/explanation";
import { limitingLayerLabel } from "@/lib/access";
import { Rights } from "@/components/EffectiveAccess";
import { PrincipalName } from "@/components/Identity";
import { SimulateAction } from "@/components/SimulateLink";
import styles from "@/components/explanation.module.css";

/**
 * The answer, before any of the working.
 *
 * Four outcomes, worded as four answers. The word alone is never the whole message: the
 * certainty sits beside it and, for an inconclusive answer, a sentence that forbids the
 * negative reading outright. "Unknown" that looks like "No" at a glance is the specific
 * failure this product exists to prevent.
 */
export function VerdictPanel({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  const wording = outcomeWording(explanation.verdict);
  const effective = explanation.effective;
  return (
    <div className="card">
      <h2>
        <span className={`verdict verdict-${wording.tone}`}>{wording.word}</span>
      </h2>
      <p>{wording.headline}</p>
      <p>{wording.guidance}</p>

      <dl>
        <dt>Effective rights</dt>
        <dd>
          {explanation.verdict.conclusive ? (
            <Rights rights={effective.rights} />
          ) : (
            // The engine's label for an empty mask is "No access", and printing it here
            // would restate the exact conclusion the verdict just withheld. Nothing was
            // established; that is a different statement and it is worded as one.
            <>
              <span>None established</span> <code>{effective.rights.mask}</code>
              <div className="ace-notes status-warn">
                Not a finding of no access — no right was established, which is a different
                statement.
              </div>
            </>
          )}
        </dd>

        <dt>Certainty</dt>
        <dd>
          <code>{explanation.verdict.certainty}</code> — {certaintyNote(explanation.verdict.certainty)}
        </dd>

        <dt>Narrower layer</dt>
        <dd>{limitingLayerLabel(effective.limiting_layer)}</dd>

        <dt>Access path</dt>
        <dd>
          <code>{explanation.access_path}</code>
          {explanation.effective.share === null &&
            " — the share ACL does not apply to this kind of access"}
        </dd>
      </dl>

      {!explanation.verdict.conclusive && (
        <div className="banner banner-warning" role="alert">
          <h3>Not a finding of no access</h3>
          <p>
            This response does not support the conclusion that this principal cannot reach
            this directory. Something it depends on has not been collected, and it could be
            hiding a grant.
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * One ACL, evaluated.
 *
 * Both layers get a panel even when one of them granted everything, because "the share ACL
 * is Full Control" is the fact that tells an administrator the fix belongs on the file
 * system. A layer that does not apply to the access path says so rather than rendering
 * empty.
 */
export function LayerPanel({
  title,
  explanation,
  layer,
}: {
  title: string;
  explanation: AccessExplanationResponse;
  layer: "ntfs" | "share";
}): JSX.Element {
  const evaluation = layer === "ntfs" ? explanation.effective.ntfs : explanation.effective.share;

  if (evaluation === null) {
    return (
      <div className="card">
        <h2>{title}</h2>
        <p className="muted">
          Not applied on the <code>{explanation.access_path}</code> access path. Local access
          does not cross a share.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>{title}</h2>
      {/*
        A <div>, not a <p>. `Rights` emits a <div className="ace-notes"> when the mask
        carries a note, and a <div> inside a <p> is invalid HTML: the browser closes the
        paragraph early and reparents the note outside it, so the warning ends up detached
        from the rights it qualifies. React logs it as a hydration error and every render
        of this panel in the test suite printed one.
      */}
      <div className="layer-rights">
        <Rights rights={evaluation.rights} />
      </div>
      <p className="muted">
        {evaluation.entries_evaluated} of {evaluation.entries_supplied} entries matched this
        subject&apos;s token.
        {evaluation.order_dependent &&
          " Entry order changed the result here — the ACL is not in canonical order."}
      </p>

      <AceTable caption="Entries that granted" entries={evaluation.granted_by} />
      <AceTable caption="Entries that denied" entries={evaluation.denied_by} />
      <AceTable
        caption="Entries that matched and took nothing"
        entries={evaluation.superseded}
        hint="Something earlier had already settled every right these name. Removing one of these on its own changes nothing."
      />
    </div>
  );
}

/**
 * Applied entries, with the position and the inheritance source.
 *
 * `position` is on every row because two entries naming one trustee are two different
 * entries, and "the Finance-RW ACE" is not a thing an administrator can go and find. The
 * inheritance source is here for the same reason: a change applied to an inherited entry,
 * on the wrong object, changes nothing and looks like it worked.
 */
export function AceTable({
  caption,
  entries,
  hint,
}: {
  caption: string;
  entries: readonly AppliedAceView[];
  hint?: string;
}): JSX.Element | null {
  if (entries.length === 0) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption>
          {caption}
          {hint !== undefined && <span className="muted"> — {hint}</span>}
        </caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Trustee</th>
            <th scope="col">Type</th>
            <th scope="col">Contributed</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr
              key={`${entry.layer}-${entry.position}-${entry.ace_key ?? "owner"}`}
              className={entry.ace_type === "deny" ? "ace-deny" : undefined}
            >
              <td>{entry.position === -1 ? "owner" : entry.position}</td>
              <th scope="row">
                <PrincipalName principal={entry.trustee} />
                {entry.via_group && (
                  <div className="ace-notes muted">reached through a group, not named directly</div>
                )}
              </th>
              <td>
                <span className={entry.ace_type === "deny" ? "badge badge-deny" : "badge badge-allow"}>
                  {entry.ace_type}
                </span>
              </td>
              <td>
                <code>{entry.contributed}</code>
              </td>
              <td>
                <span
                  className={
                    entry.source === "inherited" ? "badge badge-inherited" : "badge badge-explicit"
                  }
                >
                  {entry.source}
                </span>
                {entry.inherited_from !== null && (
                  <div className="ace-notes muted">set on {entry.inherited_from}</div>
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
 * What removing one relationship would actually do, measured.
 *
 * The screen's most load-bearing table. Every row is the result of re-running the access
 * check with that edge gone, and three of the four outcomes are ones an administrator
 * would otherwise get wrong: a removal that changes nothing, a partial removal that leaves
 * access, and a removal that would *widen* access because the edge carried a Deny.
 */
export function RemovalPanel({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  const warning = alternateRouteWarning(explanation);
  const sufficient = sufficientRemovals(explanation);

  return (
    <div className="card">
      <h2>What a removal would do</h2>

      {warning !== null && (
        <div className="banner banner-warning" role="alert">
          <h3>Alternative routes remain</h3>
          <p>{warning}</p>
        </div>
      )}

      {explanation.removal_targets.length === 0 ? (
        <p className="muted">
          No removable relationship was measured for this answer. Assumed token SIDs are not
          removable — there is no Everyone group to edit.
        </p>
      ) : (
        <>
          <p className="muted">
            {sufficient.length === 0
              ? "No single removal ends access here. That is the common case on a real estate, and it means more than one change is needed."
              : `${sufficient.length} of ${explanation.removal_targets.length} single removals end access here.`}
          </p>
          <div className="table-scroll">
            <table className="access-table">
              <caption>Each row was measured by re-running the access check without that relationship.</caption>
              <thead>
                <tr>
                  <th scope="col">Relationship</th>
                  <th scope="col">Effect</th>
                  <th scope="col">Rights afterwards</th>
                  <th scope="col">Routes that survive</th>
                  <th scope="col">What if</th>
                </tr>
              </thead>
              <tbody>
                {explanation.removal_targets.map((target) => {
                  const note = describeRemoval(explanation, target);
                  return (
                    <tr key={target.edge_id}>
                      <th scope="row">
                        <code>{target.source}</code> → <code>{target.target}</code>
                        <div className="ace-notes muted">{target.kind}</div>
                      </th>
                      <td>
                        <span className={`verdict verdict-${note.tone}`}>{note.headline}</span>
                        <span className="verdict-note">{note.detail}</span>
                      </td>
                      <td>
                        <Rights rights={target.rights_after} />
                      </td>
                      <td>
                        {note.alternates.length === 0 ? (
                          <span className="muted">none</span>
                        ) : (
                          note.alternates.map((path) => path.id).join(", ")
                        )}
                      </td>
                      <td>
                        <RemovalSimulateAction explanation={explanation} target={target} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Everything that qualifies this answer, in one place.
 *
 * Gathered by `cautions()` rather than assembled here, so that the screen and the text
 * export cannot disagree about what the caveats were. An exported explanation that drops
 * one is how a warning stops travelling with the finding it qualifies.
 */
export function CautionPanel({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element | null {
  const notes = cautions(explanation);
  if (notes.length === 0) {
    return null;
  }
  const worst = notes.some((note) => note.severity === "warning") ? "warning" : "info";
  return (
    <div className={`banner banner-${worst}`} role={worst === "warning" ? "alert" : "status"}>
      <h2>What could make this answer wrong</h2>
      <ul>
        {notes.map((note) => (
          <li key={note.id}>
            <strong>{note.headline}.</strong> {note.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Which collected state this answer came from.
 *
 * "As of" rather than "now". Equal basis tokens mean identical stored facts, so two
 * answers carrying one token differ only in the question — which is what makes a pasted
 * explanation comparable to the one beside it.
 */
export function BasisNote({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  const basis = explanation.basis;
  if (basis.is_empty) {
    return (
      <p className="muted">
        No collector run is on record. This answer is computed over an empty estate.
      </p>
    );
  }
  return (
    <p className="muted">
      As of {basis.latest_activity_at ?? "an unrecorded time"}, across {basis.runs} collector
      run{basis.runs === 1 ? "" : "s"}. Collected state <code>{basis.token}</code>.
    </p>
  );
}

/**
 * Who, what, and over which path.
 *
 * The heading names the pair and not the answer. "Why Alice can reach Payroll" is a
 * headline this screen must not write before the verdict is read — it is wrong for three
 * of the four outcomes, and wrongest for the one where nobody has collected enough to say.
 */
export function ExplanationHeader({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  return (
    <div className={styles.launcher}>
      <h1>Access explanation</h1>
      <p>
        <PrincipalName principal={explanation.subject} /> against{" "}
        <code>{explanation.resource.path ?? explanation.resource.key}</code>
      </p>
      <BasisNote explanation={explanation} />
    </div>
  );
}

/**
 * The `Simulate` action beside one removable relationship.
 *
 * This table already measured what removing the relationship would do *for this one pair*,
 * by re-running the access check. The simulation answers the wider question the operator asks
 * next: who **else** does that removal touch, across every directory the group reaches. So the
 * action opens the proposal editor with the removal written and the affected scope selected.
 *
 * Rendered only where the seed can be built exactly. A membership edge needs both principals'
 * storage keys and an ACE edge needs the entry's own key; where the graph does not carry one —
 * an assumed membership has no row to delete — the cell says so rather than offering an action
 * that would propose the wrong change.
 */
function RemovalSimulateAction({
  explanation,
  target,
}: {
  explanation: AccessExplanationResponse;
  target: RemovalTargetView;
}): JSX.Element {
  const nodes = new Map(explanation.graph.nodes.map((node) => [node.id, node]));
  const edge = explanation.graph.edges.find((item) => item.id === target.edge_id);

  if (target.kind === "membership") {
    const member = nodes.get(target.source);
    const group = nodes.get(target.target);
    if (member && group) {
      return (
        <SimulateAction
          seed={{
            kind: "remove_member",
            group_key: group.key,
            member_key: member.key,
            subject_key: explanation.subject.key,
          }}
          label="Simulate across the estate"
          title="Measure what removing this membership would do everywhere, not only here. Nothing is applied."
        />
      );
    }
  }

  if (target.kind === "trustee" && edge?.ace_key) {
    const ace = nodes.get(target.source);
    const isShare = ace?.layer === "smb_share";
    if (isShare && explanation.share?.key) {
      return (
        <SimulateAction
          seed={{
            kind: "remove_share_ace",
            share_key: explanation.share.key,
            ace_key: edge.ace_key,
            subject_key: explanation.subject.key,
          }}
          label="Simulate across the estate"
          title="Measure what removing this share entry would do everywhere. Nothing is applied."
        />
      );
    }
    if (!isShare) {
      return (
        <SimulateAction
          seed={{
            kind: "remove_ntfs_ace",
            resource_key: explanation.resource.path ?? explanation.resource.key,
            ace_key: edge.ace_key,
            subject_key: explanation.subject.key,
          }}
          label="Simulate across the estate"
          title="Measure what removing this entry would do everywhere. Nothing is applied."
        />
      );
    }
  }

  return <span className="muted">no row to remove</span>;
}
