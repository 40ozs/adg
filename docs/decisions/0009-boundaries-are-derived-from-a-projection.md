# ADR-0009: An ACL boundary is derived from the parent's projection, and unknown is a boundary

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 3B — NTFS ACL-boundary scanner
- **Deciders:** ADG project

## Context

Phase 3A stored `is_acl_boundary` as a boolean nothing checked. It could afford to: the
collector read share roots only, and a share root is a boundary by fiat, because its parent
lies outside the share and there is nothing to compare it against. The Phase 3A handoff was
explicit that this was a placeholder — "`is_acl_boundary` for a non-root directory is
whatever the collector claims" — and named deriving it as the first prerequisite for this
phase.

A tree walk reaches directories that *do* have a readable parent, so the flag becomes a claim
that can be right or wrong. Three questions had to be answered before it could be made.

**What is a child compared against?** The obvious answer — the parent's own `acl_hash` — is
wrong, and wrong in the direction that destroys the report. Windows sets the `INHERITED` bit
on every entry it copies down, so a parent's explicit `0x03` entry reaches the child as
`0x13`. A perfectly inheriting child therefore has a different digest from its parent, and an
estate scanned that way reports every directory in it as a place where permissions changed.

**What happens when the answer cannot be established?** A scan root has no read parent. A
denied descriptor has no digest. A parent with a NULL DACL projects nothing, because what a
child of it holds comes from the creating process's default DACL rather than from the parent.
Each of those has to resolve to `true` or `false`, and the two mistakes are not symmetric.

**Who decides — the collector or the server?** The collector holds both descriptors while it
walks and can compare them for nothing. The server holds the estate and can compare against
whatever it last stored. Both have information the other lacks: the collector knows the
reading was simultaneous, the server knows what every previous run saw.

## Decision

**A resource is compared against its parent's *projection* onto a child of its kind.** The
projection is the DACL a freshly created, unprotected child would carry, computed from the
parent's entries by the NTFS propagation rules — implemented in
`app.domain.project_inherited_acl` and mirrored by `Get-AdgInheritedAceProjection`, and
specified in [`ntfs-acl-boundaries.md`](../architecture/ntfs-acl-boundaries.md). A directory
uses the container projection and a file the object projection, which is why contract 1.3
carries `resource_kind` rather than inferring it.

The propagation table was **measured on Windows, not recalled from documentation**: each row
by building a directory with one ACE and reading back what Windows gave a real child, a real
grandchild, and a real file. Three of its rows contradict the intuitive reading, and each
would have produced a whole class of false boundaries.

That discipline paid immediately. The first version of this projection mapped one parent ACE
to at most one child ACE, which is true only for an ACE whose mask carries no generic bits.
An ACE carrying one — `0xe0010000`, the generic form of Modify, which Explorer writes on
almost every directory it creates — is split by Windows into an **effective** copy (mapped
through the file-system generic mapping, inheritance flags cleared) and a **propagating**
copy (unmapped, `INHERIT_ONLY`). Missing that reported every directory on a real machine as a
boundary, and it passed every unit test in the repository, because every fixture mask
happened to be specific. It was caught by walking a real tree and is now measured on a live
volume on every run.

**Unknown is reported as a boundary.** Four of the seven `boundary_reason` values —
`share_root`, `scan_root`, `parent_unreadable`, `parent_null_dacl` — mean nobody established
anything, and all four set `is_acl_boundary`. The asymmetry is the point: a boundary that is
not really there costs one extra stored ACL, while a boundary reported *false* tells the next
scan it may stop looking and silently discards every permission change beneath it.

**The flag and the reason are inseparable.** `boundary_reason` is non-null exactly when
`is_acl_boundary` is true, and the collector derives the flag *from* the reason rather than
sending them as two fields that could disagree. `NULL` on a non-boundary is the only value
meaning "carrying exactly what it inherited".

**Both sides decide, and neither wins.** The collector reports its verdict with the parent
digest it judged against; the server recomputes the projection from the parent it holds and
reports its own. `GET /resources/{path}` returns `reported`, `computed`, and `agrees`, in the
same "report both, settle nothing" shape `acl_hash` already uses. `agrees` is `null` when
either side made no comparison — the server knowing more than the collector did is not the
collector having been wrong.

## Consequences

**The report becomes readable.** An estate of ten thousand directories with forty distinct
DACLs reports forty boundaries instead of ten thousand. `ix_ntfs_resources_boundaries` is a
partial index over exactly that minority.

**A disagreement is a finding rather than a failure.** `agrees: false` means the parent
changed between the two readings, the collector's projection is wrong, or entries were lost
in transit — three different problems, all visible, none resolved by fiat.
`parent_acl_hash_agrees` separates a *stale* verdict (the parent has been re-read since) from
a *wrong* one.

**A `CREATOR OWNER` grant produces a false boundary.** Windows substitutes the creating
principal at creation time, adding an entry the projection cannot predict from the parent
alone. Such a directory reports `acl_differs_from_parent`, which is a true statement about
its DACL and a misleading one about administrative intent.
`app.domain.SUBSTITUTED_TRUSTEES` names the SIDs so the cases stay tellable apart; nothing
guesses the creator, because that would be inventing a fact. `OWNER RIGHTS` (`S-1-3-4`) is
deliberately excluded from that set — it looks like a sibling and inherits like any ordinary
trustee, and treating it as one would have made every directory beneath it a boundary.

**Generic rights are resolved in exactly one place, and never stored.**
`app.domain.map_generic_rights` exists because the effective half of a split entry cannot be
predicted without it. That is not a retreat from ADR-0008's rule that masks are stored as
observed: the mapped value is a comparison value, never written to a row and never reported
on the wire.

**Boundary verification happens on the read side, not in the planner.** `plan_batch` holds
one batch, and a parent is routinely in a different one, so the claim is stored as sent and
checked where the parent's entries are in reach. This mirrors `acl_hash`, which the planner
*does* verify — because there the evidence arrives in the same batch by contract.

**Contract 1.3 is additive, and the requirement is version-gated.** A collector speaking
1.0 through 1.2 sets `is_acl_boundary` on a share root and has never heard of
`boundary_reason`; rejecting it would make the bump breaking. Such rows store a `NULL`
reason, which reads as "the collector that wrote this predates the field" — and are not
backfilled, because deriving a reason would write a verdict nobody made.

**Two things this does not become.** It is not effective access: no Deny precedence, no owner
implicit rights, no `CREATOR OWNER` substitution, no conditional ACEs — and the one generic
expansion it does perform produces a comparison value, never a stored or reported one. And it
is not a licence to skip subtrees: the walk reads every directory it reaches and reports the
boundaries it finds. Pruning below a non-boundary is a future optimization, and it would need
the reconciliation rules to change with it.
