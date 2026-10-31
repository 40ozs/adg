#!/usr/bin/env bash
# Test-only prerequisites for the Wave D browser gates, on a LOCAL demo stack right after
# `python -m app.demo_mode reset` (the canonical dataset ships no campaign and no proposal).
# Writes only through the public API, as the development accounts; never against the hosted demo.
#
#   setup-fixtures.sh http://127.0.0.1:8110 > fixtures.json
#
# 1. COPY-04's two campaigns: "COPY-04 identity review" (generated, so it has items, with two
#    identity scopes and reviewers) and "COPY-04 resource review" (an empty draft).
# 2. DEEP-TITLES' one stored What-if proposal.
# 3. REVIEW-NAMES-08's draft "REVIEW-NAMES-08 unresolved identity": an unknown SID beside the
#    named identity scope from step 1.
set -euo pipefail
API="${1:?API base URL}"
case "$API" in http://127.0.0.1:*|http://localhost:*) ;; *) echo "refusing non-loopback API $API" >&2; exit 2;; esac
R="$(cd "$(dirname "$0")/.." && pwd)"

campaigns="$(python3 -I "$R/ADG-UX-BACKEND-COPY-04-evidence/campaign-setup.py" "$API")"
proposal="$(ADG_API_URL="$API" python3 -I "$R/ADG-UX-DEEP-TITLES-06-evidence/seed-proposal.py")"
unresolved="$(CAMPAIGNS="$campaigns" python3 -I - "$API" <<'EOF'
import json, os, sys, urllib.request
API = sys.argv[1]
def req(path, body=None, token=None):
    r = urllib.request.Request(API + path, data=None if body is None else json.dumps(body).encode(),
        headers={"content-type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.load(resp)
person = json.loads(os.environ["CAMPAIGNS"])["person"]["sid"]
unknown = person.rsplit("-", 1)[0] + "-999999"  # same domain, a RID nobody holds
tok = req("/auth/dev/login", {"username": "governance"})["access_token"]
made = req("/api/v1/governance/campaigns", {
    "name": "REVIEW-NAMES-08 unresolved identity", "focus": "principal",
    "baseline_at": "2026-10-01T00:00:00Z",
    "scopes": [{"kind": "principal", "key": unknown}, {"kind": "principal", "key": person}]}, tok)
print(json.dumps({"campaign_id": made["campaign_id"], "unknown_sid": unknown, "scopes": made["scopes"]}))
EOF
)"
printf '{"copy04": %s,\n "proposal": "%s",\n "unresolved": %s}\n' "$campaigns" "$proposal" "$unresolved"
