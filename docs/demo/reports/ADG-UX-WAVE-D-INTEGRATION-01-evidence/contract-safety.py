"""Contract and privacy checks for ScopeView.display_name on a LOCAL demo API.

Usage: python3 -I contract-safety.py http://127.0.0.1:8110 > contract-safety.txt
Reads only (plus development sign-ins). Exits non-zero if any check fails.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API = sys.argv[1].rstrip("/")
if not API.startswith(("http://127.0.0.1:", "http://localhost:")):
    raise SystemExit(f"refusing non-loopback API {API}")
failed = 0


def call(path, body=None, token=None):
    headers = {"content-type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(API + path, data, headers)) as resp:
        return json.load(resp)


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


token = {r: call("/auth/dev/login", {"username": r})["access_token"] for r in ("admin", "auditor", "governance", "viewer")}
campaigns = call("/api/v1/governance/campaigns", token=token["admin"])["items"]
scopes = [s for c in campaigns for s in c["scopes"]]
identity = [s for s in scopes if s["kind"] == "principal"]

check("every governance scope has exactly kind, key, display_name",
      all(sorted(s) == ["display_name", "key", "kind"] for s in scopes), f"{len(scopes)} scopes in {len(campaigns)} campaigns")
check("kinds are the existing scope tokens", {s["kind"] for s in scopes} <= {"principal", "share", "directory_tree", "server"},
      ", ".join(sorted({s["kind"] for s in scopes})))
check("identity keys are still SIDs", all(s["key"].startswith("S-1-") for s in identity), f"{len(identity)} identity scopes")
check("non-identity scopes carry display_name null", all(s["display_name"] is None for s in scopes if s["kind"] != "principal"))

# Privacy: the name is one of display / account / last-known name, never the UPN or an email.
for s in {s["key"]: s for s in identity}.values():
    if not s["display_name"]:
        continue
    found = call(f"/api/v1/search?q={urllib.parse.quote(s['key'])}", token=token["admin"])
    hits = [h for h in found["identities"] if h.get("sid") == s["key"]]
    upn = hits[0].get("user_principal_name") if hits else None
    allowed = {hits[0].get("display_name"), hits[0].get("sam_account_name")} if hits else set()
    check(f"display_name for …{s['key'][-5:]} is a presentation label, not the UPN",
          "@" not in s["display_name"] and s["display_name"] != upn and (not hits or s["display_name"] in allowed),
          f"display_name {s['display_name']!r}, UPN {'set' if upn else 'none'}, search hit {'yes' if hits else 'no'}")

for role in ("auditor", "governance"):
    listed = call("/api/v1/governance/campaigns", token=token[role])["items"]
    check(f"{role} reads the same names", [s["display_name"] for c in listed for s in c["scopes"]] == [s["display_name"] for s in scopes])
try:
    call("/api/v1/governance/campaigns", token=token["viewer"])
    check("viewer is still refused", False, "HTTP 200")
except urllib.error.HTTPError as error:
    check("viewer is still refused", error.code == 403, f"HTTP {error.code}")

runs = call("/api/v1/scan-runs?limit=50", token=token["admin"])
run_ids = [r["run_id"] for r in next(v for v in runs.values() if isinstance(v, list))]
shapes = set()
for run_id in run_ids:
    run = call(f"/api/v1/scan-runs/{run_id}", token=token["admin"])
    for field in ("declared_scopes", "reconciled_scopes"):
        shapes |= {tuple(sorted(s)) for s in run.get(field) or []}
check("scan-run scopes (ScanScopeView) are still exactly kind, key", shapes == {("key", "kind")},
      f"{len(run_ids)} runs, key sets {sorted(shapes)}")

print(f"\n{'OK' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
