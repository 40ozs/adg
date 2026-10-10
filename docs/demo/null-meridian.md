# Null Meridian — the Demo Mode estate

**Status:** implemented · **Seed:** `null-meridian-v1` · **Snapshot:** 2026-10-01 ·
**Code:** `backend/app/demo/null_meridian/` · **Machine-readable:**
[`null-meridian.manifest.json`](null-meridian.manifest.json) (summary, digests, scenarios),
[`null-meridian.canon.json`](null-meridian.canon.json) (every employee, group and membership)

> **Synthetic.** Null Meridian is a fictional company. Every name, mailbox, account name, SID,
> GUID, host name, share and path in this document and in the files beside it was generated
> from the seed. None is copied from, or describes, a real person, organization or system.

Null Meridian is a ten-year-old, 1,800-person company whose directory and file servers grew
the way real ones do: four generations of group-naming conventions, an Okta rollout that added
groups beside the AD ones instead of replacing them, a 2023 standard that new work follows and
old work mostly does not, and enough organic access debt to make governance worth doing. ADG's
Demo Mode serves it through the real application — the real ingestion endpoints, history
model, current-state rules, access engine, risk engine, search, simulation and UI — with no
directory, file server, collector or credential anywhere near it.

The same canon is the company OGWA's demo describes. Where both applications name an
employee, department, security group, group creation date or mailbox, the identifiers are the
ones in `null-meridian.canon.json`.

---

## 1. Running it

Demo Mode is a deployment property, chosen when the API process starts:

```
ADG_RUNTIME_MODE=demo
ADG_DATABASE_URL=postgresql+psycopg://adg:…@localhost:5432/adg_demo   # name must contain "demo"
```

and the one documented operation that puts the database into the canonical state:

```powershell
.\scripts\demo-reset.ps1                                   # Windows wrapper
python -m app.demo.null_meridian reset --report reset.json  # from backend/, any OS
docker compose exec api python -m app.demo.null_meridian reset   # in the compose stack
```

The demo database must exist (for the compose stack, start it with `POSTGRES_DB=adg_demo`,
`ADG_RUNTIME_MODE=demo`). The reset:

1. **refuses** unless `ADG_RUNTIME_MODE=demo`, `ADG_ENVIRONMENT` is not `production`, the
   database name contains `demo`, **and** the database either carries the demo marker or holds
   no scan runs — a database with data that was never marked is refused even when its name
   says `demo`;
2. runs `alembic upgrade heads` — the production schema, unchanged;
3. marks the database `COMMENT ON DATABASE … IS 'adg-demo:null-meridian-v1'` (a comment, not a
   table: the demo adds nothing to the schema);
4. empties the ADG tables **of that database only** — everything in a marked demo database is
   demo-owned;
5. posts the three scan generations through `POST /api/v1/scan-runs`, `…/batches` and
   `…/completion`, in-process, with the same validation, idempotency and reconciliation a
   Windows collector gets;
6. evaluates the risk rules at the fixed instant 2026-10-01T06:00Z;
7. prints a report with counts and a **fingerprint** of the logical database.

It is safe to run repeatedly. Two consecutive resets produce the same fingerprint
(§9); the test suite runs it twice and compares. Exit codes: `0` done, `1` failed,
`3` refused.

Other commands, all pure (no database):

```
python -m app.demo.null_meridian stats                       # counts and distributions
python -m app.demo.null_meridian manifest --out M.json --canon-out C.json
python -m app.demo.null_meridian transcripts --out DIR        # all 300 collector transcripts
```

### What Demo Mode refuses

`ADG_RUNTIME_MODE=demo` is validated at startup, with the other settings, and the process does
not start if any of these hold:

| Setting | Why |
| --- | --- |
| `ADG_ENVIRONMENT=production` | a production deployment must never be one variable away from a reset |
| a database name without `demo` | the reset empties the database it is pointed at |
| `ADG_COLLECTOR_API_KEYS` set | a demo receives no collector |
| `ADG_REMEDIATION_EXECUTION_MODE` ≠ `disabled` | no executor, not even the lab one |
| `ADG_ALERT_POLICY_PATH` set | the shipped policy delivers only to the log |

There is no API route, parameter or schema that names the mode (asserted against the published
OpenAPI document), so a running deployment cannot be switched by a request. Every response
from a demo deployment carries `X-ADG-Runtime-Mode: demo`. In Demo Mode there is **no** Active
Directory or LDAP connection, no SMB connection, no Windows credential, no PowerShell
collector, no production PostgreSQL and no file-system enumeration: the backend never had any
of the first five (collectors push to it), the reset's only subprocess is the migration, and
the DB test runs a full reset with Python's socket connect refused.

When `ADG_RISK_CONFIGURATION_PATH` is unset, Demo Mode uses Null Meridian's own risk
configuration (`app/demo/null_meridian/risk.py`): eleven folders declared sensitive (Finance,
Payroll, People, Legal, Executive, Board, security incidents, sales commissions…), and one
threshold moved — `redundant_access_paths.minimum_paths` from 2 to 4. Every migrated share
carries its legacy group *and* its `sg-share-*` replacement on purpose until migration ends,
so two routes is the expected state of most of the estate; at the shipped threshold the rule
reports ~175,000 informational findings and buries everything else.

---

## 2. The company

| Fact | Value |
| --- | --- |
| Company | Null Meridian |
| DNS / UPN domain | `nullmeridian.info` |
| NetBIOS | `NULLMERIDIAN` |
| Domain SID | `S-1-5-21-3949361974-1583827165-633756269` (from the seed) |
| Founded | 2016-10-01 |
| Snapshot | 2026-10-01 (ten years) |
| Okta adopted | 2021-10-01 |
| `sg-*` naming standard | 2023-10-01 |
| Active employees | **exactly 1,800** |
| Former employees | 640 (85 disabled accounts on the snapshot, 555 deleted) |
| Leaver policy | disabled at termination, deleted 180 days later |
| Tech debt | moderate |

### Departments (active employees on the snapshot)

| Department | People | Founded | Department group |
| --- | ---: | --- | --- |
| Engineering | 520 | 2016-10-01 | `Engineering` |
| Sales | 300 | 2016-10-01 | `GG_Sales` |
| Customer Success | 190 | 2017-03-01 | `CS-All` |
| Data | 110 | 2019-11-01 | `Data-Team` |
| Marketing | 110 | 2016-10-01 | `Marketing-All` |
| Operations | 105 | 2016-10-01 | `Ops Team` |
| Product | 95 | 2016-10-01 | `Product Team` |
| Finance | 90 | 2016-10-01 | `Finance-Team` |
| Information Technology | 85 | 2016-10-01 | `IT-Staff` |
| People / HR | 60 | 2017-01-09 | `HR Staff` |
| Design | 55 | 2018-04-02 | `Design_Team` |
| Security | 40 | 2020-01-15 | `Security-Team` |
| Legal | 28 | 2018-02-01 | `Legal_Dept` |
| Executive | 12 | 2016-10-01 | `Executives` |

Levels: 10 executives (CEO Nina Coleman and nine C-level officers), 13 department heads,
16 directors, 200 managers, 1,561 individual contributors. Every active employee but the CEO
has an active manager. 57 active employees transferred between departments; 15 people
arrived together with the acquisition of Lumen Analytics on 2021-04-01. Hires span the whole
decade — 90 people have more than eight years' tenure, 118 joined in the last 90 days.

### Identifiers

All derived, none stored; the manifest's `algorithms` block states each precisely.

- **Mail = UPN:** `given.family@nullmeridian.info`; the *n*-th holder of a local part (in
  employee-number order) gets suffix *n* — `maria.lopez2@…`.
- **sAMAccountName:** first initial + family name, ≤ 18 characters, numbered the same way.
- **Employee id:** `NM-00001` … in hire order.
- **RIDs** follow creation order across accounts and groups, the way a DC allocates them, from
  1103 (Domain Admins 512, Domain Users 513).
- **UUIDs:** `uuid5(uuid5(NAMESPACE_DNS, "nullmeridian.info"), "<kind>:<key>")`.
- **Draws:** `sha256("null-meridian-v1|<label>|<n>")[:8]` — no `random`, no clock.

---

## 3. Groups and their history

205 groups exist on the snapshot (one more, `Project-Vega`, was deleted on 2026-09-10).

| Era | From | Groups | Conventions |
| --- | --- | ---: | --- |
| Legacy | 2016-10-01 | 54 | `Finance-RW`, `Finance_Read`, `GG_Finance_RW`, `FS-Finance-R`, `Share_Engineering_Modify`, `Engineering-FS`, `HR Staff` — one convention per administrator |
| Team & application | 2019-10-01 | 61 | `ENG-Payments`, `Team_Analytics`, `APP-GitHub-Users`, `GG_App_Workday`, `Salesforce-Sales` |
| Transitional (Okta) | 2021-10-01 | 25 | `Okta_GitHub_Admin`, `Okta-Salesforce-Users`, `OKTA_Slack_All`, `okta-zoom-users` beside AD-style `Share-Data-RW`, `ENG-Payments-Oncall` |
| Standard | 2023-10-01 | 65 | `sg-share-<department>-read-only\|write`, `sg-okta-<application>-<tier>` |

62 of the 65 standard-era groups follow the standard; the three that do not are listed and
asserted (`Finance-Close-FY2024`, `IT-Okta-Break-Glass`, `PRJ-Helios`). No group created
before 2023-10-01 uses `sg-`; no group mentions Okta before 2021-10-01. The standard's share
groups rolled out department by department from 2023-10-16 (IT) to 2025-01-13 (Executive);
Legal's arrive on **2026-09-10**, between the first and second scans.

Every legacy application group survived beside its replacement, usually nested into it:
`APP-GitHub-Users → Okta_GitHub_Users → sg-okta-github-developer`. 54 groups have no owner and
42 are owned by somebody who has left; every standard-era group has an active owner.

### The hierarchy

4,273 direct membership edges on the snapshot (4,081 person→group, 192 group→group) plus
1,885 primary-group edges to Domain Users. Longest nesting chain under each group:

| Depth | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Groups | 86 | 34 | 40 | 14 | 8 | 9 | 11 | 3 |

Valid, acyclic, and cumbersome on purpose: team groups under a redundant
`Engineering-Backend` layer; `ENG-Platform` in `Engineering` twice; `FS-Legacy-Users` reached
from everybody by two routes; `GG_Finance_RW` wrapping the domain-local `Finance-RW`;
`APP-GitHub-Users` nested into the engineering *file-share* group; `Hiring-Managers` and
`sg-share-projects-read-only` filled with the whole `Managers` group; `Eng-Leads` gaining
every manager on 2026-09-22. The depth-7 groups are `sg-okta-*-user` groups that reach
everyone through `AD-Okta-Sync-All → All Staff`; no ACL names them. The deepest chain that
grants file access is six groups (Scenario G).

---

## 4. The file estate

| | Count |
| --- | ---: |
| File servers | 33 (`NM-FS-01` … `NM-FS-05`, `NM-FIN-FS01`, `NM-ENG-FS01`, `NM-CORP-FS02`, …) |
| Shares | 91, plus 33 administrative `C$` |
| Share ACEs | 606 |
| Folders (NTFS resources) | 824 (depth 0–3) |
| NTFS ACEs (explicit + inherited) | 4,111 |
| ACL objects with explicit entries | 376 |
| Protected (inheritance broken) | 86 |
| Explicit direct-user ACEs | 136 |
| Explicit group ACEs | 518 |
| Deny ACEs | 2 (a litigation hold; a write-deny on an archive scan folder) |

Servers were commissioned from 2016 (Windows Server 2012 R2) to 2025 (2022); a share is as
old as its server or younger, and its age decides how its folders were permissioned. Shares
cover Finance, Payroll, Accounting, Audit, People, Recruiting, Legal, Contracts, Engineering,
Builds, Product, Research, Data, Sales, Commissions, Marketing, Customer Success, Operations,
IT, Security, Incidents, Executive, Board, Projects, Archive, Migrations, Acquisitions, Shared
and departmental collaboration.

Permission levels are the security dialog's: Read, Read & execute, List folder contents,
Read + Write, Modify, Full control (and a Delete deny). Standard share groups map
`read-only → Read & execute` and `write → Modify`, with drift where history put it.

Share-level ACLs mirror the NTFS trustees a share needs (`FS-Admins` full, every other trustee
`change` or `read`), except three legacy shares that still have Everyone/Full and four with
Authenticated Users/Change — the NTFS ACL is the narrower layer there, which is the common real
shape. (Payroll and Archive also carry Authenticated Users at the share layer, mirrored from
the folder entries below them that needed it.)

### Distribution

Folder ACLs are authored where a scenario needs them and otherwise rolled from the seed per
folder path, weighted by the share's era. The result, over the 376 ACL objects with explicit
entries:

| Assignment | Share | Target |
| --- | ---: | --- |
| Group-based | **70.7%** | 65–75% |
| Direct-user | **18.4%** | 15–20% |
| Mixed / legacy exception | **10.9%** | 10–15% |

and over all 824 folders:

| Posture | Share | Target | What counts |
| --- | ---: | --- | --- |
| Ordinary | **77.4%** | 70–80% | inherits, or a group grant that fits |
| Questionable | **17.2%** | 15–20% | direct users, broken inheritance off sensitive shares, legacy + standard on one ACL, duplicate entries, Full control to a group, broad read on a sensitive share |
| Clearly risky | **5.3%** | 5–10% | a disabled or deleted account named, Full control to a person, a broad group with write |

Folders on servers commissioned before 2019-10 carry more than twice the debt of folders on
servers commissioned after 2023-10 (asserted).

### Direct-user assignments, and why each one is there

The 136 direct entries are spread across 32 of the 33 servers and 18 areas; no share holds
more than 6% of them and no folder more than four. Each carries an intent in the manifest (ADG has no
field for intent, which is the point of a review):

| Intent | Entries | Example |
| --- | ---: | --- |
| nested-group-confusion | 46 | somebody added because the right group was too hard to find |
| temporary-project | 27 | a procurement lead on `Legal\Vendor-Review`, removed 2026-09-14 |
| former-user | 16 | home drives on `NM-ARCH-FS01` naming deleted accounts |
| migration-leftover | 10 | IT migrators' Full control on `Migrations\FS-Consolidation-2020` |
| acquisition-migration | 9 | Lumen Analytics staff on `Acquisitions\Lumen-Analytics` |
| finance-close-exception | 9 | named accountants on `Finance\Accounting\Close\FY2025` |
| historical-engineer | 8 | long-tenured engineers on `Engineering\Tools\Build` |
| executive-assistant | 6 | Hiro Taylor and Eric Lim on `Executive` and `Board` |
| incident-access | 5 | responders on `Incidents\IR-2026-014`, created 2026-09-24 |

### Intentional risks

Known and kept, so the risk report has something true to say:

- `\\NM-FIN-FS01\Payroll\Vendor-Migration-2023`: **Authenticated Users / Modify** on a sensitive
  folder, left from a 2023 payroll-vendor file drop, plus a direct entry for a leaver.
- `\\NM-FS-01\Shared\Team-Drops` and `\\NM-FS-02\Team-Space`: All Staff / Modify.
- `\\NM-FIN-FS01\Finance\Commissions` and `\\NM-SALES-FS01\Commissions`: every seller can read
  every commission statement (`GG_Sales`).
- 85 disabled accounts; about one in ten leavers kept a team or department group.
- `Project-Vega` (deleted 2026-09-10) still named on `\\NM-PROD-FS01\Research\Vega` and on the
  archive — now an unresolved SID.
- Three legacy shares with Everyone/Full at the share layer; four with Authenticated Users.

`Everyone` is **not** ubiquitous: one folder ACL (`\\NM-FS-01\Shared\Public`, read) names it.

---

## 5. Three scans

ADG "went live" at Null Meridian at the start of September 2026. The reset replays what it
would have collected: per generation, one AD run (reconciling the domain), and per server a
local-groups run, an SMB run and an NTFS run — **300 runs, 41,756 observations**.

| Generation | Observed | Runs | What it shows |
| --- | --- | --- | --- |
| `g1` | 2026-09-03 | 99 succeeded, 1 failed | **Initial observation.** `NM-SEC-FS01`'s NTFS scan fails (`access_denied`: the collector account was not yet granted access). Its shares are known; its folders are not. |
| `g2` | 2026-09-17 | 100 succeeded | **Change.** Joiners and leavers; accounts crossing the 180-day line and the deleted group `Project-Vega` are **tombstoned** with their edges; `\\NM-ENG-FS02\QA\Sprint-Scratch` (deleted 09-12) is **tombstoned** by the reconciled NTFS scan; Legal's `sg-share-legal-*` groups and their ACEs appear; a direct entry is cleaned up; `All Staff` on `Marketing\Campaigns` is replaced by `MKT-Demand-Gen`; `NM-SEC-FS01` is read for the first time. |
| `g3` | 2026-10-01 | 98 succeeded, 1 partial, 1 failed | **Snapshot.** `NM-FS-03`'s NTFS scan **fails** (`host_unreachable`) and `NM-MIG-FS01`'s is **partial** (`access_denied` on `FS-Consolidation-2020\Restricted`). Neither may remove anything. |

What the current-state rules make of that, asserted against the database after every reset:

- everything `g2` saw on `NM-FS-03` is still current;
- `\\NM-MIG-FS01\Migrations\Wave-3-Staging` was really deleted on 2026-09-25 and is **still
  current**: the only scan since was partial, and *unobserved is not absent* — ADG keeps it
  until a complete scan says otherwise;
- the unreadable `Restricted` folder keeps its `g2` state;
- `Sprint-Scratch`, `Project-Vega` and the crossing accounts are retained in history with an
  open tombstone and are absent from every current-state view;
- the current folder set is *exactly* `g3`'s, plus `NM-FS-03` as of `g2`, plus `NM-MIG-FS01` as
  of `g2`.

---

## 6. Golden scenarios

Present after every reset; asserted in `tests/demo/test_null_meridian_estate.py` (shape) and
`tests/db/test_null_meridian_demo.py` (through the API).

**A — Modern healthy share.** `\\NM-CORP-FS02\Design` (2023-11): `sg-share-design-read-only`
(Read & execute) and `sg-share-design-write` (Modify), people as direct members, no nesting,
inheritance unbroken in all eight folders, no person on any ACL.

**B — Legacy finance hierarchy.** `\\NM-FIN-FS01\Finance` (2017): `Finance-RW`, `GG_Finance_RW`,
`Finance_Read`, `FS-Finance-R`, `sg-share-finance-write`, `sg-share-finance-read-only` on one
root. `FIN-Accounting → Finance-Team → Finance-RW → GG_Finance_RW` grants Modify;
`FIN-Close-Team` grants it again; about 40% of Finance also hold the modern group directly.
Close folders name accountants; the controller, Lucia Hart, holds Full control on `Budgets`.

**C — Engineering sprawl.** `\\NM-ENG-FS01\Engineering`: 30+ folders, five root grants,
team folders, the Apollo/Nebula/Orion/Zephyr project folders, historical direct entries, and
`APP-GitHub-Users` granting file access through `Engineering-FS`.

**D — Sensitive People share.** `\\NM-HR-FS01\People`: migrated to the standard; Employee-
Relations and Compensation protected and limited to `HR-Confidential`. Exactly two
exceptions: Recruiting readable by `Hiring-Managers` (every manager), and a Finance payroll
specialist's direct entry on the payroll-vendor transition folder.

**E — Migration and archive debt.** `\\NM-ARCH-FS01\Archive` and
`\\NM-MIG-FS01\Migrations\FS-Consolidation-2020`: broken inheritance throughout, `Finance-RW`
granted twice on one ACL, `Project-Vega` and deleted accounts on ACLs, IT migrators' leftover
Full control, `FS-Legacy-Users` with Modify.

**F — Cross-department access, two ways.** Brianna Reddy
(`brianna.reddy@nullmeridian.info`) moved from Finance to Sales Operations on 2022-02-01. On
`\\NM-FIN-FS01\Finance\Reports\Revenue` she holds Read & execute through
`Sales-Ops → Revenue-Reporting` and Modify through a direct entry from her Finance days.
`/api/v1/access/paths/principals/{sid}/resources/{path}` lists both.

**G — A six-level chain.** Camila Nair (`camila.nair@nullmeridian.info`, payments on-call)
reaches `\\NM-ENG-FS01\Engineering\Runbooks\Payments-Legacy`, whose only grant is
`Share_Engineering_Modify`, through `ENG-Payments-Oncall → ENG-Payments → Engineering-Backend →
Engineering → Engineering-FS → Share_Engineering_Modify` — and through seven groups via
`APP-GitHub-Users`.

**H — A safe first clean-up.** `Proj-Zephyr-RW` has had no members since the project ended in
2019 and still holds Modify on both Zephyr project folders and the 2019 archive. The empty-group rule reports it; removing
the entry changes nobody's access, which a simulation confirms.

---

## 7. Risk findings after a reset

With the demo risk configuration, evaluated at 2026-10-01T06:00Z, **whole estate, no ceiling
reached**: 9,666 matched, **9,386 open findings** —

| Rule | Severity | Findings |
| --- | --- | ---: |
| redundant_access_paths (4+ routes) | informational | 6,795 |
| disabled_principal_retains_access | medium | 1,921 |
| direct_user_ace | low | 308 |
| broken_inheritance | low / informational | 87 / 200 |
| unresolved_sid_on_acl | medium | 30 |
| deep_group_nesting | low | 20 |
| authenticated_users_broad_access | high / medium | 6 / 6 |
| domain_users_broad_access | low | 4 |
| empty_permission_bearing_group | low | 4 |
| everyone_broad_access | critical / high | 3 / 1 |
| broad_access_on_sensitive_resource | high | 1 (`Payroll\Vendor-Migration-2023`) |

Counts are per (principal, resource) for the access-shaped rules, so the handful of disabled
accounts that kept a team group produce most of the 1,921. The reset report
([`evidence/reset-1.json`](evidence/reset-1.json)) carries the totals for the current canon.

---

## 8. OGWA alignment

`null-meridian.canon.json` is the shared contract: one record per line for every employee
(id, UUID, names, mail, account name, SID, objectGUID, department, team, title, level,
manager, hire/termination/transfer dates), every group (name, UUID, SID, objectGUID, creation
date, era, category, application, tier, scope, owner, deletion date, DN), every nesting and
every direct membership on the snapshot. Its `digests` block — SHA-256 over the canonical
employee, group and membership rows — is pinned by the ADG test suite. An OGWA
implementation can either read the file or reimplement the algorithms in the manifest and
compare digests. A change to either is a new canon (`null-meridian-v2`), never an edit to v1.

ADG's collector contract has no `whenCreated`, department, title, manager or owner field, so
those facts live in the canon and not in ADG's database; ADG carries the department as the
user's OU (`OU=Finance,OU=People,DC=nullmeridian,DC=info`) and the naming era as the group's
OU (`OU=Legacy`, `OU=Teams and Applications`, `OU=Okta Transition`, `OU=Standard`).

---

## 9. Reproducibility and performance

Measured on the development container (PostgreSQL 16, `fsync=off`, one CPU-bound Python
process); the two reports are committed as [`evidence/reset-1.json`](evidence/reset-1.json)
and [`evidence/reset-2.json`](evidence/reset-2.json):

| Step | Reset 1 | Reset 2 |
| --- | ---: | ---: |
| Build canon + estate + 300 transcripts (pure) | ~8 s | ~8 s |
| Post 300 runs / 41,756 observations through the API | 96 s | 104 s |
| Migrate, empty, evaluate risks (9,666 matched), fingerprint | 44 s | 47 s |
| **Whole reset** | **140 s** | **151 s** |
| Fingerprint | `db736602…85c28a0` | `db736602…85c28a0` |

The fingerprint is SHA-256 over every row of `scan_runs`, `principals`, `membership_edges`,
`servers`, `smb_shares`, `smb_share_aces`, `ntfs_resources`, `ntfs_aces`, `object_versions`
and `risk_findings`, excluding only the database-clock bookkeeping columns (`created_at`,
`updated_at`), the serial `id`, and the random evaluation ids. Validity windows, run ids,
hashes, evidence and finding detail are all included, and all are identical across resets.
