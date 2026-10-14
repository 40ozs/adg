# ADG local-groups collector

Reads the local groups of named Windows hosts, and each group's **direct** members, and reports
them as [contract v1](../../../docs/contracts/collector-protocol.md) observations:

* every group in the host's own account database is a `local_group` principal scoped to the
  host. That covers BUILTIN\Administrators (`S-1-5-32-544`), Remote Desktop Users (`-555`),
  Remote Management Users (`-580`) and the rest;
* every member is a `local_group_member` edge on that host, keyed by the SID the host reported,
  with the kind the host could establish.

It reports readings, never conclusions. Whether a member administers the host, may sign in over
Remote Desktop or may use WinRM is derived by the backend's relationship projector
(ADG-GRAPH-01B). Remote Desktop and WinRM come out conditional, because the logon right, the
services and their access policy are not collected.

**ADG is read-only** ([ADR-0004](../../../docs/decisions/0004-read-only-collector-posture.md)).
The collector calls `NetServerGetInfo`, `NetLocalGroupEnum`, `NetLocalGroupGetMembers` and
`LookupAccountName`, and frees their buffers. Nothing else: it changes no group and no account.
A test pins the set of Win32 functions it binds.

---

## Running it

```powershell
# What it would send, written to disk
.\Invoke-AdgLocalGroupScan.ps1 -ComputerName FS01, FS02 -Offline -OutputDirectory C:\adg\out

# Collect and submit; the collector key is read from $env:ADG_COLLECTOR_KEY
.\Invoke-AdgLocalGroupScan.ps1 -ComputerName FS01, FS02 -ApiBaseUrl https://adg.corp.example.com
```

**Name each host exactly as the SMB and NTFS collectors address it.** The host name is part of a
local group's identity. `fs01|S-1-5-32-544` read here joins an NTFS entry on `\\FS01\...` naming
`BUILTIN\Administrators` only when the two spellings agree. An FQDN here and a short name there
are two different hosts to ADG, because nothing proves otherwise.

Each host is its own run, always. A host read completely, with every group enumerated and every
member list read without one error, reconciles its `local_groups_host` scope, so a member removed
since the last run is recorded as removed. A host read with any error finishes `partial`
and reconciles nothing. A host that cannot be reached finishes `failed`. The exit code is 0
when every host reconciled and 2 when any did not.

Targets are always explicit. There is no domain-wide sweep, for the reason the SMB collector
gives: a tool that sprays connection attempts across every computer object looks like
reconnaissance.

It is not yet a job kind of the [orchestrator](../orchestrator/), so it has no schedule or
checkpoint of its own. Run the entry script directly, or schedule it.

## What it refuses

* **Domain controllers.** A domain controller has no local account database:
  `NetLocalGroupEnum` there returns the domain's BUILTIN and domain-local groups. Those belong
  to the directory collector, and recorded as one host's local groups they would sit in the wrong
  scope. The run fails with `domain_controller` and reads nothing.
* **A group whose name does not resolve to the host's own alias.** Each enumerated name is
  resolved on the host and must come back as an alias issued by `BUILTIN` or by the host itself.
  Anything else is reported as `lookup_failed` and left out.

## What it keeps that CIM would drop

`Win32_GroupUser` resolves members to account objects. It silently omits a member whose SID no
longer resolves, such as a deleted account or a domain the host cannot reach, which is
exactly the orphaned entry in BUILTIN\Administrators an audit exists to find.
`NetLocalGroupGetMembers` returns the SID whatever it resolves to. A deleted, invalid or unknown
SID is reported with `member_kind: unresolved`, and the backend carries it as an unresolved
membership, never as no membership.

No principal observation is sent for a member. A domain account's attributes are the directory
collector's to report, and a host's partial view of them must not overwrite that.

## Minimum privileges

Reading another host's local groups remotely (`NetLocalGroupGetMembers` over SAMR) is
restricted on current Windows builds. By default only members of the target's local
Administrators may do it (`RestrictRemoteSAM`, "Network access: Restrict clients allowed to make
remote calls to SAM"). The least-privilege configuration is the SMB collector's: a dedicated
collector account in the file servers' local Administrators, never Domain Admins. An account
without the right gets `access_denied` for that host, and the run finishes `partial` without
reconciling.

Network: SMB (TCP 445) to each target, since the Net* calls use named pipes over SMB.

## Layers

| File | Does |
| --- | --- |
| `functions/AdgLocalGroupSource.ps1` | the only code that touches a host: the netapi32 bindings, compiled on first use. It is the test seam. |
| `functions/AdgLocalGroupObservation.ps1` | readings to contract observations (pure) |
| `functions/AdgLocalGroupScan.ps1` | one host, one run: refusals, error codes, scope, batching, completion |
| `Invoke-AdgLocalGroupScan.ps1` | the entry script |

Payload construction and transport are the shared [contract module](../common/)'s.

## Validation, stated plainly

| What | Where | State |
| --- | --- | --- |
| Translation, orchestration, refusals, error handling (mocked seam) | `tests/AdgLocalGroupCollector.Tests.ps1`, Pester | runs in CI on `windows-latest` |
| The real netapi32 bindings, reading the machine running the tests | same file, `Reading this machine`, Windows only | runs in CI on `windows-latest` (a standalone runner, no domain) |
| Offline output against the published schemas and backend models | `backend/tests/contracts/test_local_groups_collector.py` | runs wherever `pwsh` is on `PATH` (not in the Linux CI job) |
| Offline output ingested, and what the projector derives from it | `backend/tests/db/test_graph_derivation.py::TestTheLocalGroupsCollector` | same |
| A remote, domain-joined file server | — | **not yet validated.** No such host was available to ADG-GRAPH-01B. |
