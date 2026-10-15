"""Aggregate the React #418 gate runs (ADG-NEXT16-POST-DEMO-INTEGRATION-01).

    python3 -I aggregate.py <run file.jsonl> [...]

A "load" is one full-document load (cold, cold-390, reload-first, reload); a client-side
navigation is counted separately because it does not hydrate a document. A load "has #418"
when any channel (console, pageerror, the init-script hook) reported it during that load.
"Other application errors" are console or page errors that are neither #418 nor the known
/favicon.ico 404.
"""

from __future__ import annotations

import collections
import json
import re
import sys

FULL = {"cold", "cold-390", "reload-first", "reload"}


def page_of(url: str) -> str:
    return re.sub(r"\?.*", "", url.split(" (")[0]) + ("?time_budget_ms=1" if "time_budget_ms=1" in url else "")


def main(paths: list[str]) -> None:
    by_label: dict[str, list[dict]] = collections.defaultdict(list)
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    run = json.loads(line)
                    by_label[run["label"]].append(run)
    for label, runs in by_label.items():
        full = [ld for r in runs for ld in r["detail"] if ld["kind"] in FULL]
        nav = [ld for r in runs for ld in r["detail"] if ld["kind"] == "client-nav"]
        hit = [ld for ld in full if ld["react418"]]
        nav_hit = [ld for ld in nav if ld["react418"]]
        other = [e for r in runs for ld in r["detail"] for e in ld["app_errors"] if not re.search(r"#418|Hydration", e)]
        states = collections.Counter(
            m.group(1)
            for ld in hit
            for msg in ld.get("react418_messages", [])[:]
            for m in [re.search(r"\[(loading|interactive|complete)\]", msg)]
            if m and "reportError" in msg
        )
        pages = collections.Counter(f"{ld['kind']} {page_of(ld['url'])}" for ld in hit)
        failures = [f for r in runs for f in r["failures"]]
        http = [h for r in runs for h in r["http_errors"]]
        overflow = [ld for r in runs for ld in r["detail"] if (ld.get("horizontal_overflow_px") or 0) > 0]
        same_doc = sum(1 for ld in nav if ld.get("same_document") is True)
        print(f"== {label}: {len(runs)} runs")
        print(f"   full loads {len(full)}; with #418 {len(hit)} ({100 * len(hit) / max(len(full), 1):.1f}%)")
        print(f"   client-side navigations {len(nav)} (same document {same_doc}); with #418 {len(nav_hit)}")
        print(f"   runs with any #418: {sum(1 for r in runs if any(ld['react418'] for ld in r['detail']))} of {len(runs)}")
        if states:
            print(f"   readyState at report (hook): {dict(states)}")
        if pages:
            print(f"   #418 by page: {dict(pages.most_common())}")
        print(f"   other application errors {len(other)}; failed steps {len(failures)}; HTTP >= 400 {len(http)}; 390px overflow {len(overflow)}")
        for e in (other + failures + http)[:5]:
            print(f"     - {e[:200]}")


if __name__ == "__main__":
    main(sys.argv[1:])
