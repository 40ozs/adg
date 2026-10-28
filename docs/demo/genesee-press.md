# Genesee Press — the Demo Mode estate

**Status:** implemented · **Dataset:** `genesee-press-v1` · **Snapshot:** 2026-10-01 ·
**Code:** `backend/app/demo/genesee_press/` · **Source:** the `adg` projection of
[`40ozs/synthetic-enterprise`](https://github.com/40ozs/synthetic-enterprise), vendored in
[`backend/vendor/synthetic-enterprise/`](../../backend/vendor/synthetic-enterprise/) and pinned
by its `SYNTHETIC_ENTERPRISE.lock` · **Machine-readable:**
[`genesee-press.manifest.json`](genesee-press.manifest.json) (summary, provenance, digests,
scenarios) · **Migration:** [SYNTH-ADG-01](reports/SYNTH-ADG-01-report.md) (formerly
*Null Meridian*, `null-meridian-v1`)

> **Synthetic.** Genesee Press is a fictional company. Every name, mailbox, account name, SID,
> GUID, host name, share and path in this document and in the files beside it comes from the
> shared synthetic world or from ADG's own seeded file estate. None is copied from, or
> describes, a real person, organization or system. Domains are reserved `.example` names.

Genesee Press is a ten-year-old, 1,800-person company whose directory and file servers grew
the way real ones do: four generations of group-naming conventions, an Okta rollout that added
groups beside the AD ones instead of replacing them, a 2023 standard that new work follows and
old work mostly does not, and enough organic access debt to make governance worth doing. ADG's
Demo Mode serves it through the real application — the real ingestion endpoints, history
model, current-state rules, access engine, risk engine, search, simulation and UI — with no
directory, file server, collector or credential anywhere near it.

Genesee Press is the one organization of the shared synthetic world: OGWA's demo describes the
same core company, and AO's and CSW's describe its Distribution Division and Digital Estate.
ADG reads its directory from the vendored projection (§8) and builds only its file estate
itself.

---

## 1. Running it

Genesee Press is the canonical dataset of ADG's **Demo Mode**
([docs/architecture/demo-mode.md](../architecture/demo-mode.md)) and its default. Demo Mode
is the runtime, safety, persistence and operation boundary; this package is the data. It
generates the estate and nothing else — the reset, the database guard, the synthetic scan
and the status commands are Demo Mode's, and they load Genesee Press through
`app.demo.genesee_press.synthetic_dataset()`.

A demo is a deployment property, chosen when the process starts:

```
ADG_RUNTIME_MODE=demo                                               # production | demo
ADG_DATABASE_URL=postgresql+psycopg://adg:…@localhost:5432/adg_demo  # 'demo' as a word
# ADG_DEMO_DATASET unset (or genesee-press-v1): Genesee Press
```

and the one operation that puts the database into the canonical state:

```powershell
.\scripts\demo-reset.ps1                                  # Windows wrapper
python -m app.demo_mode reset --report reset.json         # from backend/, any OS
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build   # whole stack
```

The demo database must exist (the compose override creates `adg_demo` on its own volume). The
reset:

1. **refuses** unless `ADG_RUNTIME_MODE=demo`, the database is named with `demo` as a word,
   **and** the database either carries a `deployment_marker` row reading `demo` or holds no
   row in any ADG table — a database with data that was never marked is refused even when its
   name says `demo`, and one marked for production is refused;
2. runs `alembic upgrade heads` — the production schema, unchanged;
3. writes the marker (`dataset_key` and `dataset_seed` both `genesee-press-v1`), then, in the
   transaction that locks and re-checks it, empties the ADG tables **of that database only**;
4. posts the three scan generations, one at a time, through `POST /api/v1/scan-runs`,
   `…/batches` and `…/completion`, in-process, with the same validation, idempotency and
   reconciliation a Windows collector gets;
5. evaluates the risk rules under Genesee Press's configuration, dated 2026-10-01T06:00Z (the
   pass after the snapshot scan);
6. checks 23 canonical objects (scenarios A–H and the partial/failed scan promises) in
   current state;
7. prints a report with counts and a **fingerprint** of the logical database.

It is safe to run repeatedly. Two consecutive resets produce the same fingerprint (§9); the
test suite runs it twice and compares. `python -m app.demo_mode verify` re-checks a running
demo without changing it. To watch the history happen instead, rewind and scan forward:

```
python -m app.demo_mode reset --generations 1   # the estate as ADG saw it on 2026-09-03
python -m app.demo_mode scan                    # g2, 2026-09-17, and its risk pass
python -m app.demo_mode scan                    # g3, 2026-10-01 — the canonical state again
```

Developer utilities (no configuration, no database; all but the re-vendor are pure):

```
python -m app.demo.genesee_press stats                       # counts and distributions
python -m app.demo.genesee_press manifest --out M.json        # the committed manifest
python ../scripts/vendor-synthetic-enterprise.py --source PATH  # re-vendor (a deliberate change)
python -m app.demo.genesee_press transcripts --out DIR        # all 300 collector transcripts
```

### What a demo refuses

`ADG_RUNTIME_MODE=demo` is validated at startup, with the other settings, and the process does
not start if any of these hold (the full table is in the Demo Mode architecture, §8):

| Setting | Why |
| --- | --- |
| a database name without `demo` as a word | the reset empties the database it is pointed at |
| `ADG_COLLECTOR_API_KEYS` set | a demo receives no collector |
| `ADG_REMEDIATION_EXECUTION_MODE` ≠ `disabled` | no executor, not even the lab one |
| an alert policy enabling a non-`log` sink | a demo delivers nowhere but the log |
| `ADG_RUNTIME_MODE` anything but `production`/`demo` | including `standard`: never read as production |

No request can name or change the mode (asserted against the published OpenAPI document); the
mode is *reported*, read-only, by `GET /runtime`. Every response from a demo deployment carries
`X-ADG-Runtime-Mode: demo`, and the web shell shows a **Demo environment · Synthetic enterprise
data · Genesee Press** indicator before anybody signs in, linking to the demo guide (`/demo`),
where scenarios A–H link into the real pages. In Demo Mode there is **no** Active Directory or
LDAP connection, no SMB connection, no Windows credential, no PowerShell collector, no
production PostgreSQL and no file-system enumeration: the backend never had any of the first
five (collectors push to it), the reset's only subprocess is the migration, and the DB test
runs a full reset with Python's socket connect refused.

When `ADG_RISK_CONFIGURATION_PATH` is unset, Demo Mode uses Genesee Press's own risk
configuration (`app/demo/genesee_press/risk.py`): eleven folders declared sensitive (Finance,
Payroll, People, Legal, Executive, Board, security incidents, sales commissions…), and one
threshold moved — `redundant_access_paths.minimum_paths` from 2 to 4. Every migrated share
carries its legacy group *and* its `sec-share-*` replacement on purpose until migration ends,
so two routes is the expected state of most of the estate; at the shipped threshold the rule
reports ~175,000 informational findings and buries everything else.

---

## 2. The company

| Fact | Value |
| --- | --- |
| Company | Genesee Press |
| Mail / UPN domain | `geneseepress.example` |
| Directory (AD DNS) domain | `corp.geneseepress.example` (`DC=corp,DC=geneseepress,DC=example`) |
| NetBIOS | `GPCORP` |
| Domain SID | `S-1-5-21-3949361974-1583827165-633756269` (an opaque synthetic directory identifier, unchanged from ADG v1) |
| Founded | 2016-10-01 |
| Snapshot | 2026-10-01 (ten years) |
| Okta adopted | 2021-10-01 |
| `sec-*` naming standard | 2023-10-01 |
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
arrived together with the acquisition of Brindlewood Analytics on 2021-04-01 (formerly *Lumen
Analytics*). Hires span the whole
decade — 90 people have more than eight years' tenure, 118 joined in the last 90 days.

### Identifiers

Read from the vendored projection, never derived here; the manifest's `source` block names
the snapshot.

- **Mail = UPN:** `given.family@geneseepress.example`; the *n*-th holder of a local part (in
  employee-number order) has suffix *n* — `maria.lopez2@…`.
- **sAMAccountName:** first initial + family name, ≤ 18 characters, numbered the same way.
- **Employee id:** `GP-00001` … in hire order (ADG v1: `NM-00001`, kept as `legacy_employee_id`).
- **SIDs and objectGUIDs** are ADG v1's, unchanged: RIDs follow creation order across
  accounts and groups from 1103 (Domain Admins 512, Domain Users 513), so the frontend's
  pinned SIDs did not move.
- **Canonical ids** (`per_…`, `acc_…`, `grp_…`, `hst_…`) are kept beside each person, account,
  group and server.
- **Draws** for the file estate: `sha256("null-meridian-v1|<label>|<n>")[:8]`, no `random`, no
  clock. The seed and the server, group and share names inside labels keep their v1 spellings
  (`canon.legacy_key`), so the estate is exactly ADG v1's; they are never shown.

---

## 3. Groups and their history

205 groups exist on the snapshot (one more, `Project-Vega`, was deleted on 2026-09-10).

| Era | From | Groups | Conventions |
| --- | --- | ---: | --- |
| Legacy | 2016-10-01 | 54 | `Finance-RW`, `Finance_Read`, `GG_Finance_RW`, `FS-Finance-R`, `Share_Engineering_Modify`, `Engineering-FS`, `HR Staff` — one convention per administrator |
| Team & application | 2019-10-01 | 61 | `ENG-Payments`, `Team_Analytics`, `APP-GitHub-Users`, `GG_App_Workday`, `Salesforce-Sales` |
| Transitional (Okta) | 2021-10-01 | 25 | `Okta_GitHub_Admin`, `Okta-Salesforce-Users`, `OKTA_Slack_All`, `okta-zoom-users` beside AD-style `Share-Data-RW`, `ENG-Payments-Oncall` |
| Standard | 2023-10-01 | 65 | `sec-share-<department>-read-only\|write`, `sec-okta-<application>-<tier>` |

62 of the 65 standard-era groups follow the standard; the three that do not are listed and
asserted (`Finance-Close-FY2024`, `IT-Okta-Break-Glass`, `PRJ-Helios`). No group created
before 2023-10-01 uses `sec-`; no group mentions Okta before 2021-10-01. The standard's share
groups rolled out department by department from 2023-10-16 (IT) to 2025-01-13 (Executive);
Legal's arrive on **2026-09-10**, between the first and second scans.

Every legacy application group survived beside its replacement, usually nested into it:
`APP-GitHub-Users → Okta_GitHub_Users → sec-okta-github-developer`. 54 groups have no owner and
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
`sec-share-projects-read-only` filled with the whole `Managers` group; `Eng-Leads` gaining
every manager on 2026-09-22. The depth-7 groups are `sec-okta-*-user` groups that reach
everyone through `AD-Okta-Sync-All → All Staff`; no ACL names them. The deepest chain that
grants file access is six groups (Scenario G).

---

## 4. The file estate

| | Count |
| --- | ---: |
| File servers | 33 (`GP-FS-01` … `GP-FS-05`, `GP-FIN-FS01`, `GP-ENG-FS01`, `GP-CORP-FS02`, …) |
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
| former-user | 16 | home drives on `GP-ARCH-FS01` naming deleted accounts |
| migration-leftover | 10 | IT migrators' Full control on `Migrations\FS-Consolidation-2020` |
| acquisition-migration | 9 | Brindlewood Analytics staff on `Acquisitions\Brindlewood-Analytics` |
| finance-close-exception | 9 | named accountants on `Finance\Accounting\Close\FY2025` |
| historical-engineer | 8 | long-tenured engineers on `Engineering\Tools\Build` |
| executive-assistant | 6 | Hiro Taylor and Eric Lim on `Executive` and `Board` |
| incident-access | 5 | responders on `Incidents\IR-2026-014`, created 2026-09-24 |

### Intentional risks

Known and kept, so the risk report has something true to say:

- `\\GP-FIN-FS01\Payroll\Vendor-Migration-2023`: **Authenticated Users / Modify** on a sensitive
  folder, left from a 2023 payroll-vendor file drop, plus a direct entry for a leaver.
- `\\GP-FS-01\Shared\Team-Drops` and `\\GP-FS-02\Team-Space`: All Staff / Modify.
- `\\GP-FIN-FS01\Finance\Commissions` and `\\GP-SALES-FS01\Commissions`: every seller can read
  every commission statement (`GG_Sales`).
- 85 disabled accounts; about one in ten leavers kept a team or department group.
- `Project-Vega` (deleted 2026-09-10) still named on `\\GP-PROD-FS01\Research\Vega` and on the
  archive — now an unresolved SID.
- Three legacy shares with Everyone/Full at the share layer; four with Authenticated Users.

`Everyone` is **not** ubiquitous: one folder ACL (`\\GP-FS-01\Shared\Public`, read) names it.

---

## 5. Three scans

ADG "went live" at Genesee Press at the start of September 2026. The reset replays what it
would have collected: per generation, one AD run (reconciling the domain), and per server a
local-groups run, an SMB run and an NTFS run — **300 runs, 41,756 observations**.

| Generation | Observed | Runs | What it shows |
| --- | --- | --- | --- |
| `g1` | 2026-09-03 | 99 succeeded, 1 failed | **Initial observation.** `GP-SEC-FS01`'s NTFS scan fails (`access_denied`: the collector account was not yet granted access). Its shares are known; its folders are not. |
| `g2` | 2026-09-17 | 100 succeeded | **Change.** Joiners and leavers; accounts crossing the 180-day line and the deleted group `Project-Vega` are **tombstoned** with their edges; `\\GP-ENG-FS02\QA\Sprint-Scratch` (deleted 09-12) is **tombstoned** by the reconciled NTFS scan; Legal's `sec-share-legal-*` groups and their ACEs appear; a direct entry is cleaned up; `All Staff` on `Marketing\Campaigns` is replaced by `MKT-Demand-Gen`; `GP-SEC-FS01` is read for the first time. |
| `g3` | 2026-10-01 | 98 succeeded, 1 partial, 1 failed | **Snapshot.** `GP-FS-03`'s NTFS scan **fails** (`host_unreachable`) and `GP-MIG-FS01`'s is **partial** (`access_denied` on `FS-Consolidation-2020\Restricted`). Neither may remove anything. |

What the current-state rules make of that, asserted against the database after every reset:

- everything `g2` saw on `GP-FS-03` is still current;
- `\\GP-MIG-FS01\Migrations\Wave-3-Staging` was really deleted on 2026-09-25 and is **still
  current**: the only scan since was partial, and *unobserved is not absent* — ADG keeps it
  until a complete scan says otherwise;
- the unreadable `Restricted` folder keeps its `g2` state;
- `Sprint-Scratch`, `Project-Vega` and the crossing accounts are retained in history with an
  open tombstone and are absent from every current-state view;
- the current folder set is *exactly* `g3`'s, plus `GP-FS-03` as of `g2`, plus `GP-MIG-FS01` as
  of `g2`.

---

## 6. Golden scenarios

Present after every reset; asserted in `tests/demo/test_genesee_press_estate.py` (shape) and
`tests/db/test_genesee_press_demo.py` (through the API).

**A — Modern healthy share.** `\\GP-CORP-FS02\Design` (2023-11): `sec-share-design-read-only`
(Read & execute) and `sec-share-design-write` (Modify), people as direct members, no nesting,
inheritance unbroken in all eight folders, no person on any ACL.

**B — Legacy finance hierarchy.** `\\GP-FIN-FS01\Finance` (2017): `Finance-RW`, `GG_Finance_RW`,
`Finance_Read`, `FS-Finance-R`, `sec-share-finance-write`, `sec-share-finance-read-only` on one
root. `FIN-Accounting → Finance-Team → Finance-RW → GG_Finance_RW` grants Modify;
`FIN-Close-Team` grants it again; about 40% of Finance also hold the modern group directly.
Close folders name accountants; the controller, Lucia Hart, holds Full control on `Budgets`.

**C — Engineering sprawl.** `\\GP-ENG-FS01\Engineering`: 30+ folders, five root grants,
team folders, the Apollo/Nebula/Orion/Zephyr project folders, historical direct entries, and
`APP-GitHub-Users` granting file access through `Engineering-FS`.

**D — Sensitive People share.** `\\GP-HR-FS01\People`: migrated to the standard; Employee-
Relations and Compensation protected and limited to `HR-Confidential`. Exactly two
exceptions: Recruiting readable by `Hiring-Managers` (every manager), and a Finance payroll
specialist's direct entry on the payroll-vendor transition folder.

**E — Migration and archive debt.** `\\GP-ARCH-FS01\Archive` and
`\\GP-MIG-FS01\Migrations\FS-Consolidation-2020`: broken inheritance throughout, `Finance-RW`
granted twice on one ACL, `Project-Vega` and deleted accounts on ACLs, IT migrators' leftover
Full control, `FS-Legacy-Users` with Modify.

**F — Cross-department access, two ways.** Brianna Reddy
(`brianna.reddy@geneseepress.example`) moved from Finance to Sales Operations on 2022-02-01. On
`\\GP-FIN-FS01\Finance\Reports\Revenue` she holds Read & execute through
`Sales-Ops → Revenue-Reporting` and Modify through a direct entry from her Finance days.
`/api/v1/access/paths/principals/{sid}/resources/{path}` lists both.

**G — A six-level chain.** Camila Nair (`camila.nair@geneseepress.example`, payments on-call)
reaches `\\GP-ENG-FS01\Engineering\Runbooks\Payments-Legacy`, whose only grant is
`Share_Engineering_Modify`, through `ENG-Payments-Oncall → ENG-Payments → Engineering-Backend →
Engineering → Engineering-FS → Share_Engineering_Modify` — and through seven groups via
`APP-GitHub-Users`.

**The guided investigation (ADG-DEMO-02).** The demo guide opens with one access question
built on scenarios C and H: *why does Fatima Gordon have access to*
`\\GP-ENG-FS01\Engineering\Projects\Apollo`? Served by `GET /api/v1/demo` as
`investigations`, answered by the real engine, and read on `/access/investigate`. See
[access-investigation.md](access-investigation.md).

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

## 8. Where the directory comes from

The people, accounts, groups, memberships, servers, org units and organization facts are the
`adg` projection (`adg@0.3.0`) of one snapshot of the shared synthetic world,
`40ozs/synthetic-enterprise`, copied into `backend/vendor/synthetic-enterprise/` and pinned by
`SYNTHETIC_ENTERPRISE.lock` (repository, commit, snapshot, contract, dataset, projection,
manifest SHA-256). `app.demo.genesee_press.vendor` checks the lock, the manifest digest and every
file digest before anything is read, and refuses an edited file. Nothing is fetched at runtime.
To move to a newer snapshot: check out the commit in a local clone, run
`python scripts/vendor-synthetic-enterprise.py --source PATH`, regenerate the manifest, and let
the tests show what changed.

**ADG's demo estate is ADG's directory catalogue.** The canonical directory holds 509 groups
(dataset 2.1.0): the 206 ADG's file estate was designed around (those whose projection record
carries an ADG `legacy_name`) and 303 more from the catalogue the world was reconciled with.
SYNTH-CROSSAPP-01 merged OGWA's 12 case-only duplicates of ADG team groups (`eng-platform` /
`ENG-Platform`, ...) into ADG's groups, so the directory went from 521 to 509 groups and the
206 are unchanged; in the `ogwa` projection those groups now carry ADG's SIDs and objectGUIDs
too ([SYNTH-CROSSAPP-01](https://github.com/40ozs/synthetic-enterprise/blob/integration/SYNTH-CROSSAPP-01/docs/demo/reports/SYNTH-CROSSAPP-01-report.md)).
The demo loads every employee, the 206 groups and the memberships among them. The other 303
stay out: the
shared world permits application-specific subsets, and loading them (with the 20 nestings that
join them to the standard share groups) would change every count this document pins. A group
is added only when a concrete cross-application scenario needs ADG to see it, with the effect
measured and documented ([SYNTH-ADG-01 §6, §15](reports/SYNTH-ADG-01-report.md)).

ADG v1's own values survive only in the projection's `legacy_*` and `prior_*` fields and as
draw keys. ADG no longer exports an identity canon for other applications
(`null-meridian.canon.json` is retired): the shared world is the contract.

ADG's collector contract has no `whenCreated`, department, title, manager or owner field, so
those facts live in the canon and not in ADG's database; ADG carries the department as the
user's OU (`OU=Finance,OU=People,DC=corp,DC=geneseepress,DC=example`) and the naming era as the
group's OU (`OU=Legacy`, `OU=Teams and Applications`, `OU=Okta Transition`, `OU=Standard`).

---

## 9. Reproducibility and performance

Each reset is fingerprinted by Demo Mode: SHA-256 over per-source digests of current state
(all seven `current_*` views), `object_versions`, `scan_runs`, `scan_run_scopes`,
`scan_run_errors` and `risk_findings`, excluding only the database-clock bookkeeping columns,
risk-evaluation bookkeeping and serials. Validity windows, run ids, hashes, evidence and
finding detail are all included. Two clean resets of the integrated application, committed as
[`evidence/reset-1.json`](evidence/reset-1.json) and [`evidence/reset-2.json`](evidence/reset-2.json)
(measured in the integration container — PostgreSQL 16, one CPU-bound Python process — and
recorded with timings in the [integration handoff](../handoffs/adg-demo-integration-01.md)),
produce the same fingerprint, the same per-source digests and the same counts.

Before integration this package fingerprinted ten tables itself (`scan_runs`, `principals`,
`membership_edges`, `servers`, `smb_shares`, `smb_share_aces`, `ntfs_resources`, `ntfs_aces`,
`object_versions`, `risk_findings`, risk timestamps included); that algorithm is kept as
`frontend/cloudflare/checks/genesee_press_fingerprint.py` for hosted checks.

**After SYNTH-ADG-01** (the move to the shared world and the Genesee Press names), two clean
resets give Demo Mode fingerprint
`4667d3cfb4b574ffda3ac7ea8453912568b7517cd53548fb338b497e9b270f08` (ADG v1:
`228b9dce…f8f9efd0`) and logical fingerprint
`b4b93b65a77d895cbd0495d34865937bdab39596494bd3ca035ce8f8e59445d0` (ADG v1: `db736602…85c28a0`).
Both moved because names moved — mail, DNs, server, group and share names, run ids — and
nothing else did: the 300 collector transcripts are identical to ADG v1's once the documented
renames are applied, and every count, probe, run status and risk total in the reset report is
unchanged ([SYNTH-ADG-01 report](reports/SYNTH-ADG-01-report.md), parity evidence).
