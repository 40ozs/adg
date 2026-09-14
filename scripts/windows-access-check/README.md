# Windows access-check harness

Asks Windows what it grants, so that ADG's effective-access engine can be checked against the
only authority that matters instead of against this repository's belief about Windows.

Two probes, answering two different questions.

| Script | Question | Needs |
| --- | --- | --- |
| `Invoke-AdgAccessOracle.ps1` | What does the access check grant, for any DACL shape? | PowerShell 7. Nothing else. |
| `Invoke-AdgRealAccessProbe.ps1` | What does a real process get on a real directory? | PowerShell 7 and a writable `TEMP`. |

**Neither requires elevation, a domain, a file share, or any account you have to create.**
That is deliberate: a harness that needs a prepared host is a harness nobody runs, and the
comparison it produces stops being evidence. Both write committed fixtures, so the comparison
runs in CI and on machines with no Windows at all.

---

## Running them

```powershell
# Capture the whole matrix. ~5,600 cases, under a second.
pwsh -File scripts/windows-access-check/Invoke-AdgAccessOracle.ps1

# Measure real directories with the running process's own token.
pwsh -File scripts/windows-access-check/Invoke-AdgRealAccessProbe.ps1
```

Both default to writing into `backend/tests/fixtures/windows/`. Pass `-OutputPath` to write
somewhere else — useful for comparing two hosts before committing either.

Then:

```powershell
backend\.venv\Scripts\python.exe -m pytest tests/validation/test_windows_oracle.py -q
```

---

## When to re-run the oracle

**Whenever `backend/tests/access_engine/matrix.py` changes the cases it generates.**

The fixture records a fingerprint of the inputs each answer was given. If a case is edited
without the probe being re-run, `test_windows_oracle.py` fails with a message telling you to
re-run it, rather than comparing the engine against an answer to a different question. It is a
hard failure and not a skip, on purpose: a stale oracle reports a pass.

Re-running on a different Windows build is also worth doing, and the fixture records the host
so that a difference between two captures can be attributed.

---

## Why `AuthzAccessCheck`

`authz.dll` is the API behind the **Effective Access** tab in the Windows security dialog.
Three properties make it the right instrument:

1. **It evaluates a descriptor supplied as bytes.** A case does not have to exist on disk, so
   every DACL shape is reachable — including the ones `Set-Acl` silently reorders into
   canonical order, which is exactly where the interesting behavior lives. Windows honors the
   order a descriptor stores; an Allow ahead of a Deny wins; and that cannot be tested at all
   through an API that reorders.
2. **`AUTHZ_SKIP_TOKEN_GROUPS` plus `AuthzAddSidsToContext`** build a context holding only the
   SIDs supplied. ADG's constructed token and Windows' token can therefore be made identical,
   which makes the comparison about the access check alone rather than about who agrees on
   group membership.
3. **`MAXIMUM_ALLOWED`** returns the whole granted mask rather than a yes/no — the same
   question `evaluate_acl` answers.

### Generic rights are mapped first

`MapGenericMask` is applied to every ACE before the check, because that is what the file
system does when a descriptor is written: a stored file ACE never carries a generic bit.
Authz applies no mapping of its own, and an unmapped `GENERIC_ALL` ACE grants **nothing at
all** — so skipping this step would compare ADG's generic expansion against Windows declining
to expand anything, and every generic case would look like a bug.

The four mapped values are recorded in the fixture and held against
`FILE_SYSTEM_GENERIC_MAPPING` by a test, so the constants ADG hard-codes are measured rather
than quoted.

---

## Why a real-directory probe as well

The synthetic descriptor has one blind spot, and it matters: it carries no object type, so
Authz cannot apply the file system's valid-rights mask. On a NULL DACL it answers `0x001FFFFF`
where a real directory grants `FILE_ALL_ACCESS` (`0x001F01FF`).

`Invoke-AdgRealAccessProbe.ps1` settles that by measuring the real thing — it creates
directories, writes descriptors to them, opens them with `MAXIMUM_ALLOWED`, and reads the
access the kernel actually granted out of the handle with `NtQueryObject`.

It records **both** numbers for each case, `authz_granted` and `open_granted`, because they
answer different questions and the difference is itself a finding. See
`docs/architecture/effective-access-limits.md` §4: on a DACL that grants the token nothing the
access check reports the owner's `READ_CONTROL`/`WRITE_DAC` and the open is refused outright,
and both are true.

It writes a NULL DACL with `SetFileSecurityW` and a hand-built descriptor, because .NET's
`ObjectSecurity` cannot express one: it always carries a DACL, and an empty DACL means the
opposite of a missing one.

---

## What is not covered, and what it would take

**Share ACLs and cross-machine access.** These probes cover the NTFS layer. The share layer is
the same access check over the share's descriptor, and the engine crosses the two by
intersection (Phase 4A), but neither is exercised against a live SMB share here.

Doing that needs a host where you can create local accounts and shares, which means
elevation:

| Step | Privilege |
| --- | --- |
| `New-LocalUser`, `New-LocalGroup`, `Add-LocalGroupMember` | Local Administrator |
| `New-SmbShare`, `Grant-SmbShareAccess` | Local Administrator |
| Connecting as the created user to `\\localhost\<share>` | The created account's password |
| Cleanup (`Remove-SmbShare`, `Remove-LocalUser`) | Local Administrator |

Run it on a throwaway VM and never on a machine that matters: it creates real accounts with
known passwords. This phase did not build it, and
`docs/handoffs/phase-04c-effective-hardening.md` records why and what it would add.

---

## Files

| File | Contents |
| --- | --- |
| `AdgAuthzProbe.psm1` | The Authz P/Invoke: access check, batch check, context group dump, generic mapping |
| `AdgRealAccessProbe.psm1` | `CreateFile`/`NtQueryObject` granted access, NULL-DACL writer, current token dump |
| `Invoke-AdgAccessOracle.ps1` | Emits the matrix, asks Windows, writes `ntfs-access-oracle.json` |
| `Invoke-AdgRealAccessProbe.ps1` | Builds real directories, writes `real-directory-access.json` |

`Get-AdgTokenGroup` in `AdgAuthzProbe.psm1` dumps the group SIDs Windows itself places in a
context built from a SID. That is the measurement ADG's `TokenAssumption` model is checked
against; unlike the rest, it needs a SID the local system can resolve.

---

## A trap worth knowing

An ACE's `AccessMask` is a **signed** `Int32` in .NET. `GENERIC_READ` (`0x80000000`) arrives
as `-2147483648`, and a direct `[uint32]` cast throws. Mask through `Int64` first:

```powershell
$unsigned = [uint32](([int64]$ace.AccessMask) -band 0xFFFFFFFFL)
```

The same applies to PowerShell hex literals: `0x80000000` parses as a negative `Int32`, so
write `0x80000000L` wherever you mean the unsigned value.
