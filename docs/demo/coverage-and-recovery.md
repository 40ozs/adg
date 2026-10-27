# Coverage and recovery (ADG-DEMO-03)

Part of the [ADG demo guide](README.md) (Walkthroughs 2 and 3). This page is the detailed account.

> **Synthetic.** Every server, scan, identity and finding here is generated Genesee Press demo
> data. None of it describes a real organization or system.

The guided investigation ([access-investigation.md](access-investigation.md)) answers *why
does Fatima Gordon have access to Apollo?* This walkthrough continues it with two more
questions a reviewer asks next:

1. **How complete is the evidence behind this result?**
2. **What happens when ADG cannot finish a comparison within the interactive window?**

Both are answered by the real application over the reset estate, in about five minutes. The
demo guide (`/demo`) lists the ten steps under *Then: how complete is the evidence, and what
if a comparison cannot finish?*, each of the first five linked into the page it is about.

## The walkthrough

**Coverage**

| # | Step | Where |
| --- | --- | --- |
| 1 | View the risk totals, and how ADG says they may be read | `/risks`, banner `#risk-reading` |
| 2 | Inspect the collection coverage beside the counts | `/risks`, *Collection behind these counts* (`#risk-coverage`) |
| 3 | Identify the unavailable source and the age of the evidence ADG still holds for it | the same card's disclosure; `/collectors` for every scope |
| 4 | Return to the investigation: its answer stands, and the incomplete scopes cannot change it | `/access/investigate`, section 6 (`#collection-coverage`) |

**Recovery**

| # | Step | Where |
| --- | --- | --- |
| 5 | Launch the synthetic comparison drill | `/demo`, **Run the comparison drill** |
| 6 | Observe the timed-out state: no partial list is presented as the answer | `/changes/compare`, *How this comparison ended* |
| 7 | Open **Diagnostics**: the window, how far it got, what it read | the same panel |
| 8 | Retry inside the standard window. It stops again, as *incomplete*, at a named boundary | **Retry inside the standard 4-second window** |
| 9 | Compare only folder permission entries: a narrower comparison that fits | **Compare only folder permission entries (3,947 → 4,124 objects)** |
| 10 | Confirm the result is marked complete, and that it recovered the earlier attempt | *Complete — this comparison recovered the earlier attempt* |

## What the reviewer sees, and why it is true

**The estate's collection.** Three scan generations, 300 runs. Two scopes are not complete
after the last one, by the estate's own history:

* NTFS on `GP-FS-03`: the 2026-10-01 scan could not reach the server (`host_unreachable`).
  It is **unavailable**, and ADG still holds the folders it read on 2026-09-17: **older
  evidence**, not missing evidence, and not "nothing there".
* NTFS on `GP-MIG-FS01`: the 2026-10-01 scan was **partial** (one folder unreadable).

So coverage reads `98 of 100 collection scopes collected completely · 1 unavailable · 1
partial · 1 holding older evidence`. It is a count of scopes, never a percentage: a scope is a
whole server or the whole directory, and they are not equal in size.

**The risk totals.** 9,386 open findings (3 critical, 8 high). The banner says *These counts
are not exhaustive*, and why. The 189 open findings on `GP-FS-03` are still counted, from the
scan that last read them: a failed scan closes nothing, and the page says so. The counts sit
beside the snapshot they describe; the risk pass records the collected state it read, so a
total over a snapshot that has since moved reads *stale*, not current.

**The investigation.** Fatima's answer reads four scopes: the directory, and `GP-ENG-FS01`'s
shares, folders and local groups. All four were collected completely, so *missing collection
elsewhere cannot change this answer*. The answer's own certainty (`at_least`, two open
questions) is unchanged: coverage is context beside the answer, never part of it.

**The comparison.** The drill compares the whole estate between ADG's first and latest scans.
Estate wide it reads more objects per instant (13,765 and 14,139) than one interactive
comparison does (5,000 per side), so it can never be complete in one request. The drill
launches it inside a deliberately short 1 ms window, which no host reads two instants within,
so it stops at its first checkpoint every time:

```
start → timed_out (1 ms window, at the read step: nothing presented)
      → retry inside the standard window → incomplete (compared through a named boundary
                                                        in the group memberships; labelled partial)
      → compare only folder permission entries → complete, recovered (221 differences)
```

Every step is a real request; the recovery actions are the API's, each naming the attempt it
recovers from. The outcome is stated in words and a symbol, as an announced status; a link
that is running says *comparing…* until the outcome replaces the page.

## The states, as ADG defines them

| Scope state (latest attempt) | Evidence held | Meaning |
| --- | --- | --- |
| `collected` | current | the latest scan was complete; a zero is "checked, nothing there" (`returned_nothing`) |
| `partial` | current | it ran and missed part (errors, an undelivered batch, an unreconciled scope) |
| `unavailable` | stale or none | it could not reach the target (`host_unreachable`, `rpc_unavailable`, `timeout`) |
| `failed` | stale or none | it failed or was canceled for any other reason (e.g. `access_denied`) |
| `running` | current or none | still open |

A collector kind nobody has ever reported is `not_collected` when the estate expects it
(`ADG_EXPECTED_COLLECTORS`, or a demo dataset's own scan history) and `not_configured` when it
does not; ADG cannot tell an intentional omission from a collector nobody set up, and says so.

| Comparison outcome | Presented |
| --- | --- |
| `complete` | the whole comparison; `recovered` when it retried an earlier attempt |
| `incomplete` | the part up to `compared_through`, labelled partial |
| `timed_out` | nothing but what was read and how far it got |
| `cancelled` | nothing; the caller had gone (in the log only) |

## Where it lives

* `GET /api/v1/collection/coverage[?resource=]` — `app/domain/coverage.py` (pure),
  `app/services/coverage.py`; built on the collector operations records, not a second model.
* `GET /api/v1/risks/summary` — `collection` and `interpretation` (`interpret_totals`);
  migration `0018_risk_evaluation_basis` records each risk pass's snapshot.
* `GET /api/v1/changes/compare` — `ChangeService.compare` (aligned cut, window, outcome,
  `narrower`), `app/api/changes.py` (recovery, telemetry: one `changes.compare` log line per
  comparison).
* `GET /api/v1/demo` — `comparisons`: the dataset's drill (`GeneseePressDataset.comparisons`).
* Frontend: `components/Coverage.tsx`, `components/Comparison.tsx`,
  `components/ComparisonLink.tsx`, `lib/coverage.ts`, `/risks`, `/access/investigate`,
  `/changes/compare`, `components/DemoGuide.tsx`.

The full account, with measurements, is
[the ADG-DEMO-03 report](reports/ADG-DEMO-03-report.md).
