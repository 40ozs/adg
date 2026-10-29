"""Rename-normalized comparison of ADG's legacy and migrated collector transcripts.

    python transcript_parity.py LEGACY_DIR NEW_DIR PROJECTION_GROUPS_JSONL

Legacy values are mapped to canonical ones by the documented rules (SYNTH-RECONCILE-01A
supersession; projection legacy_name), and every UUID is replaced by its order of first
appearance, so run/batch ids derived from a renamed namespace compare by structure.
"""
import json, pathlib, re, sys

legacy_dir, new_dir, groups_path = map(pathlib.Path, sys.argv[1:4])
renames = {}
for line in groups_path.read_text().splitlines():
    g = json.loads(line)
    if g["legacy_name"] and g["legacy_name"] != g["name"]:
        renames[g["legacy_name"]] = g["name"]
TEXT = [("sg-share-", "sec-share-"), ("sg-okta-", "sec-okta-"), ("DC=nullmeridian,DC=info", "DC=corp,DC=geneseepress,DC=example"), ("NULLMERIDIAN\\", "GPCORP\\"),
        ("Null Meridian", "Genesee Press"), ("null-meridian@", "genesee-press@"),
        ("Lumen Analytics", "Brindlewood Analytics"), ("Lumen-Models", "Brindlewood-Models"),
        ("Lumen-Analytics", "Brindlewood-Analytics"), ("lumen-models", "brindlewood-models"),
        ("lumen-analytics", "brindlewood-analytics")]
NAMES = sorted(renames.items(), key=lambda kv: -len(kv[0]))
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

def legacy_string(key, value):
    if key == "user_principal_name":
        value = value.replace("@nullmeridian.info", "@geneseepress.example")
    value = value.replace("nullmeridian.info", "corp.geneseepress.example")
    for old, new in TEXT:
        value = value.replace(old, new)
    for old, new in NAMES:
        value = value.replace(old, new).replace(old.casefold(), new.casefold())
    value = re.sub(r"\bNM-", "GP-", value)
    return re.sub(r"\bnm-", "gp-", value)

def walk(node, fn, key=None):
    if isinstance(node, dict):
        return {k: walk(v, fn, k) for k, v in node.items()}
    if isinstance(node, list):
        return [walk(v, fn, key) for v in node]
    return fn(key, node) if isinstance(node, str) else node

def uuids(docs):
    seen = {}
    def fn(key, value):
        return UUID.sub(lambda m: f"uuid#{seen.setdefault(m.group(0), len(seen))}", value)
    return [walk(d, fn) for d in docs]

legacy_files = sorted(legacy_dir.glob("*.json"))
new_files = sorted(new_dir.glob("*.json"))
names_equal = [legacy_string(None, p.name) for p in legacy_files] == [p.name for p in new_files]
legacy_docs = uuids([walk(json.loads(p.read_text()), legacy_string) for p in legacy_files])
new_docs = uuids([json.loads(p.read_text()) for p in new_files])
differing = [p.name for p, a, b in zip(new_files, legacy_docs, new_docs) if a != b]
observations = sum(len(b["observations"]) for d in new_docs for b in d["batches"])
raw_equal = sum(1 for a, b in zip(legacy_files, new_files) if a.read_bytes() == b.read_bytes())
print(json.dumps({"transcripts": [len(legacy_files), len(new_files)], "file_names_equal_after_rename": names_equal,
                  "observations": observations, "group_renames_applied": len(renames),
                  "byte_identical_before_normalization": raw_equal,
                  "differing_after_normalization": differing}, indent=1))

