import { Fragment, type JSX, type ReactNode } from "react";

import type { Note } from "@/lib/acl";
import type { ProvenanceView } from "@/lib/contracts";

export interface Fact {
  term: string;
  value: ReactNode;
}

/** A definition list of collected facts. */
export function FactList({ facts }: { facts: readonly Fact[] }): JSX.Element {
  return (
    // Fragments rather than wrapper elements: `dl.facts` is a two-column grid, and a
    // wrapper would become the grid item instead of the term and its value.
    <dl className="facts">
      {facts.map((fact) => (
        <Fragment key={fact.term}>
          <dt>{fact.term}</dt>
          <dd>{fact.value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/**
 * A field the API sent as null.
 *
 * Null means no run established the value. "not reported" and an em dash are not the same
 * claim, and in a tool whose whole subject is what was and was not observed, the difference
 * is the product: a blank cell reads as "nothing there", which is the one thing it does not
 * mean.
 */
export function NotReported(): JSX.Element {
  return <span className="muted">not reported</span>;
}

/** A tri-state boolean: true, false, or nobody said. */
export function TriState({ value }: { value: boolean | null | undefined }): JSX.Element {
  if (value === null || value === undefined) {
    return <NotReported />;
  }
  return <>{value ? "yes" : "no"}</>;
}

export function Maybe({ value }: { value: string | number | null | undefined }): JSX.Element {
  return value === null || value === undefined || value === "" ? (
    <NotReported />
  ) : (
    <>{value}</>
  );
}

/**
 * Notes about a descriptor, rendered so the severe ones cannot be skimmed past.
 *
 * `bad` and `warn` are alerts; a NULL DACL is announced rather than shown in a colour.
 * Every note carries its word as well as its hue, because colour is not a signal for
 * everybody reading this.
 */
export function NoteList({ notes, heading }: { notes: readonly Note[]; heading?: string }): JSX.Element | null {
  if (notes.length === 0) {
    return null;
  }
  return (
    <>
      {heading && <h3>{heading}</h3>}
      {notes.map((note) => (
        <div
          key={note.headline}
          className={`banner banner-${toneClass(note.tone)}`}
          role={note.tone === "bad" || note.tone === "warn" ? "alert" : "status"}
        >
          <h2>{note.headline}</h2>
          <p>{note.detail}</p>
        </div>
      ))}
    </>
  );
}

function toneClass(tone: Note["tone"]): string {
  switch (tone) {
    case "bad":
      return "error";
    case "warn":
      return "warning";
    default:
      return "info";
  }
}

/**
 * When ADG last saw this, and which run said so.
 *
 * Shown on every detail page because every fact here has an age, and a permission read
 * three weeks ago is a different kind of answer from one read this morning.
 */
export function Provenance({ provenance }: { provenance: ProvenanceView }): JSX.Element {
  return (
    <FactList
      facts={[
        { term: "First observed", value: <time dateTime={provenance.first_observed_at}>{provenance.first_observed_at}</time> },
        { term: "Last observed", value: <time dateTime={provenance.last_observed_at}>{provenance.last_observed_at}</time> },
        { term: "Last run", value: <code>{provenance.last_observed_run_id}</code> },
        { term: "Source", value: <code>{provenance.source_key}</code> },
      ]}
    />
  );
}
