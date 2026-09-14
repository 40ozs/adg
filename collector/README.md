# Collectors

Native Windows collectors. These run as Windows processes, services, or scheduled tasks —
never in Docker — because collection requires domain context and Windows APIs.

- `powershell/` — PowerShell 7 collection scripts and modules (Windows PowerShell 5.1 only
  where a required module cannot run correctly under PowerShell 7).
- `service/` — the long-running collector host that schedules runs and submits observations
  to the API.

Collectors are read-only and report observations. They contain no authorization or business
logic: effective-access semantics live in the backend (`backend/app/access_engine`).

- `powershell/smb/` — SMB shares and raw share-level ACLs (Phase 2A). See its
  [README](powershell/smb/README.md) for the privileges, firewall rules, and remote-management
  prerequisites collection needs.

Run the PowerShell collector suites with `.\scripts\collector-test.ps1`.
