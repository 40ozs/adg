"use client";

import { useState, type JSX } from "react";

import type { SimulationReportView } from "@/lib/contracts";
import { reportJson, reportText } from "@/lib/simulation";
import styles from "@/components/simulation.module.css";

type Format = "text" | "json";

/**
 * The simulation, in a form that can leave the screen.
 *
 * Two formats because two audiences: the text goes into a change ticket for a person, the
 * JSON is the API's own response body for anything that has to be compared or re-checked
 * later. The JSON is the response verbatim rather than a reshaped subset — a reshaping would
 * be a second description of the answer, ageing independently of the one the engine computes.
 *
 * **Every caveat travels with the conclusion.** An export that carried "Bob loses access" and
 * left `loss_may_not_hold` behind would be worse than no export at all: the sentence somebody
 * pastes into a ticket is the sentence the change gets signed off against, and the
 * qualification is the half that stops a remediation that achieves nothing. So is the
 * truncation block — a partial impact list pasted into a ticket must not read as a complete
 * one.
 *
 * The non-destructive notice is the **first line** of the text export, for the same reason.
 * A report read six months later, by somebody who was not here, must not be mistaken for a
 * record of a change that was made.
 *
 * Both formats are rendered into a read-only textarea rather than only offered to the
 * clipboard: `navigator.clipboard` needs a secure context and a permission an operator on an
 * internal host may not have, and a copy button that silently does nothing is how an auditor
 * loses a finding. The textarea always works.
 */
export function SimulationExport({
  report,
  name,
}: {
  report: SimulationReportView;
  name?: string;
}): JSX.Element {
  const [format, setFormat] = useState<Format>("text");
  const [status, setStatus] = useState<string>("");

  const body = format === "text" ? reportText(report, name) : reportJson(report);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(body);
      setStatus(`Copied the ${format === "text" ? "text" : "JSON"} report to the clipboard.`);
    } catch {
      setStatus(
        "This browser would not give the page clipboard access. Select the text below and copy it.",
      );
    }
  };

  return (
    <div className="card">
      <h2>Export this simulation</h2>
      <p className="muted">
        The plan and the result, with every caveat and every bound that was hit. Not a
        certificate that anything was changed — nothing was.
      </p>

      <div className={styles.exportButtons}>
        <fieldset>
          <legend className="visually-hidden">Export format</legend>
          <label>
            <input
              type="radio"
              name="simulation-format"
              value="text"
              checked={format === "text"}
              onChange={() => {
                setFormat("text");
                setStatus("");
              }}
            />{" "}
            Human-readable text
          </label>{" "}
          <label>
            <input
              type="radio"
              name="simulation-format"
              value="json"
              checked={format === "json"}
              onChange={() => {
                setFormat("json");
                setStatus("");
              }}
            />{" "}
            Structured JSON
          </label>
        </fieldset>
        <button type="button" className="button" onClick={() => void copy()}>
          Copy to clipboard
        </button>
      </div>

      <p role="status" aria-live="polite" className="muted">
        {status}
      </p>

      <label htmlFor="simulation-export" className="visually-hidden">
        The simulation as {format === "text" ? "text" : "JSON"}
      </label>
      <textarea
        id="simulation-export"
        className={styles.exportArea}
        readOnly
        value={body}
        spellCheck={false}
      />
    </div>
  );
}
