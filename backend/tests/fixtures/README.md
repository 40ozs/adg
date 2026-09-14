# Canonical test vectors

Each file under `scenarios/` is a complete, replayable scan-run transcript in contract v1
form: a start envelope, one or more observation batches, and a completion envelope — exactly
the bytes a collector would POST.

**Every later phase should use these rather than inventing data.** One shared set of facts
is what keeps ingestion, the rights model, the effective-access engine, the explanation API,
and the UI describing the same estate.

| Scenario | What it pins |
| --- | --- |
| `01-direct-user-grant` | An ACE naming the user's own SID. The baseline. |
| `02-group-grant` | One membership edge between user and ACE. |
| `03-nested-group-grant` | AGDLP: user → global group → domain-local group → ACE. |
| `04-multiple-membership-paths` | Three independent paths, including a `primary_group` edge that a `member`-only collector would miss. |
| `05-cyclic-group-graph` | Ring-A ∈ Ring-B ∈ Ring-A. Expansion must terminate. |
| `06-unresolved-sid` | An orphaned SID holding Full Control, with no guessed name. |
| `07-deny-candidate` | Explicit Deny ordered before an Allow that would otherwise grant. |
| `08-inherited-ace` | An inherited ACE that records the ancestor it came from. |
| `09-broken-inheritance` | `SE_DACL_PROTECTED`; access arrives only through a host-scoped local group. |
| `10-smb-more-restrictive` | NTFS Full Control behind a share that grants Read. |
| `11-ntfs-more-restrictive` | Share Full Control in front of NTFS Read & Execute. |
| `12-partial-run-no-reconciliation` | A run that hit `access_denied`, finished `partial`, and reconciled nothing. |

## The `expectations` block

Each file carries an `expectations` object naming the subject, the resource, the layer that
limits access, the number of distinct paths, and so on. These are **expectations for later
phases to verify**, not results computed here: Phase 0B defines contracts and does not
implement effective access. Phase 4 should assert against them; if a fixture's expectation
turns out to be wrong, fix the fixture in that phase and say so in its handoff.

## Using them

From Python:

```python
from tests.fixtures import load_scenario, scenario_names

scenario = load_scenario("03-nested-group-grant")
scenario.edges  # membership observations, parsed
scenario.ntfs_aces  # NTFS ACE observations, parsed
scenario.start  # the ScanRunStart envelope
```

From PowerShell, as a request body:

```powershell
$transcript = Get-Content .\03-nested-group-grant.json -Raw | ConvertFrom-Json
$body = $transcript.batches[0] | ConvertTo-Json -Depth 12 -Compress
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body $body `
    -Uri "http://localhost:8000/api/v1/scan-runs/$($transcript.start.run_id)/batches"
```

## Rules

* They are validated on every test run against the published schemas *and* through the
  backend models (`tests/contracts/test_fixtures.py`), including cross-checks that every ACE
  has a resource observation and that declared `ace_count` values match.
* They contain no derived conclusions — no effective access, no expanded membership.
* Add a scenario rather than editing one when a phase needs a new shape; an existing
  scenario is a fixed point other phases assert against.
* Every SID here is synthetic. Never commit data captured from a real domain.
* They were first generated through the contract models, so each was valid by construction;
  the JSON is now the artifact and is maintained directly. Nothing regenerates it, so an
  edit must keep the whole transcript consistent — the tests check `ace_count` against the
  ACEs reported, every ACE against a resource observation, every share ACE against a share,
  and every `source_key` against its derivation.
