"use client";

/**
 * The two places a reviewer writes something down.
 *
 * Client components because both need state a server component cannot hold: which answer is
 * selected, what has been typed, and which items are ticked. Both submit through the server
 * actions in `app/governance/actions.ts`, so the access token stays on the server.
 *
 * **Nothing here is a control.** The comment requirement, the assignment gate, the campaign
 * lifecycle and the bulk homogeneity rules are all enforced by the API, and a refusal is
 * rendered with the API's own message. What these components do is tell the reviewer what
 * they are about to assert *before* they assert it — which is a different job from
 * preventing them, and the one a screen can actually do.
 *
 * Two choices worth naming:
 *
 * * **The comment box is always present**, and says whether it is required for the answer
 *   currently selected. A box that appeared when you picked "revoke" would teach reviewers
 *   that explaining themselves is an exception.
 * * **A bulk action is never the default.** The selection has to be made deliberately, the
 *   bar states what the batch is about in words, and the button is disabled with a reason
 *   whenever this application can already tell the API will refuse it.
 */

import { useState, useTransition, type JSX } from "react";

import { recordBulkDecision, recordDecision, type ActionResult } from "@/app/governance/actions";
import type { ItemView } from "@/lib/contracts";
import {
  DECISION_OPTIONS,
  bulkEligibility,
  bulkSubject,
  commentRequired,
  decisionOption,
} from "@/lib/governance";

function Outcome({ result }: { result: ActionResult | null }): JSX.Element | null {
  if (result === null) {
    return null;
  }
  const tone = result.status === "recorded" ? "verdict-ok" : "verdict-bad";
  return (
    <p className={`verdict ${tone}`} role="status">
      {result.message}
    </p>
  );
}

/**
 * Record one attestation.
 *
 * `readOnly` covers the two cases where the API would refuse anyway — the campaign is not
 * active, or this item is not assigned to this reviewer — and says which, because "the
 * button is missing" sends somebody to the wrong person.
 */
export function DecisionForm({
  itemId,
  commentRequirement,
  readOnly,
}: {
  itemId: string;
  commentRequirement: string;
  readOnly: { reason: string } | null;
}): JSX.Element {
  const [decision, setDecision] = useState<string>("certify");
  const [rationale, setRationale] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  const [pending, startTransition] = useTransition();

  if (readOnly !== null) {
    return (
      <section className="card" aria-labelledby="decide">
        <h2 id="decide">Your decision</h2>
        <p className="verdict verdict-info">{readOnly.reason}</p>
      </section>
    );
  }

  const option = decisionOption(decision);
  const required = commentRequired(decision, commentRequirement);
  const blank = rationale.trim() === "";

  return (
    <section className="card" aria-labelledby="decide">
      <h2 id="decide">Your decision</h2>
      <p className="muted">
        You are deciding about the frozen evidence above — what was true when this campaign
        was cut — not about whatever the grant is now.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          startTransition(async () => {
            setResult(await recordDecision(itemId, decision, rationale));
          });
        }}
      >
        <fieldset>
          <legend>What do you conclude?</legend>
          {DECISION_OPTIONS.map((candidate) => (
            <label key={candidate.value} className="role-pill">
              <input
                type="radio"
                name="decision"
                value={candidate.value}
                checked={decision === candidate.value}
                onChange={() => setDecision(candidate.value)}
              />{" "}
              {candidate.label}
            </label>
          ))}
        </fieldset>
        {option === null ? null : <p className="muted">{option.meaning}</p>}
        <label htmlFor="rationale">
          Comment{required ? " (required)" : " (optional)"}
        </label>
        <textarea
          id="rationale"
          name="rationale"
          rows={4}
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
          aria-describedby="rationale-hint"
        />
        <p id="rationale-hint" className="muted">
          {required
            ? commentRequirement === "always"
              ? "This campaign requires a comment on every decision, approvals included."
              : "This answer must say why. An unexplained removal cannot be defended to the person who loses access."
            : "Optional for an approval, and still worth writing when the reason is not obvious."}
        </p>
        <button type="submit" className="button" disabled={pending || (required && blank)}>
          {pending ? "Recording…" : "Record decision"}
        </button>
      </form>
      <Outcome result={result} />
    </section>
  );
}

/**
 * Answer several items that are one question.
 *
 * The eligibility check is a courtesy: it explains a disabled button using only what the
 * item rows already say. Drift and assignment are deliberately not guessed at here — the
 * first needs a live comparison and the second the database — so a selection this bar
 * allows can still be refused, and the refusal is shown verbatim.
 */
export function BulkDecisionBar({
  campaignId,
  items,
  selected,
  commentRequirement,
}: {
  campaignId: string;
  items: readonly ItemView[];
  selected: readonly string[];
  commentRequirement: string;
}): JSX.Element {
  const [decision, setDecision] = useState<string>("certify");
  const [rationale, setRationale] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  const [pending, startTransition] = useTransition();

  const chosen = items.filter((item) => selected.includes(item.item_id));
  const eligibility = bulkEligibility(chosen);
  const required = commentRequired(decision, commentRequirement);
  const blank = rationale.trim() === "";

  return (
    <section className="card" aria-labelledby="bulk">
      <h2 id="bulk">Answer the selected items together</h2>
      {chosen.length === 0 ? (
        <p className="muted">
          Select items to answer several at once. A batch must be one question: one principal
          across many targets, or one target across many principals.
        </p>
      ) : (
        <p>
          <strong>{chosen.length}</strong> selected — {bulkSubject(chosen)}.
        </p>
      )}
      {!eligibility.eligible && chosen.length > 0 ? (
        <p className="verdict verdict-bad">{eligibility.reason}</p>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          startTransition(async () => {
            setResult(
              await recordBulkDecision(
                campaignId,
                chosen.map((item) => item.item_id),
                decision,
                rationale,
              ),
            );
          });
        }}
      >
        <label htmlFor="bulk-decision">Decision</label>
        <select
          id="bulk-decision"
          value={decision}
          onChange={(event) => setDecision(event.target.value)}
        >
          {DECISION_OPTIONS.map((candidate) => (
            <option key={candidate.value} value={candidate.value}>
              {candidate.label}
            </option>
          ))}
        </select>
        <label htmlFor="bulk-rationale">
          Comment{required ? " (required)" : " (optional)"}
        </label>
        <textarea
          id="bulk-rationale"
          rows={3}
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
        />
        <p className="muted">
          One comment covers the batch. Each item still gets its own decision, its own
          evidence digest and its own audit event, so the record is the same as if you had
          answered them one at a time. Items whose access has changed since the baseline are
          refused: those need reading.
        </p>
        <button
          type="submit"
          className="button"
          disabled={pending || !eligibility.eligible || (required && blank)}
        >
          {pending ? "Recording…" : `Record on ${chosen.length} items`}
        </button>
      </form>
      <Outcome result={result} />
    </section>
  );
}

/**
 * The item list with its selection, kept here because the checkboxes and the bar share it.
 *
 * A server component cannot hold the selection, and lifting it to the page would make the
 * whole page a client component — which would mean fetching the items in the browser and
 * putting a token where the session design says one never goes.
 */
export function SelectableItems({
  campaignId,
  items,
  commentRequirement,
  canDecide,
}: {
  campaignId: string;
  items: readonly ItemView[];
  commentRequirement: string;
  canDecide: boolean;
}): JSX.Element {
  const [selected, setSelected] = useState<string[]>([]);

  return (
    <>
      {canDecide ? (
        <BulkDecisionBar
          campaignId={campaignId}
          items={items}
          selected={selected}
          commentRequirement={commentRequirement}
        />
      ) : null}
      <div className="table-scroll">
        <table className="acl-table">
          <caption className="muted">
            {items.length.toLocaleString()} items. Each is one grant — a principal and a
            target — with every entry that creates it.
          </caption>
          <thead>
            <tr>
              {canDecide ? <th scope="col">Select</th> : null}
              <th scope="col">Principal</th>
              <th scope="col">Target</th>
              <th scope="col">Entries</th>
              <th scope="col">Certainty</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.item_id}>
                {canDecide ? (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.principal_display_name ?? item.principal_sid}`}
                      checked={selected.includes(item.item_id)}
                      onChange={(event) =>
                        setSelected((current) =>
                          event.target.checked
                            ? [...current, item.item_id]
                            : current.filter((id) => id !== item.item_id),
                        )
                      }
                    />
                  </td>
                ) : null}
                <th scope="row">
                  <a href={`/governance/items/${item.item_id}`}>
                    {item.principal_display_name ?? item.principal_sid}
                  </a>
                  {" "}
                  <a
                    className="muted"
                    href={`/governance/campaigns/${campaignId}?principal=${encodeURIComponent(
                      item.principal_key,
                    )}`}
                    title="Every grant this principal has in this campaign"
                  >
                    (all theirs)
                  </a>
                </th>
                <td>
                  <a
                    href={`/governance/campaigns/${campaignId}?target=${encodeURIComponent(
                      item.target_key,
                    )}`}
                    title="Everyone named on this target in this campaign"
                  >
                    {item.target_path ?? item.target_key}
                  </a>
                </td>
                <td>{item.grants.length}</td>
                <td>{item.certainty}</td>
                <td>{item.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
