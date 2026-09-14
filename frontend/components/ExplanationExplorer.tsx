"use client";

import { useState, type JSX } from "react";

import type { AccessExplanationResponse, CausalPathView } from "@/lib/derived";
import {
  chainLabels,
  describePath,
  effectWording,
  relationLabel,
  structureRows,
  type PathDetail,
} from "@/lib/explanation";
import { Rights } from "@/components/EffectiveAccess";
import { AccessGraph } from "@/components/AccessGraph";
import styles from "@/components/explanation.module.css";

/**
 * The diagram, the route table, and the inspector — one selection between them.
 *
 * The three views are three renderings of the same response, and the selection is held
 * here so they cannot disagree about which route is being looked at. Selecting is done
 * from the table, with a real button per row, and the diagram follows; clicking the
 * diagram also selects, for a mouse user, but nothing is reachable *only* that way.
 *
 * This is the one client component on the screen. Everything else — the verdict, both
 * layers, the measured removals, the caveats — renders on the server, so a reader with no
 * JavaScript still gets every fact and every warning. Losing the highlight is acceptable;
 * losing the answer is not.
 */
export function ExplanationExplorer({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  const [selectedPathId, setSelectedPathId] = useState<string | null>(null);
  const detail = selectedPathId === null ? null : describePath(explanation, selectedPathId);

  const toggle = (pathId: string): void => {
    setSelectedPathId((current) => (current === pathId ? null : pathId));
  };

  return (
    <div className={styles.explorer}>
      <div className="card">
        <h2>How the rights arrive</h2>
        <p className="muted">
          Each route runs from the principal, through the groups that carry it, to the entry
          that named one of them, and on to the object. Select a route to open it.
        </p>

        <AccessGraph
          explanation={explanation}
          selectedPathId={selectedPathId}
          onSelectPath={setSelectedPathId}
        />
        <ul className={styles.legend}>
          <li>Dashed outline: an entry on an ACL</li>
          <li>Dotted outline: a SID Windows assumes, not an observed membership</li>
          <li>Red dashed line: a Deny</li>
          <li>Thick line: the selected route</li>
        </ul>
      </div>

      <div className="card">
        <h2>Routes</h2>
        {explanation.paths.length === 0 ? (
          <p className="muted">
            No entry on either ACL reached this principal, so there is no route to show.
          </p>
        ) : (
          <>
            {!explanation.complete && (
              <div className="banner banner-warning" role="alert">
                <h3>Not every route</h3>
                <p>
                  The enumeration hit a limit. Routes exist that are not in this table, so
                  the absence of one here is not evidence there is none.
                </p>
              </div>
            )}
            <PathTable
              paths={explanation.paths}
              selectedPathId={selectedPathId}
              onSelect={toggle}
            />
          </>
        )}
      </div>

      {detail !== null && <PathInspector detail={detail} />}

      <details className="card">
        <summary>Every relationship in the diagram, as a list</summary>
        <p className="muted">
          The same nodes and edges the diagram draws, in text. Nothing is in one and not the
          other.
        </p>
        <StructureTable explanation={explanation} selectedPathId={selectedPathId} />
      </details>
    </div>
  );
}

/**
 * Every route, with what it says and what it is worth.
 *
 * Those are two columns because they are two facts: an Allow that delivers nothing because
 * the other layer withholds it is an ordinary state of a real estate, and a table that
 * collapsed them would report a grant that is not one.
 */
export function PathTable({
  paths,
  selectedPathId,
  onSelect,
}: {
  paths: readonly CausalPathView[];
  selectedPathId: string | null;
  onSelect: (pathId: string) => void;
}): JSX.Element {
  return (
    <div className="table-scroll">
      <table className="access-table">
        <caption>Select a route to inspect every hop, the exact entry, and where it was set.</caption>
        <thead>
          <tr>
            <th scope="col">Route</th>
            <th scope="col">Reaches the entry through</th>
            <th scope="col">Entry</th>
            <th scope="col">Says</th>
            <th scope="col">Worth</th>
          </tr>
        </thead>
        <tbody>
          {paths.map((path) => {
            const selected = path.id === selectedPathId;
            const effect = effectWording(path.effect);
            const chain = chainLabels(path.chain);
            return (
              <tr key={path.id} className={selected ? styles.selectedRow : undefined}>
                <th scope="row">
                  <button
                    type="button"
                    className={`${styles.pathButton} ${selected ? styles.pathSelected : ""}`}
                    aria-pressed={selected}
                    onClick={() => onSelect(path.id)}
                  >
                    <span className={styles.marker} aria-hidden="true">
                      {selected ? "▸" : ""}
                    </span>
                    {path.id}
                  </button>
                </th>
                <td>
                  {chain.length <= 1 ? (
                    <span className="muted">named directly on the ACL</span>
                  ) : (
                    chain.join(" → ")
                  )}
                  {path.assumed && (
                    <div className="ace-notes status-warn">
                      through an assumed token SID, not an observed membership
                    </div>
                  )}
                </td>
                <td>
                  <code>{path.layer}</code> #{path.ace_position === -1 ? "owner" : path.ace_position}
                  {path.inherited && <div className="ace-notes muted">inherited</div>}
                </td>
                <td>
                  <span
                    className={path.relation === "deny" ? "badge badge-deny" : "badge badge-allow"}
                  >
                    {relationLabel(path.relation)}
                  </span>
                  <div className="ace-notes">
                    <Rights rights={path.ace_rights} />
                  </div>
                </td>
                <td>
                  <span className={`verdict verdict-${effect.tone}`}>{effect.label}</span>
                  <span className="verdict-note">{effect.detail}</span>
                  <div className="ace-notes">
                    <Rights rights={path.effective_rights} />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One route, hop by hop.
 *
 * The four things an administrator has to know before changing anything: each membership
 * edge and whether it can be deleted at all, the exact entry with its position, where an
 * inherited entry was actually set, and whether this route contributed to the final answer
 * or is one of the ones that changes nothing.
 */
export function PathInspector({ detail }: { detail: PathDetail }): JSX.Element {
  const path = detail.path;
  const effect = effectWording(path.effect);
  return (
    <section className={styles.inspector} aria-label={`Route ${path.id}`}>
      <h2>Route {path.id}</h2>
      <p>
        <span className={`verdict verdict-${effect.tone}`}>{effect.label}</span>
        <span className="verdict-note">{detail.worth}</span>
      </p>

      <ol className={styles.steps}>
        {detail.steps.map((step, index) => (
          <li
            key={`${step.kind}-${step.edge?.id ?? index}`}
            className={`${styles.step} ${step.removable ? styles.stepRemovable : ""}`}
          >
            <div className={styles.stepKind}>
              {step.kind.replace("_", " ")}
              {step.edge !== null && (step.removable ? " · removable" : " · not removable")}
            </div>
            <div>{step.label}</div>
            {step.note !== null && <div className="ace-notes muted">{step.note}</div>}
          </li>
        ))}
      </ol>

      <h3>The entry</h3>
      {detail.ace === null ? (
        <p className="muted">
          {path.ace_position === -1
            ? "There is no entry. The rights come from owning the object, which is why no ACL viewer shows this route."
            : "The response did not carry the applied entry for this route."}
        </p>
      ) : (
        <dl>
          <dt>Position</dt>
          <dd>
            <code>{detail.ace.layer}</code> entry #{detail.ace.position}
          </dd>
          <dt>Key</dt>
          <dd>
            <code>{detail.ace.ace_key}</code>
          </dd>
          <dt>Trustee it names</dt>
          <dd>
            <code>{detail.ace.matched_key}</code>
          </dd>
          <dt>Mask</dt>
          <dd>
            <code>{detail.ace.access_mask}</code>, of which <code>{detail.ace.contributed}</code>{" "}
            applied
          </dd>
          <dt>Set on</dt>
          <dd>
            {detail.inheritedFrom === null ? (
              <>explicitly on this object</>
            ) : (
              <>
                <code>{detail.inheritedFrom}</code> — a change made here would not affect it
              </>
            )}
          </dd>
        </dl>
      )}

      <h3>Does this route matter?</h3>
      <p>
        {detail.contributes
          ? "Yes. This route changed the final answer, so a remediation has to address it."
          : `No. ${effect.detail} Removing this route alone would not change the effective rights.`}
      </p>
    </section>
  );
}

/** The diagram as rows: one per relationship, with the routes that travel it. */
export function StructureTable({
  explanation,
  selectedPathId,
}: {
  explanation: AccessExplanationResponse;
  selectedPathId: string | null;
}): JSX.Element {
  const rows = structureRows(explanation);
  return (
    <div className="table-scroll">
      <table className="acl-table">
        <caption>Every node and relationship the diagram draws, in text.</caption>
        <thead>
          <tr>
            <th scope="col">Relationship</th>
            <th scope="col">Kind</th>
            <th scope="col">Removable</th>
            <th scope="col">On routes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.edgeId}
              className={
                selectedPathId !== null && row.paths.includes(selectedPathId)
                  ? styles.selectedRow
                  : undefined
              }
            >
              <th scope="row">{row.description}</th>
              <td>{row.kind.replace("_", " ")}</td>
              <td className={row.removable ? undefined : "muted"}>{row.removable ? "yes" : "no"}</td>
              <td>{row.paths.length === 0 ? <span className="muted">none</span> : row.paths.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
