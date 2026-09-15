import type { JSX } from "react";
import Link from "next/link";

import { fetchFinding, fetchFindings, fetchRiskRules, fetchRiskSummary } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { FindingSummaryView, RiskRuleView } from "@/lib/contracts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation } from "@/lib/paging";
import { SEVERITIES } from "@/lib/risks";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { PagePosition, Pager } from "@/components/Pager";
import {
  CoverageBanner,
  EvidenceDrawer,
  FindingTable,
  RiskSummaryStrip,
} from "@/components/Risks";
import { SignedOutNotice } from "@/components/SignedOutNotice";

const PATH = "/risks";

/**
 * Risks: what the rules found, and everything needed to read it honestly.
 *
 * The page is filtered by default — open findings, most consequential first — because an
 * unfiltered report over a real estate is mostly history, and a reader who has to skim past
 * four hundred resolved findings will stop reading the page.
 *
 * Three things above the table make that defensible, and each one answers a question a
 * reader would otherwise have to know to ask:
 *
 * - **What the last evaluation covered.** Shown always, including when it is good news. An
 *   empty table under "the rules have never been evaluated" means *nothing has looked*, and
 *   the single most dangerous sentence this product could accidentally say is the one that
 *   reads as *nothing is wrong*.
 * - **What the configuration is hiding.** A disabled rule reports nothing, and no resource
 *   being marked sensitive means the sensitive-resource rule reports nothing either.
 * - **What the filter is hiding.** The number of findings the status filter excluded.
 *
 * The evidence drawer is a URL rather than a click handler: `?finding=<key>` renders the
 * whole records below the table, server-side. That is deliberate and not a limitation — the
 * drawer is then linkable, which is exactly what somebody wants when they are pasting a
 * finding into a ticket.
 */
export default async function RisksPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const severity = many(params.severity);
  const rule = many(params.rule);
  const showResolved = single(params.status) === "all";
  const place = single(params.place);
  const principal = single(params.principal);
  const openKey = single(params.finding) ?? null;

  const statuses = showResolved ? ["open", "resolved"] : ["open"];
  const query = {
    status: statuses,
    ...(severity.length > 0 ? { severity } : {}),
    ...(rule.length > 0 ? { rule } : {}),
    ...(place !== undefined ? { place } : {}),
    ...(principal !== undefined ? { principal } : {}),
  };

  const trail = decodeTrail(params.trail);
  const [summary, findings, rules, drawer] = await Promise.all([
    fetchRiskSummary(token),
    fetchFindings(token, { ...query, cursor: cursorFor(trail) }),
    fetchRiskRules(token),
    openKey !== null ? fetchFinding(token, openKey) : Promise.resolve(null),
  ]);

  // `isEmpty` is deliberately never true. `classify` interprets an empty list against the
  // *collector* coverage, which is the wrong question here and would produce "ADG could not
  // check whether collection has run" above a report whose own coverage is stated, exactly,
  // in the banner two elements up. What this page still needs `classify` for is the failure
  // and session states, which it handles better than an ad-hoc check would.
  const state = classify(findings, { isEmpty: () => false, subject: "findings" });

  const linkParams = {
    severity,
    rule,
    status: showResolved ? "all" : null,
    place: place ?? null,
    principal: principal ?? null,
  };
  const navigation = pageNavigation({
    basePath: PATH,
    params: linkParams,
    trailParam: "trail",
    trail,
    nextCursor: findings.ok ? findings.data.page.next_cursor : null,
  });

  const catalog: RiskRuleView[] = rules.ok ? rules.data.rules : [];

  return (
    <>
      <h1>Risks</h1>
      <p className="muted">
        Each finding is a named rule that matched a shape in the collected facts, with the
        records it matched on. Nothing here is a score, and severities are never added up.
      </p>

      {summary.ok && <CoverageBanner coverage={summary.data.coverage} />}

      {summary.ok && (
        <RiskSummaryStrip
          severityCounts={summary.data.severity_counts}
          ruleCounts={summary.data.rule_counts}
          statusCounts={summary.data.status_counts}
          shownStatuses={statuses}
          configuration={summary.data.configuration}
          rules={catalog}
        />
      )}

      <Filters
        severity={severity}
        rule={rule}
        rules={catalog}
        showResolved={showResolved}
        place={place}
        principal={principal}
      />

      <PagePosition trail={trail} firstPageHref={hrefWith(PATH, linkParams)} />

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : state.data.items.length === 0 ? (
        <div className="card">
          <h2>Nothing matched these filters</h2>
          <p>
            No finding matched the filters in use. That is not the same as a clean estate —
            the counts above are taken over everything the last evaluation covered, and the
            banner above them says how much of the estate that was.
          </p>
        </div>
      ) : (
        <>
          <FindingTable
            findings={state.data.items}
            drawerHref={(finding) => drawerHref(finding, linkParams, params.trail)}
            openKey={openKey}
          />
          <Pager navigation={navigation} page={state.data.page} subject="findings" />
        </>
      )}

      {drawer !== null && drawer.ok && (
        <EvidenceDrawer detail={drawer.data} closeHref={hrefWith(PATH, linkParams)} />
      )}
      {drawer !== null && !drawer.ok && (
        <div className="banner banner-warning" role="status">
          <h2>That finding could not be read</h2>
          <p>
            It may have been produced by an evaluation that has since been rolled back, or
            the key in the URL may be wrong. The list above is unaffected.
          </p>
        </div>
      )}
    </>
  );
}

/**
 * The drawer's link, carrying the reader's page with it.
 *
 * `trail` is passed through unchanged rather than re-derived. Without it, opening the
 * evidence for a row on page three would silently return the reader to page one with the
 * drawer open — and the row they clicked would no longer be on screen.
 */
function drawerHref(
  finding: FindingSummaryView,
  params: Record<string, string | string[] | null>,
  trail: string | string[] | undefined,
): string {
  return hrefWith(PATH, {
    ...params,
    finding: finding.key,
    trail: single(trail) ?? null,
  });
}

function Filters({
  severity,
  rule,
  rules,
  showResolved,
  place,
  principal,
}: {
  severity: string[];
  rule: string[];
  rules: RiskRuleView[];
  showResolved: boolean;
  place: string | undefined;
  principal: string | undefined;
}): JSX.Element {
  const scope = { place: place ?? null, principal: principal ?? null };
  const base = { ...scope, status: showResolved ? "all" : null };
  return (
    <nav className="filter-strip" aria-label="Filters">
      <p>
        Severity:{" "}
        <FilterLink
          label="any"
          href={hrefWith(PATH, { ...base, rule })}
          active={severity.length === 0}
        />
        {SEVERITIES.map((value) => (
          <span key={value}>
            {" · "}
            <FilterLink
              label={value}
              href={hrefWith(PATH, { ...base, rule, severity: toggle(severity, value) })}
              active={severity.includes(value)}
            />
          </span>
        ))}
      </p>
      <p>
        Status:{" "}
        <FilterLink
          label="open only"
          href={hrefWith(PATH, { ...scope, severity, rule })}
          active={!showResolved}
        />
        {" · "}
        <FilterLink
          label="including resolved"
          href={hrefWith(PATH, { ...scope, severity, rule, status: "all" })}
          active={showResolved}
        />
      </p>
      {rules.length > 0 && (
        <p>
          Rule:{" "}
          <FilterLink
            label="any"
            href={hrefWith(PATH, { ...base, severity })}
            active={rule.length === 0}
          />
          {rules
            .filter((entry) => entry.enabled)
            .map((entry) => (
              <span key={entry.rule_id}>
                {" · "}
                <FilterLink
                  label={entry.rule_id}
                  href={hrefWith(PATH, {
                    ...base,
                    severity,
                    rule: toggle(rule, entry.rule_id),
                  })}
                  active={rule.includes(entry.rule_id)}
                />
              </span>
            ))}
        </p>
      )}
      {(place !== undefined || principal !== undefined) && (
        <p>
          Scope: {place !== undefined && <code>{place}</code>}
          {principal !== undefined && <code>{principal}</code>} ·{" "}
          <Link href={hrefWith(PATH, { severity, rule, status: showResolved ? "all" : null })}>
            clear
          </Link>
        </p>
      )}
      <p className="muted">
        <Link href="/risks/alerts">Alerts and watches</Link>
      </p>
    </nav>
  );
}

function FilterLink({
  label,
  href,
  active,
}: {
  label: string;
  href: string;
  active: boolean;
}): JSX.Element {
  return active ? (
    <strong aria-current="true">{label}</strong>
  ) : (
    <Link href={href}>{label}</Link>
  );
}

/** Add or remove one value from a repeatable filter. */
function toggle(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function many(value: string | string[] | undefined): string[] {
  if (value === undefined) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}
