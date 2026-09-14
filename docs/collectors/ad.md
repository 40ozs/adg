# Active Directory collector

**Status:** implemented (Phase 1A)
**Code:** [`collector/powershell/ad/`](../../collector/powershell/ad/)
**Shared primitives:** [`collector/powershell/common/`](../../collector/powershell/common/)
**Protocol:** [`docs/contracts/collector-protocol.md`](../contracts/collector-protocol.md)
**Decisions:** [ADR-0001](../decisions/0001-sid-as-identity.md),
[ADR-0002](../decisions/0002-graph-preserving-membership.md),
[ADR-0004](../decisions/0004-read-only-collector-posture.md)

Collects users, groups, computers, managed service accounts, and foreign security
principals from a domain, along with their **direct** membership edges.

Two properties define this collector and both are load bearing:

* **It never expands nested groups.** If group A contains group B and group B contains
  Alice, it reports two edges — A→B and B→Alice — and never A→Alice. The chain is the
  explanation of Alice's access and the thing an administrator has to change to remove it;
  a flattened set answers "can she?" but not "why, and what do I take away?".
* **It never requires Domain Admin.** Read access to the directory is enough, and any
  authenticated domain account has that by default. See [Privileges](#privileges).

---

## 1. Requirements

| | |
| --- | --- |
| PowerShell | 7.0 or later (`#Requires -Version 7.0`) |
| .NET assemblies | `System.DirectoryServices.Protocols`, present on any Windows host with .NET |
| RSAT / `ActiveDirectory` module | **not required** |
| Network | LDAP to a domain controller: TCP 389 (signed and sealed) or 636 (LDAPS) |
| Account | any account that can read the directory; see [Privileges](#privileges) |

The RSAT `ActiveDirectory` module is deliberately not used. `System.DirectoryServices.Protocols`
is present without installing a Windows feature, it exposes paging and ranged retrieval
explicitly rather than hiding them, and it surfaces LDAP result codes — which is what lets
the collector tell a permissions failure from an outage instead of guessing.

---

## 2. Quick start

```powershell
# Write the payloads to disk instead of sending them, to see exactly what would be posted.
.\collector\powershell\ad\Invoke-AdgAdCollector.ps1 -Offline -OutputDirectory C:\code\adg\.tmp\ad-run

# Send to the API.
$env:ADG_COLLECTOR_TOKEN = '<token>'
.\collector\powershell\ad\Invoke-AdgAdCollector.ps1 -ApiBaseUrl https://adg.corp.example.com

# From a configuration file, overriding one setting on the command line.
.\collector\powershell\ad\Invoke-AdgAdCollector.ps1 -ConfigPath C:\ProgramData\ADG\ad-collector.json -BatchSize 250
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | `succeeded` — every declared scope enumerated completely, no errors. |
| `2` | `partial` — usable observations, incomplete coverage. Something was unreadable. |
| `1` | `failed` — coverage unknown. |

`2` is a real result, not a crash: the observations were valid and were ingested. A
scheduled task should alert on it without treating it as a failure to run at all.

---

## 3. Privileges

**Domain Admin is not required and must not be used.**

Normal collection needs exactly one thing: the ability to read the directory. In a default
Active Directory installation, `Authenticated Users` (and therefore every domain account)
can read the attributes this collector requests:

| Attribute | Why |
| --- | --- |
| `objectSid` | identity — everything else is metadata |
| `objectClass` | user vs computer vs group vs foreign security principal |
| `sAMAccountName`, `userPrincipalName`, `displayName`, `name` | human-readable metadata |
| `distinguishedName` | scoping, and resolving member references |
| `userAccountControl` | enabled or disabled |
| `primaryGroupID` | the membership that appears in no group's `member` attribute |
| `groupType` | group scope, and security vs distribution |
| `member` | the direct membership edges |
| `isDeleted`, `whenChanged`, `uSNChanged` | deletion state and the change watermark |

### Recommended account

Use a dedicated, low-privilege account or a group managed service account:

* member of `Domain Users` only;
* `Deny log on locally` and `Deny log on through Remote Desktop Services` on the collector
  host, so the account is useful for nothing but this;
* a long random password, or a gMSA so there is no password to store;
* **no** membership in `Domain Admins`, `Account Operators`, `Backup Operators`, or
  `Server Operators`.

### When a default read permission has been removed

Some environments tighten the default directory ACLs, or use the AdminSDHolder-protected
accounts' descriptors, so that part of the tree is unreadable. The collector does not work
around that:

* it reports a `collectorError` naming the object and the operation;
* it finishes the run as `partial`;
* it reconciles nothing, so nothing is marked absent;
* it never escalates privilege, takes ownership, or modifies an object to make a read
  succeed (ADR-0004).

Grant the collector account **Read** on the affected container if you want the coverage.
Do not grant it Write, and do not add it to a privileged group.

### What the collector never does

It issues search requests and nothing else. There is no add, modify, delete, or move
anywhere in the code, and contract v1 has no payload that could express a permission
change.

---

## 4. Configuration

Copy [`config/adg-ad-collector.example.json`](../../collector/powershell/ad/config/adg-ad-collector.example.json)
and edit it. Every key can be overridden on the command line.

| Key | Default | Meaning |
| --- | --- | --- |
| `domain` | — | DNS domain name, for documentation; a domain SID here is used as the scope key. |
| `domainController` | `null` | The server to bind. `null` lets the LDAP client locate one. |
| `port` | `389` | `389` (signed and sealed) or `636` with `useTls`. |
| `useTls` | `false` | LDAPS. When `false`, the connection is signed **and sealed** rather than clear. |
| `searchBase` | `null` | Defaults to the directory's `defaultNamingContext`. |
| `includeOrganizationalUnits` | `[]` | Enumerate only these DNs. Makes the run incremental. |
| `excludeOrganizationalUnits` | `[]` | Do not enumerate these DNs. Makes the run incremental. |
| `batchSize` | `500` | Observations per batch, 1–1000. |
| `pageSize` | `1000` | LDAP paged-search page size. |
| `rangeStep` | `1500` | Values requested per ranged `member` read. |
| `apiBaseUrl` | — | ADG API base URL. |
| `apiTokenEnvironmentVariable` | `ADG_COLLECTOR_TOKEN` | Where the bearer token is read from. |
| `skipCertificateCheck` | `false` | Development only. |
| `collectorHost` | `$env:COMPUTERNAME` | Reported as `source.collector_host`. |
| `offline` / `outputDirectory` | `false` / `null` | Write payloads instead of sending them. |
| `incremental` | `false` | Force the run to be unable to reconcile. |
| `stateFile` | `null` | Where to record the change watermark. |
| `maxAttempts` | `5` | Retry attempts for a transient failure. |
| `timeoutSeconds` | `120` | LDAP and HTTP timeout. |
| `principalFilter` / `groupFilter` | `null` | Override the LDAP filters. Rarely needed. |

**No secret belongs in this file.** The collector token is read at run time from the
environment variable the file names, and `Import-AdgAdCollectorConfig` rejects an
`apiBaseUrl` that carries something that looks like a credential. An unknown key is also
rejected rather than ignored: a misspelled `excludeOrganizationalUnits` would otherwise
silently collect an OU the operator believed was excluded.

---

## 5. Scope and reconciliation

A run declares one scope: `domain|<domain SID>`.

| Run | `incremental` | May reconcile? |
| --- | --- | --- |
| Whole naming context, no errors | `false` | **Yes** |
| Narrowed by `includeOrganizationalUnits` | `true` | No |
| Narrowed by `excludeOrganizationalUnits` | `true` | No |
| `incremental: true` in configuration | `true` | No |
| Any run that reported an error | — | No |

Reconciliation is the only way ADG marks anything absent. A run that deliberately read part
of the domain, or failed to read part of it, is structurally incapable of reconciling — so
an incomplete scan can never delete real access from the record.

**An exclusion limits enumeration, not membership.** A principal inside an excluded
container is still recorded if an in-scope group grants it membership, because an edge
pointing at a SID with no principal behind it cannot be audited. If you need a principal
genuinely absent from ADG, remove its access, not its OU from the scan.

---

## 6. What it handles, and how

| Situation | Behavior |
| --- | --- |
| **Nested groups** | Recorded as edges. Nesting is never followed; the inner group's own members are collected when the enumeration reaches it. |
| **Membership cycles** | Just two ordinary edges. Because nesting is never followed, a cycle needs no detection and cannot loop. |
| **Primary group** | Emitted as a `primary_group` edge, derived from `primaryGroupID` plus the account's domain SID. It appears in no group's `member` attribute; a collector that read only `member` would lose every user's primary group, usually `Domain Users`. |
| **Large groups** | Ranged retrieval is followed to its end. AD returns at most `MaxValRange` (1500) values per read and signals the truncation only by renaming the attribute to `member;range=0-1499`. A collector that stops there reports a 5000-member group as a 1500-member group. If the directory stops making progress mid-range, the read **fails** rather than returning a partial list that looks complete. |
| **Paged searches** | The LDAP provider pages every search. A `SizeLimitExceeded` result is treated as an error, not as the end of the results. |
| **Foreign security principals** | The stub object's RDN *is* the principal's SID, which is often all this domain knows and is enough. The edge carries `is_foreign_security_principal: true` and a principal observation is recorded for the SID. |
| **Members outside the scope** | Read directly and recorded, so the edge stays auditable. |
| **Inaccessible objects** | A `collectorError` naming the object and the operation; the run is `partial` and reconciles nothing. |
| **Transient DC failures** | Retried with exponential backoff (`maxAttempts`). Insufficient rights and "no such object" are **not** retried: retrying them burns time and hides the finding. |
| **Objects renamed mid-scan** | A member DN that stops resolving triggers one re-read of the group's membership. AD rewrites member DNs on rename, so the re-read yields the new name — and the SID, which ADG keys on, never changed. Any reference still unaccounted for after that is reported as an error. |
| **Deleted objects** | `isDeleted` is carried through as `is_deleted`. The collector does not enable the show-deleted control, so tombstones are only seen where they are discoverable normally. |
| **Unclassifiable objects** | Reported as `principal_kind: unresolved` with the name in `last_known_name`, never as a guessed `display_name`. |
| **A group listing itself** | Refused. Windows does not create such an edge; the collector records an error rather than emitting one or hiding it. |

---

## 7. Offline mode

`-Offline -OutputDirectory <path>` writes the exact bytes that would have been POSTed:

```text
<path>/
  start.json          the scan-run start envelope
  batch-001.json      one file per batch
  ...
  completion.json     the scan-run completion envelope
  envelopes.ndjson    every envelope above, one per line, in send order
```

This is not only a debugging aid. `backend/tests/contracts/test_ad_collector_offline.py`
runs the collector this way against a synthetic directory and validates the output against
the published JSON Schemas *and* through the backend models, which re-derive every
`source_key`. That comparison is what keeps the PowerShell key derivations and the
normative Python ones in `backend/app/contracts/v1/keys.py` from drifting apart.

`-DirectoryFixture <path>` runs the whole collector against a JSON directory fixture
instead of a domain. It is a development and test facility; it describes the fixture, not a
real environment.

---

## 8. Change metadata and the watermark

`-StateFile <path>` records the highest `uSNChanged` and `whenChanged` the run observed:

```json
{
  "schema": "adg-ad-collector-state/1",
  "run_id": "...",
  "status": "succeeded",
  "directory_server": "dc01.corp.example.com",
  "domain_sid": "S-1-5-21-...",
  "highest_usn_changed": 41204,
  "highest_when_changed": "20260914083300.0Z"
}
```

Two rules make this safe, and both are enforced in code:

1. **Only a clean, successful run writes a watermark.** A partial run did not read
   everything below its highest `uSNChanged`, so a later run starting there would skip
   exactly the objects this one failed to read — and nothing would ever look missing.
2. **The watermark records which server issued it.** `uSNChanged` is a per-server counter,
   so DC1's watermark means nothing to DC2. `Test-AdgAdCollectorStateUsable` refuses a
   watermark from a different server.

The watermark is captured now; the incremental query that consumes it is not yet
implemented. It does not travel on observations because contract v1 has no field for it and
forbids extra ones — adding one would be a contract change, not a collector change.

---

## 9. Operational examples

### A scheduled daily full collection

```powershell
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument (
    '-NoProfile -NonInteractive -File "C:\Program Files\ADG\collector\ad\Invoke-AdgAdCollector.ps1" ' +
    '-ConfigPath "C:\ProgramData\ADG\ad-collector.json"')
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
$principal = New-ScheduledTaskPrincipal -UserId 'CORP\adg-collector$' -LogonType Password

Register-ScheduledTask -TaskName 'ADG Active Directory collection' `
    -Action $action -Trigger $trigger -Principal $principal
```

The task should treat exit code `2` as a warning (incomplete coverage) and `1` as a failure.

### Collecting one OU while testing

```powershell
.\Invoke-AdgAdCollector.ps1 -Offline -OutputDirectory .\.tmp\staff `
    -IncludeOrganizationalUnit 'OU=Staff,OU=Corp,DC=corp,DC=example,DC=com'
```

The run is marked incremental and reconciles nothing, so it can be run against production
without any risk of marking unseen objects absent.

### Checking what a run would send, before sending it

```powershell
.\Invoke-AdgAdCollector.ps1 -Offline -OutputDirectory .\.tmp\preview
Get-Content .\.tmp\preview\batch-001.json | ConvertFrom-Json |
    Select-Object -ExpandProperty observations |
    Group-Object kind | Select-Object Count, Name
```

### Binding a specific domain controller over LDAPS

```powershell
.\Invoke-AdgAdCollector.ps1 -DomainController dc01.corp.example.com -Port 636 -UseTls `
    -ApiBaseUrl https://adg.corp.example.com
```

---

## 10. Tests

```powershell
.\scripts\collector-test.ps1                                  # the whole collector suite
.\scripts\collector-test.ps1 -Path collector\powershell\tests  # this collector only
cd backend; .\.venv\Scripts\python.exe -m pytest tests/contracts/test_ad_collector_offline.py
```

The Pester suite runs entirely against a fixture provider: no domain, no network. What it
proves includes the no-flattening guarantee (by comparing every emitted edge against the
edges the fixture literally states), ranged retrieval across many chunks, the rename
recovery, the refusal to reconcile after an error, and the refusal to write a watermark for
a partial run.

**Not covered by tests:** `New-AdgLdapDirectoryProvider` itself — the code that opens the
socket. It has never been run against a domain controller. Everything above it is exercised
through the fixture provider; the LDAP provider is thin by design for exactly that reason.
