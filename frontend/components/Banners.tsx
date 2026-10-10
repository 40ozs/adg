import type { JSX } from "react";
import Link from "next/link";

import type { AuthConfig, CollectionStatus, Principal, RuntimeInfo } from "@/lib/contracts";
import type { Caveat, ViewState } from "@/lib/state";

/**
 * The banner that must never be missable.
 *
 * A deployment running development authentication has no real sign-in: anyone who can reach
 * it is whoever they say they are. The API refuses to start in that mode in production, so
 * this cannot appear on a production deployment — but it must be unmistakable wherever it
 * does appear, because the alternative is somebody demonstrating "the auditing tool" to a
 * customer on a box with no front door.
 *
 * `role="alert"` rather than a quiet note: it is read out, and it is not dismissible.
 */
export function DevelopmentAuthBanner({ config }: { config: AuthConfig }): JSX.Element | null {
  if (!config.development) {
    return null;
  }
  return (
    <div className="banner-development" role="alert">
      <strong>Development authentication.</strong> This deployment issues its own tokens and
      verifies no credential. Anyone who can reach it can sign in as any account.
      Environment: <code>{config.environment}</code>.
    </div>
  );
}

/**
 * The demo environment indicator: persistent, small, and impossible to mistake.
 *
 * It has to say two things at once, because both are true and a viewer needs both: the
 * *application* is real — the same API, access engine, history and risk rules a production
 * deployment runs — and the *organization* is fictional. A full-width warning would bury the
 * first under the second; a footnote would lose the second. A labelled pill in the header,
 * with the explanation one click away, carries both.
 *
 * Rendered from `GET /runtime`, which is deployment configuration. There is deliberately no
 * control here: a demo cannot be switched to live data from the browser, so the indicator
 * offers nothing to switch.
 */
export function DemoEnvironmentIndicator({
  runtime,
}: {
  runtime: RuntimeInfo;
}): JSX.Element | null {
  if (runtime.runtime_mode !== "demo") {
    return null;
  }
  const dataset = runtime.dataset;
  return (
    <details className="demo-indicator">
      <summary aria-label="Demo environment: synthetic enterprise data. Show details.">
        <strong>Demo environment</strong>
        <span className="demo-indicator-detail">
          Synthetic enterprise data{dataset ? ` · ${dataset.company_name}` : ""}
        </span>
      </summary>
      <div className="demo-indicator-panel">
        <p>
          {runtime.notice ??
            "This environment uses deterministic synthetic data. No production Active " +
              "Directory or file servers are connected."}
        </p>
        <p className="muted">
          The application is real: every answer comes from ADG&rsquo;s own API, access engine,
          history and risk rules. The organization, its people and its file servers are
          fictional.
        </p>
        <p>
          <Link href="/demo">Explore the demo scenarios</Link>
        </p>
        {dataset && (
          <dl className="demo-indicator-facts">
            <dt>Organization</dt>
            <dd>{dataset.company_name}</dd>
            <dt>Domain</dt>
            <dd>
              <code>{dataset.domain}</code>
            </dd>
            <dt>Snapshot</dt>
            <dd>{dataset.snapshot_date}</dd>
            <dt>Seed</dt>
            <dd>
              <code>{dataset.seed}</code>
            </dd>
            {Object.entries(dataset.facts).map(([name, value]) => (
              <div key={name} className="demo-indicator-fact">
                <dt>{name}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </details>
  );
}

/** Shown above data that is present but cannot be read as complete. */
export function CoverageCaveat({ caveat }: { caveat: Caveat | null }): JSX.Element | null {
  if (caveat === null) {
    return null;
  }
  return (
    <div
      className={caveat.severity === "warning" ? "banner banner-warning" : "banner banner-info"}
      role={caveat.severity === "warning" ? "alert" : "status"}
    >
      <h2>{caveat.headline}</h2>
      <p>{caveat.explanation}</p>
      <p className="muted">
        <Link href="/collectors">Open Collectors for the detail.</Link>
      </p>
    </div>
  );
}

/**
 * Everything a view shows when it is not showing data.
 *
 * One component for all five non-ready states, so that "empty because nothing is there" and
 * "empty because a collector failed" cannot drift apart into two components that word the
 * same situation differently.
 */
export function StateMessage<T>({ state }: { state: ViewState<T> }): JSX.Element | null {
  if (state.kind === "ready") {
    return null;
  }

  if (state.kind === "empty") {
    // A collection problem is a warning; a genuinely empty answer is not.
    const isAnswer = state.reason === "no-matches";
    return (
      <div
        className={isAnswer ? "banner banner-info" : "banner banner-warning"}
        role={isAnswer ? "status" : "alert"}
      >
        <h2>{state.headline}</h2>
        <p>{state.explanation}</p>
        {!isAnswer && (
          <p className="muted">
            <Link href="/collectors">See what the collectors reported.</Link>
          </p>
        )}
      </div>
    );
  }

  if (state.kind === "unauthenticated") {
    return (
      <div className="banner banner-warning" role="alert">
        <h2>{state.headline}</h2>
        <p>{state.explanation}</p>
        <p>
          <Link className="button" href="/login">
            Sign in
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="banner banner-error" role="alert">
      <h2>{state.headline}</h2>
      <p>{state.explanation}</p>
    </div>
  );
}

/**
 * A note for an account whose token carries roles ADG could not act on.
 *
 * Without it, a misassigned app role looks to the user like a broken product and to the
 * administrator like a working one.
 */
export function RoleAdviceBanner({ principal }: { principal: Principal }): JSX.Element | null {
  const problems: string[] = [];
  if (principal.capabilities.length === 0) {
    problems.push(
      "This account has no active ADG role, so every section is empty. An administrator " +
        "must assign viewer, auditor, or admin.",
    );
  }
  if (principal.inactive_roles.length > 0) {
    problems.push(
      `Reserved role(s) held: ${principal.inactive_roles.join(", ")}. These are provisioned ` +
        "for a future capability and grant nothing yet.",
    );
  }
  if (principal.unrecognized_roles.length > 0) {
    problems.push(
      `The sign-in carried values ADG does not recognize: ${principal.unrecognized_roles.join(
        ", ",
      )}. Check the app role values or the group-to-role map on the API.`,
    );
  }

  if (problems.length === 0) {
    return null;
  }

  return (
    <div className="banner banner-warning" role="alert">
      <h2>This account may see less than expected</h2>
      {problems.map((problem) => (
        <p key={problem}>{problem}</p>
      ))}
    </div>
  );
}

/** The collection verdict, rendered on its own rather than as a caveat over data. */
export function CollectionSummary({ status }: { status: CollectionStatus }): JSX.Element {
  const tone =
    status.health === "healthy"
      ? "banner banner-info"
      : status.health === "failed"
        ? "banner banner-error"
        : "banner banner-warning";
  return (
    <div className={tone} role={status.health === "healthy" ? "status" : "alert"}>
      <h2>
        Collection: <span className="visually-hidden">status </span>
        {status.health.replace("_", " ")}
      </h2>
      <p>{status.summary}</p>
      {status.concerns.map((concern) => (
        <p key={concern} className="muted">
          {concern}
        </p>
      ))}
    </div>
  );
}
