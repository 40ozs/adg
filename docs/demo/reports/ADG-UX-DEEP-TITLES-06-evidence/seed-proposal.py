"""Store one What-if proposal in a LOCAL throwaway demo database, so the title gate has a
proposal detail page to open. Reads ADG_API_URL (default http://127.0.0.1:8030); never point it
at the hosted demo. The proposal removes one NTFS ACE in memory; nothing is applied anywhere."""

import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("ADG_API_URL", "http://127.0.0.1:8030")
ZEPHYR = r"\\GP-ENG-FS01\Engineering\Projects\Zephyr"


def call(method, path, body=None, token=None):
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data, headers, method=method)) as r:
        return json.load(r)


token = call("POST", "/auth/dev/login", {"username": "admin"})["access_token"]
if call("GET", "/api/v1/simulations?limit=1", token=token)["simulations"]:
    raise SystemExit("a proposal already exists; nothing stored")
acl = call("GET", f"/api/v1/resources/{urllib.parse.quote(ZEPHYR, safe='')}/acl", token=token)
entries = [e for e in acl["entries"] if "ace_key" in e]
change = {"kind": "remove_ntfs_ace", "resource_key": ZEPHYR.casefold(), "ace_key": entries[0]["ace_key"]}
stored = call("POST", "/api/v1/simulations", {"name": "Title gate proposal", "changes": [change]}, token=token)
print("stored proposal", stored["simulation"]["simulation_id"])
