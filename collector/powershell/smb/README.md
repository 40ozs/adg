# ADG SMB share collector

Discovers Windows file servers' shares and their raw share-level ACLs, and reports them as
[contract v1](../../../docs/contracts/collector-protocol.md) observations.

It reports readings, never conclusions. There is no effective access here, no expanded
group membership, and no risk verdict — those are computed by the backend from these facts
together with the NTFS layer. The two layers are separate on purpose: remote access is
limited by **both** the share ACL and the NTFS ACL, and local access bypasses the share
layer entirely.

**ADG is read-only.** Nothing in this collector writes to a target server, takes ownership,
or modifies a security descriptor to make a read succeed ([ADR-0004](../../../docs/decisions/0004-read-only-collector-posture.md)).

---

## Running it

```powershell
# See what it would send, without a server
.\Invoke-AdgSmbScan.ps1 -Server FS01 -DryRun -OutputDirectory C:\code\adg\.tmp\smb

# Collect and submit
.\Invoke-AdgSmbScan.ps1 -ConfigPath .\adg-smb-targets.json -ApiBaseUrl http://localhost:8000 -RunPerServer
```

Copy [`adg-smb-targets.example.json`](adg-smb-targets.example.json) to
`adg-smb-targets.json` and edit it. The example documents every option inline.

**Use `-RunPerServer` for anything larger than a handful of hosts.** A run that reports any
error may not reconcile, so in one combined run across fifty servers, a single unreachable
host prevents every *other* server's shares from ever being marked absent. Per-server runs
contain that: the forty-nine healthy servers still reconcile.

Targets are always explicit. There is no domain-wide sweep, and that is a posture decision
rather than a missing feature — a tool that sprays connection attempts across every
computer object looks exactly like reconnaissance, and it makes a run's declared scope mean
something vague.

---

## Minimum privileges

Domain Admin is **not** required and must not be used.

| To read | The account needs |
| --- | --- |
| The share list and share ACLs | Membership in the target server's local **Administrators** group, **or** an account granted remote WMI/CIM access to the `root\CIMV2` and `root\Microsoft\Windows\SMB` namespaces plus `READ_CONTROL` on the shares |
| Host identity (`Win32_ComputerSystem`, `Win32_OperatingSystem`) | The same remote WMI/CIM read access |
| Nothing else | The collector never needs write, `WRITE_DAC`, `WRITE_OWNER`, or `SeTakeOwnershipPrivilege` |

Reading a *share* security descriptor is an administrative operation on most Windows
builds: `Win32_LogicalShareSecuritySetting` is not readable by an unprivileged remote
caller. The practical least-privilege configuration is therefore a dedicated collector
account placed in **local Administrators on the file servers only** — never Domain Admins,
and never in a group with rights over domain controllers.

Run the collector as a **group managed service account (gMSA)** where possible. It holds no
password you have to store, which is why the configuration file has no credential field.
`-Credential` exists for interactive testing.

### What least privilege costs you

An account without rights to read a share descriptor does not silently return fewer ACEs.
The collector records an `access_denied` error, finishes the run as `partial`, and
reconciles nothing — so a permissions problem shows up as an incomplete scan, not as a
share that appears to grant nobody anything.

---

## Remote management prerequisites

Collection uses CIM over WinRM by default.

| Requirement | Detail |
| --- | --- |
| **WinRM enabled** on each target | `Enable-PSRemoting -Force`, or the *Allow remote server management through WinRM* Group Policy |
| **Firewall** | TCP **5985** (HTTP) or **5986** (HTTPS) inbound on the target, from the collector host only |
| **DCOM alternative** | Set `"protocol": "Dcom"`. Needs TCP **135** plus the dynamic RPC range (49152–65535 by default), which is usually *harder* to allow than WinRM, not easier |
| **Name resolution** | The collector addresses each server by the name you configure; that name must resolve and match the host's Kerberos identity |
| **Kerberos** | Both hosts domain-joined. For a non-domain target, WinRM requires HTTPS or a `TrustedHosts` entry |

A target that is unreachable is reported as an error against that host and leaves the run
`partial` or `failed`. It is never reported as a server with no shares.

---

## What it collects

| Observation | Source |
| --- | --- |
| `server` | `Win32_ComputerSystem`, `Win32_OperatingSystem` — name, FQDN, NetBIOS name, domain membership, OS caption |
| `smb_share` | `Get-SmbShare -IncludeHidden` — name, local path, type, description, user limit, caching mode, `is_special` |
| `smb_ace` | `Win32_LogicalShareSecuritySetting.GetSecurityDescriptor()` — trustee SID, allow/deny, raw access mask |
| `principal` | Only for a trustee SID that did not resolve, as `principal_kind: unresolved` |

`computer_sid` and `domain_sid` are deliberately left unset: a computer's SID is an Active
Directory fact that the Phase 1 collector reads from the directory.

**One source key reaches a batch once.** An orphaned SID is usually orphaned estate-wide, so
it sits on the ACL of several shares of one server and every one of them describes it — and a
batch may not carry one key twice, because two observations with one key are indistinguishable
and the API rejects the whole batch with a 422. The repeat is the same fact about the same
SID, so it is dropped; the finding is not, and `observation_count` reports what the batches
actually carry. A key repeating in a *different* batch is not a repeat: the server keys
observations by `(run_id, source_key)` and ignores the second arrival.

### Two ways to read a share ACL

| | Descriptor (preferred) | Permission levels (fallback) |
| --- | --- | --- |
| API | `Win32_LogicalShareSecuritySetting` | `Get-SmbShareAccess` |
| Trustee | **SID**, always present | Account **name**, which may not translate |
| Right | Raw 32-bit access mask | `read` / `change` / `full`, or `Custom` |
| Contract field | `access_mask` | `permission` |

The descriptor is preferred because a descriptor *stores* SIDs: a trustee whose name no
longer resolves still yields a usable ACE. The level API reports names, so an ACE whose
name will not translate cannot be reported at all — the contract identifies a trustee only
by SID — and such an ACE is recorded as a `lookup_failed` error rather than dropped.

`Auto` tries the descriptor and falls back. **Pin `aclMethod` in a mixed estate.** The two
forms are legitimately different readings of one ACL and produce different source keys, so
an ACL that alternates between them churns the history for no reason.

Note that `Win32_LogicalShareSecuritySetting` has no instance for an administrative share
(`C$`, `ADMIN$`, `IPC$`) — those carry a default descriptor rather than a stored one — so
reading one falls back to the level API. They are excluded by default anyway.

---

## Which shares are collected

Precedence, highest first:

1. an explicit `excludeShares` pattern — a deny list you wrote is never overridden;
2. an explicit `includeShares` pattern, when any are configured;
3. administrative shares, excluded unless `includeAdminShares`;
4. ordinary hidden shares (`Data$`), excluded unless `includeHiddenShares`;
5. non-disk shares (print, IPC, device), excluded unless `includeNonDiskShares`;
6. otherwise included.

Patterns are case-insensitive wildcards matched against the share name.

**Hidden is not the same as administrative.** A trailing `$` only suppresses a share from
browsing; `Data$` is an administrator publishing real data unlisted, while `C$` is Windows's
own. The collector distinguishes them by the SMB server's `Special` flag, falling back to a
name test only when the server did not report one. If your estate publishes real data on
hidden shares, turn `includeHiddenShares` on.

By default an excluded share is still **recorded as existing** — only its ACL goes unread.
That is what lets a run honestly claim to have enumerated every share on the server, which
is in turn what lets ADG notice that a share was deleted. See the scope discussion below.

---

## Scopes, coverage, and why a bad scan cannot delete anything

Reconciling a scope is the only mechanism by which ADG marks an object absent. A scope must
therefore never claim more than the run actually read.

| Scope | Declared when | Means |
| --- | --- | --- |
| `server\|<host>` | every share on the host was enumerated | shares missing from the run were deleted |
| `share\|<host>\|<share>` | that share's ACL was read completely | ACEs missing from the run were removed |

An excluded share gets no `share` scope, so its unread ACL is never mistaken for an empty
one — which would read as *nobody has access*, the exact inversion the contract warns
about. Setting `recordExcludedShares` to `false` drops excluded shares entirely; the
`server` scope is then not declared for that host, and deletions there stop being
detectable. That is the trade, and it is yours to make.

Any error at all makes the run `partial`, and a `partial` run reconciles nothing. This is
structural, not a convention: the contract rejects a completion that claims both an error
and a reconciled scope.

---

## Tests

```powershell
.\scripts\collector-test.ps1 -Path collector\powershell\smb\tests
```

Every function that touches a remote host lives in `functions/AdgSmbSource.ps1` and is
mocked, so the whole suite runs on a workstation with no domain, no file server, and no
network.

The collector's output is separately validated against the published JSON Schemas *and*
through the backend models — which independently re-derive every `source_key` — by
`backend/tests/contracts/test_smb_collector.py`. That comparison checks the PowerShell key
derivations against the Python ones, and is what keeps this collector from drifting away
from the contract.

---

## Layout

| File | Role |
| --- | --- |
| `Invoke-AdgSmbScan.ps1` | Entry point |
| `functions/AdgSmbConfig.ps1` | Targets and the include/exclude policy |
| `functions/AdgSmbSource.ps1` | The only code that touches a remote host — the test seam |
| `functions/AdgSmbObservation.ps1` | Raw readings to contract observations (pure) |
| `functions/AdgSmbScan.ps1` | Orchestration, retries, scopes, batching |
| `functions/AdgSmbTransport.ps1` | Submission, with the contract's retry rules |
