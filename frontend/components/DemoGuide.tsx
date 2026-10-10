import type { JSX } from "react";
import Link from "next/link";

import type { DemoGeneration, DemoOverview, DemoPolicyEntry, DemoScenario } from "@/lib/contracts";
import { explainHref } from "@/lib/explanation";
import { principalHrefForKey } from "@/lib/identity";
import { directoryHref } from "@/lib/resources";

/**
 * The demo guide's parts. Every link points into the real application — the directory page,
 * the principal page, the access explanation — so a scenario is something to inspect with
 * ADG itself, not a description of what ADG would show. Nothing here is a fixture: the
 * scenarios, generations and policy all come from `GET /api/v1/demo`.
 */

export function DemoScenarioList({ scenarios }: { scenarios: DemoScenario[] }): JSX.Element {
  if (scenarios.length === 0) {
    return <p className="muted">This dataset defines no guided scenarios.</p>;
  }
  return (
    <ol className="demo-scenarios">
      {scenarios.map((scenario) => (
        <li key={scenario.key} className="card demo-scenario" aria-label={`Scenario ${scenario.key}`}>
          <h3>
            <span className="demo-scenario-key">{scenario.key}</span> {scenario.title}
          </h3>
          <p>{scenario.summary}</p>
          <p>
            Folder: <Link href={directoryHref(scenario.resource)}>{scenario.resource}</Link>
            {scenario.related_resources.map((related) => (
              <span key={related}>
                {" · "}
                <Link href={directoryHref(related)}>{related}</Link>
              </span>
            ))}
          </p>
          {scenario.subjects.length > 0 && (
            <ul className="demo-scenario-subjects">
              {scenario.subjects.map((subject) => (
                <li key={subject.sid}>
                  <Link href={principalHrefForKey(subject.sid)}>{subject.name}</Link>{" "}
                  <span className="muted">({subject.kind})</span>
                  {subject.kind === "person" && (
                    <>
                      {" — "}
                      <Link href={explainHref(subject.sid, scenario.resource)}>
                        explain their access to this folder
                      </Link>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

export function DemoGenerationList({
  generations,
  baseline,
}: {
  generations: DemoGeneration[];
  baseline: number;
}): JSX.Element {
  return (
    <table>
      <thead>
        <tr>
          <th>Scan generation</th>
          <th>Observed</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        {generations.map((generation) => (
          <tr key={generation.name}>
            <td>
              <code>{generation.name}</code>
              {generation.in_baseline ? "" : " (beyond the baseline)"}
            </td>
            <td>{generation.description}</td>
            <td className={generation.applied ? "status-ok" : "muted"}>
              {generation.applied ? "applied" : "not yet applied"}
            </td>
          </tr>
        ))}
      </tbody>
      <caption className="muted">
        A reset installs the first {baseline} of {generations.length}. Each generation is a set
        of collector-protocol runs posted through ADG&rsquo;s own ingestion routes.
      </caption>
    </table>
  );
}

const TREATMENT_LABEL: Record<DemoPolicyEntry["treatment"], string> = {
  supported: "Works as in production, on demo records",
  simulated: "Simulation only — the output is inert",
  unavailable: "Not available in the demo",
};

export function DemoPolicySummary({ policy }: { policy: DemoPolicyEntry[] }): JSX.Element {
  const treatments: DemoPolicyEntry["treatment"][] = ["unavailable", "simulated", "supported"];
  return (
    <dl className="demo-policy">
      {treatments.map((treatment) => {
        const entries = policy.filter((entry) => entry.treatment === treatment);
        return (
          <div key={treatment}>
            <dt>
              {TREATMENT_LABEL[treatment]} ({entries.length})
            </dt>
            <dd>
              <ul>
                {entries.map((entry) => (
                  <li key={`${entry.method} ${entry.path}`}>
                    {entry.operation} <code>{`${entry.method} ${entry.path}`}</code>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** The whole guide, for one overview. */
export function DemoGuide({ overview }: { overview: DemoOverview }): JSX.Element {
  const dataset = overview.dataset;
  return (
    <>
      <h1>Demo guide: {dataset.company_name}</h1>
      <p>{dataset.description}</p>
      <p className="muted">
        Every page in this environment is the real ADG application reading synthetic data for{" "}
        {dataset.company_name} (<code>{dataset.domain}</code>, snapshot {dataset.snapshot_date},
        seed <code>{dataset.seed}</code>). No Active Directory, file server or collector is
        connected, and nothing here can change real infrastructure.
      </p>

      <h2>Scenarios</h2>
      <DemoScenarioList scenarios={overview.scenarios} />

      <h2>Scan history</h2>
      <DemoGenerationList
        generations={overview.generations}
        baseline={overview.baseline_generations}
      />

      <h2>What the demo lets you change</h2>
      <DemoPolicySummary policy={overview.mutation_policy} />
    </>
  );
}
