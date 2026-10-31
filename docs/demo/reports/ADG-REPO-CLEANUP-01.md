# ADG-REPO-CLEANUP-01 — repository and disk cleanup after the Wave D promotion

## 1. Verdict

**`ADG_REPO_CLEANUP_COMPLETE_WITH_LIMITATIONS`**

Twenty stale ADG worktrees are gone (about 31.7 GiB back on the T5). The 1.2 GB Wave D scratch
directory is gone. Seventeen proven-merged or superseded branches are deleted, locally and on
`origin`. Open PR #4 and every branch with work that is not on `main` are untouched. No tracked
file outside this report and its evidence changed. Nothing was deployed. Staging and production
were not touched.

Limitations (§12): this report's docs branch remains open for the owner, 19 merged remote branches
outside Wave A–D are left for a later lane, and the internal-disk "after" figure is confounded by
other lanes.

## 2. Authority

| | |
|---|---|
| `origin/main` at start and at end | **`c4d6a679b34465176af128f589aa13b8845d270a`** (did not move during the lane) |
| What it is | Docs record on the Wave D merge `e81272d` (`ADG_WAVE_D_PROMOTED_TO_MAIN_WITH_LIMITATIONS`) |
| `main` CI | Merge [41239013815](https://github.com/40ozs/adg/actions/runs/41239013815) success; docs follow-up green |
| Open PRs | Only **#4** `ci/ADG-CI-SPEEDUP-01` @ `3aae395`, open, all checks success. Re-queried right before the remote deletions ([open-prs.txt](ADG-REPO-CLEANUP-01-evidence/open-prs.txt)) |

Wave D durable record on `main` (tree of `c4d6a67`):
`ADG-UX-WAVE-D-INTEGRATION-01.md` + evidence, `ADG-WAVE-D-STAGING-RELEASE-01.md` + evidence,
`ADG-WAVE-D-PROMOTION-01.md` + evidence.

## 3. Method

Everything was discovered and proven before anything was deleted. Tree diffs and blob identity
were used, not `git branch --merged` alone.

1. **Ancestry.** `git merge-base --is-ancestor <ref> origin/main` for every local and remote ref.
2. **Reports.** For each ancestor branch, every path under `docs/demo/reports/` at its tip exists
   in `main`'s tree: 0 missing for all 16 Wave A–D branches.
3. **Non-ancestor branches.** Every file the branch changes was compared blob by blob with `main`.
4. **Worktrees.** `git status --porcelain`, stash list, and the ignored-file listing (minus
   `node_modules`, `.venv`, `.next` and caches) were checked for every worktree. All 20 had
   0 tracked changes, 0 stashes and no untracked files apart from ignored `._*`/`.DS_Store`.
   Ten worktrees' `frontend/node_modules` were symlinks into Wave A's, so Wave A was removed last.
5. **Processes.** `lsof` found five leftover processes of the promotion lane: two `stack.sh`
   shells, plus `next-server` on :3110, :3111 and :3112. Their working directory was inside the
   PROMOTION-01 and Wave C worktrees. They were stopped by PID after their cwd was re-checked.
   No other process was touched.

## 4. Worktree inventory (before)

Sorted by size. Each row shows HEAD, whether it is an ancestor of `main`, and whether the local
branch equals its remote. None of these worktrees was dirty, and none was attached to an open PR.

| Size | Worktree | Branch | HEAD | Main ancestor? | Remote = local? | Action |
|---|---|---|---|---|---|---|
| 5.7 GB | `mmm/adg` | `main` | `c4d6a67` | = main | yes | **KEEP** (canonical) |
| 5.2 GB | `adg-WAVE-D-PROMOTION-01` | `integration/ADG-WAVE-D-PROMOTION-01` | `d82d477` | yes | yes | removed |
| 4.7 GB | `adg-UX-WAVE-A-01` | `integration/ADG-UX-WAVE-A-01` | `fcb7928` | yes | yes | removed (last) |
| 4.6 GB | `adg-UX-PERF-01` | `feature/ADG-UX-PERF-01-progressive-overview` | `9a7a2a5` | yes | yes | removed |
| 4.3 GB | `adg-UX-TITLES-03` | `feature/ADG-UX-TITLES-03-document-titles` | `76e3c46` | yes | yes | removed |
| 4.3 GB | `adg-UX-01` | `feature/ADG-UX-01-information-architecture` | `6ded5ed` | yes | yes | removed |
| 4.3 GB | `adg-UX-06` | `feature/ADG-UX-06-operational-overview` | `d9f7a3b` | yes | yes | removed |
| 717 MB | `adg-UX-WAVE-D-01` | `integration/ADG-UX-WAVE-D-01` | `bb370f8` | yes | yes | removed |
| 670 MB | `adg-UX-NAV-07` | `feature/ADG-UX-NAV-07-review-item-client-navigation` | `70d08e7` | yes | yes | removed |
| 608 MB | `adg-UX-REVIEW-NAMES-08` | `feature/ADG-UX-REVIEW-NAMES-08-scope-display-name` | `6877039` | yes | yes | removed |
| 550 MB | `adg-UX-TEST-STABILITY-09` | `test/ADG-UX-TEST-STABILITY-09-deterministic-titles` | `9c67d2c` | yes | yes | removed |
| 489 MB | `adg-UX-BACKEND-COPY-04` | `feature/ADG-UX-BACKEND-COPY-04-identity-wording` | `9cec0b0` | yes | yes | removed |
| 485 MB | `adg-UX-WAVE-C-01` | `integration/ADG-UX-WAVE-C-01` | `559dbaf` | yes | yes | removed |
| 458 MB | `adg-UX-RECENT-05B` | `feature/ADG-UX-RECENT-05B-recently-viewed` | `44d1a3c` | yes | yes | removed |
| 457 MB | `adg-UX-WAVE-B-01` | `integration/ADG-UX-WAVE-B-01` | `10c73d1` | yes | yes | removed |
| 435 MB | `adg-UX-DEEP-TITLES-06` | `feature/ADG-UX-DEEP-TITLES-06-contextual-titles` | `c226516` | yes | yes | removed |
| 416 MB | `adg-UX-ENTITY-02` | `feature/ADG-UX-ENTITY-02-identity-context` | `a0a54de` | yes | yes | removed |
| 376 MB | `adg-WAVE-D-STAGING-RELEASE-01` | `docs/ADG-WAVE-D-STAGING-RELEASE-01` | `d60168f` | no (superseded) | yes | removed |
| 358 MB | `adg-HOSTED-STREAMING-10` | `docs/ADG-HOSTED-STREAMING-10` | `dc3fd52` | **no, unique** | yes | removed, **branch kept** |
| 244 MB | `adg-HOSTED-STREAMING-11` | `docs/ADG-HOSTED-STREAMING-11` | `77b4400` | **no, unique** | yes | removed, **branch kept** |
| 221 MB | `adg-UX-RECENT-05A` | `design/ADG-UX-RECENT-05A-recently-viewed-architecture` | `5a4c27b` | **no, unique** | yes | removed, **branch kept** |

The three "unique" branches hold docs that are not on `main`. Their worktrees were clean, had no
ignored files of their own, and matched their remote, so removing the worktrees lost nothing. The
branches stay, both locally and on `origin`.

PR #4 never had a local worktree or local branch on this machine, so there was nothing local to
protect beyond the remote branch.

## 5. Branch classification

The full table of all 83 refs (25 local, 58 remote) is in
[branch-classification.txt](ADG-REPO-CLEANUP-01-evidence/branch-classification.txt).

| Category | Count (refs) | Action |
|---|---|---|
| **B MERGED**: the 16 Wave A–D branches (local + remote) | 32 | deleted |
| **C SUPERSEDED**: `docs/ADG-WAVE-D-STAGING-RELEASE-01` (local + remote) | 2 | deleted |
| **A ACTIVE**: open PR, unique commits, deploy branches, old UI line | 30 | kept |
| **B MERGED, outside Wave A–D**: claude/\*, SYNTH-\*, governance (remote) | 19 | kept, deferred |
| **D UNKNOWN** | 0 | — |

### 5.1 Superseded proof — `docs/ADG-WAVE-D-STAGING-RELEASE-01`

The branch was `d60168f`, two commits (`80c515f`, `d60168f`) on top of Wave D `bb370f8`. `main`
carries the same content as cherry-picks `7578524` and `1c53444`. All **36/36** files the branch
changes have the **same blob id** on `main` (byte-identical). The branch changes no path outside
`docs/`. It was deleted with `git branch -D`, the lane's only forced local deletion, and the
proof was re-run inline right before it.

### 5.2 Wave D integration and promotion branches

- `integration/ADG-UX-WAVE-D-01` (`bb370f8`) and `integration/ADG-WAVE-D-PROMOTION-01`
  (`d82d477`) are both ancestors of `main`.
- The Wave D product changes, the integration report and evidence, the staging-release report
  and evidence, and the promotion report are all in `main`'s tree. Post-merge CI is green.
- Neither branch is referenced by an open PR. Both were retired, locally and on `origin`.

### 5.3 Wave C and earlier

Wave A/B/C integration and their feature branches are ancestors of `main`, and every one of their
report files is in `main`'s tree. The SHAs that later reports cite (for example Wave C `559dbaf`)
stay reachable through `main`'s history.

## 6. Deletion ledger

[removed-items.txt](ADG-REPO-CLEANUP-01-evidence/removed-items.txt) records each item's final
SHA, its size and the command used.

- **Worktrees (20):** all of §4 except `mmm/adg`. Each was removed with plain
  `git worktree remove` (no `--force` needed), then `git worktree prune`. `git worktree list` now
  shows only the canonical checkout.
- **Local branches (17):** 16 with `git branch -d` (each passed the `--is-ancestor` check first),
  plus 1 with `-D` (§5.1).
- **Remote branches (17):** the same 17 names. Each was deleted with
  `git push --force-with-lease=<branch>:<verified sha> origin :<branch>`, after the open-PR head
  and base names were re-queried. Remote branches went from 58 to 41.
- **Processes (5):** the leftover promotion-lane stack (§3.5).
- **Scratch (1):** `~/Code/scratch-adg-wave-d` (§8).

## 7. Preservation ledger

See [retained-items.txt](ADG-REPO-CLEANUP-01-evidence/retained-items.txt).

- **Canonical checkout** `/Volumes/T5 EVO/mmm/adg` on `main` = `origin/main` `c4d6a67`. Its working
  tree is clean, and its `.env` and `frontend/.dev.vars` are unchanged (local, no secrets).
- **PR #4** `origin/ci/ADG-CI-SPEEDUP-01` @ `3aae395`: **PRESERVED**.
- **Unique docs branches**, local + remote: `docs/ADG-HOSTED-STREAMING-10`,
  `docs/ADG-HOSTED-STREAMING-11` and `design/ADG-UX-RECENT-05A-recently-viewed-architecture`.
- **Deploy branches** (`deploy/*`), the old UI line (`release/ADG-UI-EDGE-DIRECTORY-02` =
  `claude/adoring-cannon-7gh2pf`, `fix/ADG-UI-UPDATE-01`), `feature/CLOUDFLARE-DEMO-CACHE-01`,
  `feature/ADG-GRAPH-*`, and every other remote with commits not on `main`.
- **Tags:** the repository has none, and none were touched. No history was rewritten and no
  aggressive gc was run.
- **Ignored evidence logs** that existed only in removed worktrees were copied byte-for-byte to
  the same ignored paths in the canonical checkout. `*.log` is gitignored, so they are not
  committed:
  - `ADG-UX-06-evidence/overview-gate.log`: 43/43. The report cites `overview-gate.json`.
  - `ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-checks/smoke.log`: 23/23, all `ok: true`.
  - `ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-checks/browser.log`: 22/22, all `ok: true`.
    The staging report on `main` links this path, which was never committed, so the link now
    resolves only in the canonical checkout. The results themselves are stated in the report.

## 8. Scratch disposition — `~/Code/scratch-adg-wave-d`

| Content | Size | Already durable on `main`? |
|---|---|---|
| `pgdata/` (stopped pgserver PG 16 cluster: fresh `adg_demo` reset + fixtures) | 1.2 GB | Rebuildable. Recipe in `ADG-UX-WAVE-D-INTEGRATION-01.md` §16 and §25 (`timezone=UTC`, `demo_mode reset`, `setup-fixtures.sh` on `main`) |
| `c04.mjs` | 16 KB | Yes: blob `217009c` is in `main`'s history (`6fbb7a1`, `e2fd131`). The Wave D adaptation is `copy-check.wave-d.mjs` |
| `gate.sh`, `start-web.sh` | <1 KB | Thin wrappers. Their wiring is described in the Wave D and promotion reports |
| `logs/unit.log` | 8 KB | Yes: "6164 passed" is in `…-WAVE-D-INTEGRATION-01-evidence/backend-gates.txt` |
| `logs/ci-watch.log` | 48 KB | Yes: run 41223498140 is recorded in the Wave D and promotion reports |
| `logs/build.log`, `api.log`, `db.log`, `pg.log`, `web*.log`, `initdb.log`, `demo-reset.log` | <100 KB | Transient stack logs. The outcomes are in the committed gate evidence |

No unique project evidence, credentials or source changes were in it. There was no memory-index
backup in this directory. The directory was deleted, reclaiming **1.17 GiB** on the internal disk.

The macOS `._*` / AppleDouble collection incident is already recorded in
`ADG-WAVE-D-PROMOTION-01.md` (§11, `backend-gates.attempt1-exfat-sidecars.txt`). No scratch log
was needed for it.

## 9. Disk

From [disk-before-after.txt](ADG-REPO-CLEANUP-01-evidence/disk-before-after.txt):

| Volume | Free before | Free after | Change |
|---|---|---|---|
| `/Volumes/T5 EVO` (exFAT, all ADG worktrees) | 5,578,693,888 KB | 5,611,980,160 KB | **+31.74 GiB** (the removed worktrees measured 33.66 GiB by `du`) |
| Internal `/` | 27,334,120 KB | 26,069,060 KB | Scratch deletion freed **+1.19 GiB** when it ran (27,336,732 → 28,583,396 KB). Other lanes on this Mac kept writing to `/` afterwards |

## 10. Scope guards

- **Tracked files changed:** only this report and `ADG-REPO-CLEANUP-01-evidence/`.
  **Product changes: NONE.**
- **Other repositories:** none touched. The ODA entries in `mmm/worktrees/` were left alone, and
  no global Git objects were touched.
- **Staging:** `genesee-press-2.1.0-bb370f8e4531` **UNCHANGED**. No Worker, Render or database
  action was taken.
- **Production:** **NOT DEPLOYED**.
- **Wave E:** no branches were created.

## 11. Final canonical checkout

`/Volumes/T5 EVO/mmm/adg`, branch `main` tracking `origin/main` @ `c4d6a67`, clean.

**FUTURE ADG PRODUCT/UX WORK MUST BRANCH FROM CURRENT VERIFIED `origin/main`.**

## 12. Limitations

1. **The report branch remains.** `docs/ADG-REPO-CLEANUP-01` is pushed for owner review and is not
   merged to `main`. Delete it after it merges.
2. **Remote branches outside Wave A–D were left alone.** The 19 merged remotes outside Wave A–D
   (category B, deferred) are left for a later lane. So are the 30 category-A refs, which have
   commits not on `main` and provenance outside this lane.
3. **Internal-disk "after" is not ADG-only.** See §9.
4. **One report link resolves only locally.** `browser.log` (§7) is in the canonical checkout,
   not in Git.

## 13. Next step

1. Review and gate PR #4 (`ADG-CI-SPEEDUP-01`).
2. Then select the Wave E product work.
