"""Compare ADG's legacy generated canon (origin/main) with the projection-backed canon.

Run from anywhere with the two backend directories as arguments. Prints a JSON summary.
"""
import datetime as dt, importlib, json, sys

legacy_root, new_root = sys.argv[1], sys.argv[2]

def load(root, package):
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    sys.path.insert(0, root)
    try:
        return importlib.import_module(f"app.demo.{package}.canon").build_canon()
    finally:
        sys.path.remove(root)

old = load(legacy_root, "null_meridian")
new = load(new_root, "genesee_press")
new_mod = sys.modules["app.demo.genesee_press.canon"]

emp = {e.employee_id: e for e in new.employees}
rename_emp = {e.employee_id: "GP-" + e.employee_id[3:] for e in old.employees}
grp_by_sid = {g.sid: g.name for g in new.groups}
rename_grp = {g.name: grp_by_sid[f"{g.sid}"] for g in old.groups}
out = {"employees": [len(old.employees), len(new.employees)], "groups": [len(old.groups), len(new.groups)]}

problems = []
for o in old.employees:
    n = emp[rename_emp[o.employee_id]]
    for f in ("given_name","family_name","department","team","title","hire_date","termination_date",
              "previous_department","transfer_date","sam_account_name","sid","object_guid"):
        if getattr(o, f) != getattr(n, f):
            problems.append((o.employee_id, f, str(getattr(o, f)), str(getattr(n, f))))
    if (rename_emp.get(o.manager_id) if o.manager_id else None) != n.manager_id:
        problems.append((o.employee_id, "manager"))
    if o.level.value != n.level.value: problems.append((o.employee_id, "level"))
    if o.email.split("@")[0] != n.email.split("@")[0]: problems.append((o.employee_id, "mail"))
    otags = sorted(t.replace("lumen-analytics", new_mod.ACQUISITION_TAG.split(":")[1]) for t in o.tags)
    if otags != list(n.tags): problems.append((o.employee_id, "tags", otags, n.tags))
gnew = new.groups_by_name
renamed_groups = {}
for o in old.groups:
    n = gnew[rename_grp[o.name]]
    if o.name != n.name: renamed_groups[o.name] = n.name
    for f in ("created","deleted","scope","group_type","tier","department","object_guid"):
        if getattr(o, f) != getattr(n, f): problems.append((o.name, f, str(getattr(o, f)), str(getattr(n, f))))
    if o.category.value != n.category.value or o.era.value != n.era.value: problems.append((o.name, "category/era"))
    if (rename_emp.get(o.owner_id) if o.owner_id else None) != n.owner_id: problems.append((o.name, "owner"))
    if o.application != n.application: problems.append((o.name, "application", o.application, n.application))
    if o.description != n.description: problems.append((o.name, "description", o.description, n.description))
    if o.distinguished_name.split(",OU=")[0] != n.distinguished_name.split(",OU=")[0].replace(n.name, o.name): problems.append((o.name, "dn"))
out["group_renames"] = len(renamed_groups)
out["field_differences"] = problems

def norm(edges):
    return {(rename_grp[g], rename_grp.get(m) or rename_emp[m]) for g, m in edges}

days = list(new_mod.iter_dates(dt.date(2016, 10, 1), dt.date(2026, 10, 1)))
mismatched = []
for day in days:
    if norm(old.memberships_on(day)) != set(new.memberships_on(day)):
        mismatched.append(day.isoformat())
out["days_compared"] = len(days)
out["membership_days_differing"] = mismatched[:20]
out["membership_edges_at_snapshot"] = len(new.memberships_on(new_mod.SNAPSHOT))
out["nesting_depths_equal"] = {rename_grp[k]: v for k, v in old.nesting_depths().items()} == new.nesting_depths()
print(json.dumps(out, indent=1, default=str))
