"use client";

import { useState, type JSX } from "react";

import type { AccessExplanationResponse } from "@/lib/derived";
import { explanationJson, explanationText } from "@/lib/explanation";
import styles from "@/components/explanation.module.css";

type Format = "text" | "json";

/**
 * The explanation, in a form that can leave the screen.
 *
 * Two formats because two audiences: the text goes into a ticket for a person, the JSON is
 * the API's own response body for anything that has to be compared or re-checked later.
 * The JSON is the response verbatim rather than a reshaped subset — a reshaping would be a
 * second description of the answer, and the one thing worse than no export is an export
 * that quietly says something else.
 *
 * Both formats are also rendered into a read-only textarea, not only offered to the
 * clipboard. `navigator.clipboard` needs a secure context and a permission that an
 * operator on an internal host may not have, and a copy button that silently does nothing
 * is how an auditor loses a finding. The textarea always works.
 *
 * This is an export of *one answer*, deliberately. There is no report builder here, no
 * scheduling and no estate-wide extract; those are a later phase and a much larger
 * disclosure decision than a single explanation somebody is already looking at.
 */
export function ExplanationExport({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  const [format, setFormat] = useState<Format>("text");
  const [status, setStatus] = useState<string>("");

  const body = format === "text" ? explanationText(explanation) : explanationJson(explanation);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(body);
      setStatus(`Copied the ${format === "text" ? "text" : "JSON"} explanation to the clipboard.`);
    } catch {
      setStatus(
        "This browser would not give the page clipboard access. Select the text below and copy it.",
      );
    }
  };

  return (
    <div className="card">
      <h2>Export this explanation</h2>
      <p className="muted">
        One answer, in full, with its caveats. Not a report — the caveats travel with the
        finding or the finding is misleading.
      </p>

      <div className={styles.exportButtons}>
        <fieldset>
          <legend className="visually-hidden">Export format</legend>
          <label>
            <input
              type="radio"
              name="explanation-format"
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
              name="explanation-format"
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
        <button type="button" className="button" onClick={copy}>
          Copy to clipboard
        </button>
      </div>

      <p role="status" aria-live="polite" className="muted">
        {status}
      </p>

      <label htmlFor="explanation-export" className="visually-hidden">
        The explanation as {format === "text" ? "text" : "JSON"}
      </label>
      <textarea
        id="explanation-export"
        className={styles.exportArea}
        readOnly
        value={body}
        spellCheck={false}
      />
    </div>
  );
}
