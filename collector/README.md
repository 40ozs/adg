# Collectors

Native Windows collectors. These run as Windows processes, services, or scheduled tasks —
never in Docker — because collection requires domain context and Windows APIs.

- `powershell/` — PowerShell 7 collection scripts and modules (Windows PowerShell 5.1 only
  where a required module cannot run correctly under PowerShell 7).
- `service/` — the long-running collector host that schedules runs and submits observations
  to the API. Not built: since Phase 7B the scheduling is `powershell/orchestrator/`, driven
  by one Windows scheduled task, which needs no service to host it.

Collectors are read-only and report observations. They contain no authorization or business
logic: effective-access semantics live in the backend (`backend/app/access_engine`).

- `powershell/ad/` — Active Directory principals and membership (Phase 1A).
- `powershell/smb/` — SMB shares and raw share-level ACLs (Phase 2A). See its
  [README](powershell/smb/README.md) for the privileges, firewall rules, and remote-management
  prerequisites collection needs.
- `powershell/ntfs/` — the NTFS security descriptors of directory trees, and where
  permissions change within them (Phases 3A and 3B). See its
  [README](powershell/ntfs/README.md) for the privileges it needs and, just as importantly,
  the ones it refuses to acquire.
- `powershell/orchestrator/` — which collection job runs when, what it may resume from, and
  what it is entitled to reconcile (Phase 7B). One scheduled task drives all six jobs; the
  cadence lives in its configuration rather than in the scheduler. See its
  [README](powershell/orchestrator/README.md).

The share layer and the file-system layer are collected **separately and never merged**.
Remote access over SMB is limited by both ACLs; access at the console is limited only by the
NTFS one. Combining them is the backend's job, and it is a different claim — effective
access — reported on its own route.

Run the PowerShell collector suites with `.\scripts\collector-test.ps1`.
