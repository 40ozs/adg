/**
 * Turning the alert feed into the sentences a page shows.
 *
 * Pure, and for the same reason {@link module:lib/risks} is: the interesting decisions are
 * wordings, and a wording that can mislead somebody in a monitoring feed deserves a test.
 *
 * ## The two things a reader must be able to tell apart
 *
 * **A quiet feed and a broken one.** {@link queueNotice} reads the delivery queue, which is
 * the only place that distinction is visible: depth alone cannot separate a busy pipeline
 * from one nothing is draining, and an abandoned delivery is an alert somebody was meant to
 * receive and did not.
 *
 * **An alert that happened once and one that happened eleven times.** `suppressed_total`
 * above zero means a cooldown folded occurrences into a single notification.
 * {@link suppressionNotice} says so in words, because the number on its own reads as a
 * technical detail rather than as "you were told about one of these".
 */

import type { AlertQueueResponse, AlertView, WatchKindView } from "@/lib/contracts";

/** What a trigger is called in a list. The API carries the long description. */
export function triggerWording(trigger: string): string {
  switch (trigger) {
    case "watched_group_membership_changed":
      return "Group membership changed";
    case "watched_resource_acl_changed":
      return "Permissions changed";
    case "watched_access_expanded":
      return "Access expanded";
    case "critical_risk_finding_opened":
      return "Risk finding opened";
    default:
      return trigger;
  }
}

export function watchKindWording(kind: string): string {
  switch (kind) {
    case "resource":
      return "Directory";
    case "share":
      return "Share";
    case "group":
      return "Group";
    default:
      return kind;
  }
}

/**
 * How an alert's status should read, which depends on its lifecycle.
 *
 * A transient alert is always "open" in the database because an access control list edit
 * cannot un-happen. Rendering that as **Open** would invite somebody to go and close it, so
 * a transient alert is shown as a notice instead.
 */
export function statusWording(alert: AlertView): string {
  if (alert.lifecycle === "transient") {
    return "Notice";
  }
  return alert.status === "resolved" ? "Resolved" : "Open";
}

export function statusTone(alert: AlertView): "bad" | "warn" | "ok" {
  if (alert.lifecycle === "transient") {
    return "warn";
  }
  return alert.status === "resolved" ? "ok" : "bad";
}

/**
 * What a suppression count means, in words. `null` when nothing was held back.
 *
 * The count matters because a notification that silently stood for eleven changes
 * under-reports by ten, and nothing else on the row would say so.
 */
export function suppressionNotice(alert: AlertView): string | null {
  if (alert.suppressed_total === 0) {
    return null;
  }
  return (
    `This has happened ${alert.occurrence_count} time(s) and ${alert.suppressed_total} ` +
    "occurrence(s) were not delivered — held back by the watch's cooldown, or identical to " +
    "what was already sent. Every one of them is recorded; open the alert to see them."
  );
}

/** Why a particular occurrence was not delivered. */
export function suppressionReasonWording(reason: string | null): string | null {
  switch (reason) {
    case "identical_content":
      return "Identical to what was already delivered — detection ran twice over the same change.";
    case "within_cooldown":
      return "Something new, inside the quiet window this watch is configured for.";
    case "watch_disabled":
      return "The watch was turned off. Recorded anyway, so turning it back on shows what it missed.";
    case null:
      return null;
    default:
      return reason;
  }
}

export interface QueueNotice {
  tone: "warning" | "error" | "ok";
  headline: string;
  explanation: string;
}

/**
 * Whether the pipeline is actually delivering. Returned in every case.
 *
 * The order of the checks is the order of severity, and abandonment comes first: an
 * abandoned delivery is the one state that means somebody was meant to be told and was not,
 * and it does not clear itself.
 */
export function queueNotice(queue: AlertQueueResponse): QueueNotice {
  if (queue.abandoned > 0) {
    return {
      tone: "error",
      headline: `${queue.abandoned} alert(s) were never delivered`,
      explanation:
        "Delivery was given up on, either because the retry schedule ran out or because the " +
        "destination reported a failure retrying cannot fix. These are alerts somebody was " +
        "meant to receive and did not; they are kept, and each one records what stopped it.",
    };
  }
  if (queue.stale > 0) {
    return {
      tone: "warning",
      headline: `${queue.stale} delivery(s) have been waiting more than fifteen minutes`,
      explanation:
        "That usually means nothing is draining the queue. Run `python -m app.operations " +
        "drain-alerts`, or schedule it. A queue nobody drains grows quietly: the alerts are " +
        "recorded, and nobody is being told about them.",
    };
  }
  const pending = (queue.depth.pending ?? 0) + (queue.depth.failed ?? 0);
  if (pending > 0) {
    return {
      tone: "warning",
      headline: `${pending} delivery(s) are waiting`,
      explanation: "Queued and not yet sent. Nothing here is overdue.",
    };
  }
  return {
    tone: "ok",
    headline: "Everything raised has been delivered",
    explanation:
      "Nothing is waiting and nothing was abandoned. A quiet feed below therefore means a " +
      "quiet estate rather than a pipeline that stopped.",
  };
}

/**
 * Whether the policy has anywhere to deliver to.
 *
 * `null` when it does. The policy lines the API serves already say "NONE CONFIGURED"; this
 * turns that into something a page can show above the feed, because an installation with no
 * destination has a queue that only grows and no other field would say why.
 */
export function deliveryDestinationNotice(queue: AlertQueueResponse): string | null {
  const nowhere = queue.policy.some(
    (line) => line.includes("NONE CONFIGURED") || line.includes("delivered nowhere"),
  );
  return nowhere
    ? "This installation has no enabled destination, so alerts are recorded and delivered " +
        "nowhere. Configure a sink in the alert policy file."
    : null;
}

/** The triggers a watch of this kind may subscribe to, for a form. Served, never guessed. */
export function triggersFor(kinds: WatchKindView[], kind: string): string[] {
  return kinds.find((entry) => entry.kind === kind)?.triggers ?? [];
}

/** A cooldown in seconds, as something a person reads. */
export function cooldownWording(seconds: number): string {
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  return `${seconds} seconds`;
}
