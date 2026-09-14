import type { JSX } from "react";
import Link from "next/link";

import type { AuthConfig, CollectionStatus, Principal } from "@/lib/contracts";
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
