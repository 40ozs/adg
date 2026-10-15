"""Collect every hydration-gate run into one evidence file, in the order the runs were made.

    python3 -I aggregate-runs.py <runs directory> <output json>

Each run directory holds the gate's ``summary.json`` and ``loads.jsonl``. Every run is listed,
pilots and positive controls included; nothing is filtered. A run's 418 occurrences are copied
whole (route, phase, the readyState React reported at), and ``loads.jsonl`` is reduced to one
line per load so the file stays reviewable.
"""

import json
import sys
from pathlib import Path

runs_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
runs = []
for run in sorted(runs_dir.iterdir(), key=lambda p: (p / "summary.json").stat().st_mtime if (p / "summary.json").exists() else 0):
    summary_file = run / "summary.json"
    if not summary_file.exists():
        runs.append({"run": run.name, "summary": None, "note": "no summary: the run did not finish"})
        continue
    summary = json.loads(summary_file.read_text())
    loads = [json.loads(line) for line in (run / "loads.jsonl").read_text().splitlines() if '"phase"' in line]
    runs.append(
        {
            "run": run.name,
            "label": summary["label"],
            "control": summary["control"],
            "base": summary["base"],
            "rounds": summary["rounds"],
            "started": summary["started"],
            "finished": summary["finished"],
            "full_document_loads": summary["fullDocumentLoads"],
            "client_transitions": summary["clientTransitions"],
            "total": summary["total"],
            "phases": summary["phases"],
            "loads_with_418": [
                {"round": o["round"], "phase": o["phase"], "route": o["route"], "ready_state": [r["readyState"] for r in o["react"]], "first": o["first"].split("\n")[0]}
                for o in summary["occurrences418"]
            ],
            "other_errors": summary["otherErrors"],
            "failed_steps": summary["failedSteps"],
            "phone_overflow": summary["phoneOverflow"],
            "known_errors": summary["knownErrors"],
            "loads": [
                f'{r["round"]}|{r["phase"]}|{r["route"]}|{r.get("ms")}ms|418x{r["n418"]}|other x{len(r["other"])}|{"ok" if r["ok"] else "FAILED: " + r["detail"][:120]}'
                for r in loads
            ],
        }
    )
out.write_text(json.dumps({"runs": runs}, indent=1, ensure_ascii=False) + "\n")
for r in runs:
    if r.get("total"):
        t = r["total"]
        print(f'{r["run"]:<34} {r["label"]:<55} loads {r["full_document_loads"]:>4}  transitions {r["client_transitions"]:>3}  418-loads {t["with418"]:>3}  other {t["withOtherErrors"]}  failed {t["failedSteps"]}')
    else:
        print(r["run"], r.get("note"))
