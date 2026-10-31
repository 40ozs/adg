"""Seed two review campaigns on a local demo stack (the demo dataset ships none).

Usage: python3 -I campaign-setup.py http://127.0.0.1:8040 > campaigns.json
Writes only through the public API, as the development governance administrator.
"""
import json, sys, urllib.request

API = sys.argv[1].rstrip("/")

def req(path, body=None, token=None):
    r = urllib.request.Request(API + path, data=None if body is None else json.dumps(body).encode(),
        headers={"content-type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.load(resp)

tok = req("/auth/dev/login", {"username": "governance"})["access_token"]
demo = req("/api/v1/demo", token=tok)
subjects = [dict(s, scenario=sc["key"], resource=sc.get("resource")) for sc in demo["scenarios"] for s in sc.get("subjects", [])]
group = next(s for s in subjects if s["kind"] == "group")
person = next((s for s in subjects if s["kind"] == "person" and s["scenario"] == "F"), None)
resource = person["resource"]
share = "|".join(resource.lstrip("\\").split("\\")[:2]).lower()
baseline = "2026-10-01T00:00:00Z"

by_identity = req("/api/v1/governance/campaigns", {
    "name": "COPY-04 identity review", "focus": "principal", "baseline_at": baseline,
    "scopes": [{"kind": "principal", "key": group["sid"]}, {"kind": "principal", "key": person["sid"]}],
}, tok)
cid = by_identity["campaign_id"]
req(f"/api/v1/governance/campaigns/{cid}/generation", {}, tok)
req(f"/api/v1/governance/campaigns/{cid}/assignments", {
    "reviewer_subject": "reviewer", "reviewer_display_name": "Development Reviewer",
    "scope": {"kind": "principal", "key": group["sid"]}}, tok)
req(f"/api/v1/governance/campaigns/{cid}/assignments", {
    "reviewer_subject": "auditor", "reviewer_display_name": "Development Auditor",
    "scope": {"kind": "principal", "key": person["sid"]}}, tok)

by_resource = req("/api/v1/governance/campaigns", {
    "name": "COPY-04 resource review", "focus": "resource", "baseline_at": baseline,
    "scopes": [{"kind": "share", "key": share}, {"kind": "directory_tree", "key": resource}],
}, tok)

print(json.dumps({"identity_campaign": cid, "resource_campaign": by_resource["campaign_id"],
                  "group": group, "person": person, "share": share, "resource": resource}, indent=2))
