import type { JSX } from "react";

/**
 * The `running` state of a comparison (ADG-DEMO-03), shown while the server renders the page.
 *
 * Announced, not only drawn: `role="status"` with `aria-live` so a screen reader hears that the
 * comparison is running, and `aria-busy` so it waits for the outcome that replaces this.
 */
export default function ComparisonRunning(): JSX.Element {
  return (
    <>
      <h1>Compare two points in time</h1>
      <div className="banner banner-info" role="status" aria-live="polite" aria-busy="true">
        <h2>
          <span aria-hidden="true">…</span> Comparing
        </h2>
        <p>
          The comparison is running. It answers inside its interactive window, or stops and
          says so — it does not run on past it.
        </p>
      </div>
    </>
  );
}
