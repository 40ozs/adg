/**
 * The wordings an alert feed shows.
 *
 * The two mistakes this file exists to prevent:
 *
 * - **A quiet feed read as a quiet estate.** Only the delivery queue can tell those apart,
 *   and only if the page says what it found there.
 * - **A "resolved" badge on something that was never a condition.** An access control list
 *   edit cannot un-happen; showing it as open invites somebody to go and close it.
 */

import { describe, expect, it } from "vitest";

import type { AlertQueueResponse, AlertView, WatchKindView } from "@/lib/contracts";
import {
  cooldownWording,
  deliveryDestinationNotice,
  queueNotice,
  statusTone,
  statusWording,
  suppressionNotice,
  suppressionReasonWording,
  triggerWording,
  triggersFor,
  watchKindWording,
} from "@/lib/alerts";

function alert(overrides: Partial<AlertView> = {}): AlertView {
  return {
    alert_key: "a".repeat(64),
    trigger: "watched_resource_acl_changed",
    trigger_description: "An access control entry on a place you are watching moved.",
    lifecycle: "transient",
    status: "open",
    summary: "The access control list of Finance changed.",
    watch_id: "11111111-1111-1111-1111-111111111111",
    watch_label: "Finance directory",
    resource_key: "\\\\fs01\\finance",
    share_key: null,
    principal_key: null,
    first_raised_at: "2026-09-14T09:00:00Z",
    last_raised_at: "2026-09-14T09:05:00Z",
    last_notified_at: "2026-09-14T09:00:00Z",
    resolved_at: null,
    occurrence_count: 1,
    suppressed_total: 0,
    suppressed_since_notice: 0,
    payload: {},
    detail_url: "/api/v1/alerts/" + "a".repeat(64),
    ...overrides,
  };
}

function queue(overrides: Partial<AlertQueueResponse> = {}): AlertQueueResponse {
  return {
    depth: { pending: 0, delivered: 4, failed: 0, abandoned: 0 },
    stale: 0,
    abandoned: 0,
    oldest_pending_at: null,
    policy: ["Alert policy version 'default'.", "  sink 'operator-log' (log): enabled, all triggers"],
    ...overrides,
  };
}

describe("an alert's status", () => {
  it("reads as a notice when nothing can resolve it", () => {
    // A transient alert is always `open` in the database because an edit cannot un-happen.
    // Rendering that as "Open" would invite somebody to go and close it.
    const notice = alert({ lifecycle: "transient" });

    expect(statusWording(notice)).toBe("Notice");
    expect(statusTone(notice)).toBe("warn");
  });

  it("reads as open or resolved for a condition that can end", () => {
    const open = alert({ lifecycle: "stateful", status: "open" });
    const closed = alert({ lifecycle: "stateful", status: "resolved" });

    expect(statusWording(open)).toBe("Open");
    expect(statusTone(open)).toBe("bad");
    expect(statusWording(closed)).toBe("Resolved");
    expect(statusTone(closed)).toBe("ok");
  });
});

describe("the suppression notice", () => {
  it("is silent when nothing was held back", () => {
    expect(suppressionNotice(alert())).toBeNull();
  });

  it("says how many occurrences nobody was told about", () => {
    // A notification that silently stood for eleven changes under-reports by ten, and
    // nothing else on the row would say so.
    const notice = suppressionNotice(alert({ occurrence_count: 11, suppressed_total: 10 }));

    expect(notice).toMatch(/11 time/);
    expect(notice).toMatch(/10 occurrence/);
    expect(notice).toMatch(/recorded/);
  });

  it("explains each reason in the terms an operator would ask in", () => {
    expect(suppressionReasonWording("identical_content")).toMatch(/detection ran twice/);
    expect(suppressionReasonWording("within_cooldown")).toMatch(/quiet window/);
    expect(suppressionReasonWording("watch_disabled")).toMatch(/what it missed/);
    expect(suppressionReasonWording(null)).toBeNull();
  });
});

describe("the queue notice", () => {
  it("leads with abandonment, because it does not clear itself", () => {
    const notice = queueNotice(
      queue({ abandoned: 2, stale: 9, depth: { pending: 3, abandoned: 2 } }),
    );

    expect(notice.tone).toBe("error");
    expect(notice.headline).toMatch(/never delivered/);
    expect(notice.explanation).toMatch(/meant to receive and did not/);
  });

  it("names staleness, which is the one thing depth cannot say", () => {
    // Depth alone cannot tell a busy pipeline from one nothing is draining.
    const notice = queueNotice(queue({ stale: 9, depth: { pending: 9 } }));

    expect(notice.tone).toBe("warning");
    expect(notice.explanation).toMatch(/drain-alerts/);
  });

  it("distinguishes waiting from overdue", () => {
    const notice = queueNotice(queue({ depth: { pending: 3 } }));

    expect(notice.headline).toMatch(/3 delivery/);
    expect(notice.explanation).toMatch(/Nothing here is overdue/);
  });

  it("says a quiet feed can be believed when nothing is waiting", () => {
    const notice = queueNotice(queue());

    expect(notice.tone).toBe("ok");
    expect(notice.explanation).toMatch(/quiet estate/);
  });
});

describe("the delivery destination notice", () => {
  it("is silent when something is configured to receive alerts", () => {
    expect(deliveryDestinationNotice(queue())).toBeNull();
  });

  it("says when an installation delivers nowhere", () => {
    // Such an installation has a queue that only grows, and no other field would say why.
    const notice = deliveryDestinationNotice(
      queue({ policy: ["  sinks: NONE CONFIGURED -- alerts are recorded and delivered nowhere."] }),
    );

    expect(notice).toMatch(/no enabled destination/);
  });
});

describe("the watch form's trigger list", () => {
  const kinds: WatchKindView[] = [
    { kind: "resource", triggers: ["watched_resource_acl_changed"], covers: "One directory." },
    { kind: "group", triggers: ["watched_group_membership_changed"], covers: "One group." },
  ];

  it("comes from the server, per kind", () => {
    // A second copy of this rule is a second copy that can be wrong, and the wrong one is
    // always the one somebody trusts: a directory watch offered a membership trigger would
    // be saved, listed, and silent forever.
    expect(triggersFor(kinds, "resource")).toEqual(["watched_resource_acl_changed"]);
    expect(triggersFor(kinds, "group")).toEqual(["watched_group_membership_changed"]);
  });

  it("offers nothing for a kind the server did not describe", () => {
    expect(triggersFor(kinds, "share")).toEqual([]);
  });
});

describe("wordings", () => {
  it("names triggers and watch kinds in a reader's terms", () => {
    expect(triggerWording("watched_access_expanded")).toBe("Access expanded");
    expect(triggerWording("critical_risk_finding_opened")).toBe("Risk finding opened");
    expect(watchKindWording("resource")).toBe("Directory");
  });

  it("renders a cooldown as a duration rather than a number of seconds", () => {
    expect(cooldownWording(900)).toBe("15 minutes");
    expect(cooldownWording(60)).toBe("1 minute");
    expect(cooldownWording(3600)).toBe("1 hour");
    expect(cooldownWording(90)).toBe("90 seconds");
  });

  it("falls back to the raw value rather than inventing one", () => {
    expect(triggerWording("something_new")).toBe("something_new");
  });
});
