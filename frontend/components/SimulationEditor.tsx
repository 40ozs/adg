"use client";

import { useState, type JSX } from "react";
import { useRouter } from "next/navigation";

import type { SimulationReportView, SimulationVocabularyResponse } from "@/lib/contracts";
import type { SimulationChangeBody, SimulationSeed } from "@/lib/simulation";
import { changeFromSeed } from "@/lib/simulation";
import { SimulationReport } from "@/components/Simulation";
import styles from "@/components/simulation.module.css";

/**
 * The proposal editor.
 *
 * Everything about this component is arranged around one sentence: **no changes will be
 * applied**. The banner is the first thing on the page, it is `role="alert"`, and it is not
 * dismissible. The button that runs the simulation says *Evaluate* rather than *Apply*, and
 * says underneath it what it will not do. The button that stores one says *Save this
 * proposal*, because that is exactly what it does — it writes ADG's own record of a question
 * somebody asked, and touches no Windows object.
 *
 * ## The proposal is JSON, and that is deliberate
 *
 * Nine change kinds, each with its own required fields and its own rules — a removal must
 * name the entry it removes; protecting a directory must say what happens to the entries it
 * inherits; a share entry carries a mask or a permission level and never both. A form with
 * nine conditional layouts would be a second copy of those rules written in TypeScript, it
 * would be the more lenient copy, and it would let somebody submit a proposal the API then
 * refuses with a message the form had already contradicted.
 *
 * So the editor edits the change document, seeds it from wherever the operator came from, and
 * lets the API's own domain constructors be the validator. A refusal comes back as the
 * sentence the domain wrote, naming the field — which is more useful than anything a form
 * could have said in advance. The vocabulary panel beside the editor is served by the API for
 * the same reason: a second copy of a vocabulary is a second copy that can be wrong.
 *
 * ## Nothing is stored until somebody names it
 *
 * `Evaluate` calls the preview route, which keeps nothing. A draft that accumulated a row in
 * the database on every keystroke would turn an editing session into an audit trail of
 * half-formed ideas.
 */
export function SimulationEditor({
  seed,
  vocabulary,
}: {
  seed: SimulationSeed | null;
  vocabulary: SimulationVocabularyResponse | null;
}): JSX.Element {
  const router = useRouter();
  const [text, setText] = useState(() => initialText(seed));
  const [scope, setScope] = useState<string>(seed?.subject_key ? "pair" : "affected");
  const [subjectKey, setSubjectKey] = useState(seed?.subject_key ?? "");
  const [resourceKey, setResourceKey] = useState(seed?.resource_key ?? "");
  const [accessPath, setAccessPath] = useState("remote_smb");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [report, setReport] = useState<SimulationReportView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const body = (): unknown => ({
    changes: JSON.parse(text) as SimulationChangeBody[],
    scope: {
      kind: scope,
      subject_key: scope === "pair" || scope === "subject" ? subjectKey : null,
      resource_key: scope === "pair" || scope === "resource" ? resourceKey : null,
      path: accessPath,
    },
  });

  const send = async (path: string, extra: Record<string, unknown> = {}): Promise<unknown> => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(body() as object), ...extra }),
    });
    const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    if (!response.ok) {
      throw new Error(
        typeof payload?.detail === "string"
          ? payload.detail
          : `The API refused the proposal (HTTP ${response.status}).`,
      );
    }
    return payload;
  };

  const evaluate = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      setReport((await send("/api/simulations/preview")) as SimulationReportView);
    } catch (problem) {
      setReport(null);
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  };

  const save = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const stored = (await send("/api/simulations", { name, description })) as {
        simulation: { simulation_id: string };
      };
      router.push(`/simulations/${stored.simulation.simulation_id}`);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      setBusy(false);
    }
  };

  return (
    <>
      <div className="banner banner-warning" role="alert">
        <h2>No changes will be applied</h2>
        <p>
          This screen proposes a change and measures what it would do. ADG computes the answer
          by reading its own collected facts through the proposal, held in memory. Nothing is
          written to Active Directory, to a share, or to an NTFS descriptor, and nothing in
          ADG&rsquo;s collected state is altered. There is no button on this page that changes
          a permission.
        </p>
      </div>

      <div className="card">
        <h2>The proposal</h2>
        <p className="muted">
          One or more changes, as the API&rsquo;s own change documents. The rules about which
          fields each kind needs live in the backend, and a refusal comes back naming the field
          — which is more useful than a form guessing in advance.
        </p>
        <label htmlFor="simulation-changes">Changes</label>
        <textarea
          id="simulation-changes"
          className={styles.editor}
          value={text}
          spellCheck={false}
          onChange={(event) => setText(event.target.value)}
        />

        <fieldset className={styles.scope}>
          <legend>What to ask about</legend>
          <label htmlFor="simulation-scope">Scope</label>
          <select
            id="simulation-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          >
            <option value="affected">
              Everything this change could affect (derived from the proposal)
            </option>
            <option value="pair">One principal against one directory</option>
            <option value="resource">Everyone ADG can enumerate for one directory</option>
            <option value="subject">Everything one principal can reach</option>
          </select>

          {(scope === "pair" || scope === "subject") && (
            <>
              <label htmlFor="simulation-subject">Principal</label>
              <input
                id="simulation-subject"
                value={subjectKey}
                placeholder="S-1-5-21-…-1104"
                onChange={(event) => setSubjectKey(event.target.value)}
              />
            </>
          )}
          {(scope === "pair" || scope === "resource") && (
            <>
              <label htmlFor="simulation-resource">Directory</label>
              <input
                id="simulation-resource"
                value={resourceKey}
                placeholder="\\FS01\Finance"
                onChange={(event) => setResourceKey(event.target.value)}
              />
            </>
          )}

          <label htmlFor="simulation-path">Access path</label>
          <select
            id="simulation-path"
            value={accessPath}
            onChange={(event) => setAccessPath(event.target.value)}
          >
            <option value="remote_smb">Over the network (share ACL and NTFS ACL)</option>
            <option value="local">On the console (NTFS ACL only)</option>
          </select>
        </fieldset>

        <div className={styles.actions}>
          <button type="button" className="button" disabled={busy} onClick={() => void evaluate()}>
            {busy ? "Evaluating…" : "Evaluate this proposal"}
          </button>
          <span className="muted">
            Reads ADG&rsquo;s collected facts and computes an answer. Changes nothing.
          </span>
        </div>

        {error && (
          <p className="status-bad" role="alert">
            {error}
          </p>
        )}
      </div>

      {report && <SimulationReport report={report} heading="What this proposal would do" />}

      {report && (
        <div className="card">
          <h2>Save this proposal</h2>
          <p className="muted">
            Writes the proposal and this result into ADG&rsquo;s own records, so it can be
            attached to a change ticket and run again later against a newer scan. It still
            changes nothing about the estate.
          </p>
          <label htmlFor="simulation-name">Name</label>
          <input
            id="simulation-name"
            value={name}
            placeholder="CHG-1042: take Finance-Team off the payroll path"
            onChange={(event) => setName(event.target.value)}
          />
          <label htmlFor="simulation-description">Notes (optional)</label>
          <textarea
            id="simulation-description"
            className={styles.notes}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <div className={styles.actions}>
            <button
              type="button"
              className="button"
              disabled={busy || name.trim() === ""}
              onClick={() => void save()}
            >
              Save this proposal
            </button>
          </div>
        </div>
      )}

      {vocabulary && <VocabularyPanel vocabulary={vocabulary} />}
    </>
  );
}

function VocabularyPanel({
  vocabulary,
}: {
  vocabulary: SimulationVocabularyResponse;
}): JSX.Element {
  return (
    <details className="card">
      <summary>What a proposal can say, and what a report can say back</summary>
      <p className="muted">
        Served by the API rather than written into this page: a second copy of a vocabulary is
        a second copy that can be wrong, and the wrong one is always the one somebody trusts.
      </p>
      <h3>Changes</h3>
      <dl>
        {vocabulary.change_kinds.map((entry) => (
          <div key={entry.code}>
            <dt>
              <code>{entry.code}</code>
            </dt>
            <dd>{entry.description}</dd>
          </div>
        ))}
      </dl>
      <h3>Caveats a report can raise</h3>
      <dl>
        {vocabulary.caveats.map((entry) => (
          <div key={entry.code}>
            <dt>
              <code>{entry.code}</code>
            </dt>
            <dd>{entry.description}</dd>
          </div>
        ))}
      </dl>
      <p className="muted">
        At most {vocabulary.max_changes} changes in one proposal; at most{" "}
        {vocabulary.bounds_ceilings.max_principals} principals and{" "}
        {vocabulary.bounds_ceilings.max_pairs} pairs evaluated.
      </p>
    </details>
  );
}

/**
 * The document the editor opens with.
 *
 * A seeded change when one arrived, and otherwise a membership removal with the fields empty
 * — the change an administrator most often wants to ask about, laid out so the shape is
 * visible rather than having to be remembered.
 */
function initialText(seed: SimulationSeed | null): string {
  const change = seed ? changeFromSeed(seed) : null;
  if (change) {
    return JSON.stringify([change], null, 2);
  }
  return JSON.stringify(
    [{ kind: "remove_member", group_key: "", member_key: "" }],
    null,
    2,
  );
}
