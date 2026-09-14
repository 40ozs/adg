# ADR-0008: A DACL has one normalized form, and its hash excludes the owner

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 3A — NTFS share-root ACL collection
- **Deciders:** ADG project

## Context

A file-system scan asks the same question of every directory it reaches: *is this DACL the
same one the parent handed down?* The answer decides whether the directory is an ACL
boundary, whether the scanner descends further, and whether anything changed since the last
run. In an estate with millions of directories and a few thousand distinct permission
decisions, it is the question that makes the scan affordable at all.

Comparing ACE lists pairwise answers it badly. Two readings of one descriptor legitimately
differ in the order entries come back, the spelling of a SID, the width of an integer, and
whether the source reported positions at all — none of which mean the permissions differ.
Worse, the comparison has to happen in three places that cannot share code: the collector
while walking (PowerShell), the server while ingesting (Python), and any later report.

A digest over a canonical form solves all three, and introduces exactly one hard question:
**what belongs inside it?** Three candidates were genuinely arguable.

* **Evaluation order.** Windows evaluates a DACL top to bottom. The same entries in a
  different order grant differently. But ADG had already decided that `order_index` is *not*
  part of an ACE's identity (ADR-0007's sibling rule, restated in
  `app.domain.access.ntfs_ace_identity_key`), because an administrator reordering an ACL must
  not look like every entry being deleted and recreated.
* **The owner.** The owner holds implicit `READ_CONTROL` and `WRITE_DAC` no matter what the
  DACL says. It is unquestionably part of who can reach the directory.
* **Whether the reading was ordered at all.** A source that reports positions and one that
  does not are describing the same ACL with different fidelity.

## Decision

**One normalized text document per DACL, versioned in its own first line, hashed with
SHA-256.** The format is specified in
[`docs/architecture/ntfs-acl-normalization.md`](../architecture/ntfs-acl-normalization.md).
Three rulings follow.

**Evaluation order is part of the hash — as a rank, not as a number.** A Deny moved below an
Allow grants access that was previously refused, and a digest that called those two ACLs
equal would hide a real change in the one place ADG looks for change. Entries are sorted by
their reported position and rendered with their *rank* in that sequence, so two collectors
that number a DACL differently still agree. This does not contradict the ACE identity rule:
an ACE keeps its identity when it moves, and the *ACL* is reported as changed. Those are
different objects and they should answer differently.

**The owner is excluded.** Every folder under a share root is owned by whoever created it
while sharing one identical inherited DACL. An owner-sensitive digest would mark every one of
those folders an ACL boundary — the exact opposite of what a boundary is for, and it would
make the scan's cost scale with the number of users rather than the number of decisions.
Ownership is recorded and reported beside the ACL, where it can be audited on its own terms.

**An unordered reading can never collide with an ordered one.** When any entry lacks a
position the document says `order=unordered` and sorts by content, so the two modes hash
differently even for identical entries. One reading knows the evaluation order and the other
does not; treating them as the same observation would claim knowledge nobody collected.

**The digest travels, and is checked rather than trusted.** The collector sends it on the
`ntfs_resource` observation (contract 1.2, additive). The server recomputes it from the ACEs
it holds and reports *both* — never one — with an `agrees` verdict; ingestion refuses a batch
whose two statements contradict each other. A collector that could not read a whole DACL
sends no digest at all, because a digest over part of one is indistinguishable from a digest
of all of it.

## Consequences

**Good.**

- "Which directories carry this exact DACL" becomes one indexed lookup, which is what turns
  thousands of boundaries into the few dozen permission decisions behind them.
- A boundary is detectable without reading the parent twice, and without shipping ACE lists
  between processes.
- Entries lost between a collector and the database become *visible*: the reported and
  computed digests disagree, and the declared and stored entry counts disagree with them.
- The normalized document is reproducible, so a disagreement can be explained rather than
  only reported. `NormalizedAcl` keeps the text for exactly that reason, and a 422 prints it.

**Costs, accepted.**

- Two implementations of one format, in two languages, that must agree byte for byte. This
  is mitigated the same way source keys are: a contract test runs the real collector and
  compares the two over a real DACL. It is not mitigated by hoping.
- The format is frozen. Any change to the bytes changes every digest ADG has stored, so it
  is a new version token, not an edit — `docs/architecture/ntfs-acl-normalization.md` §7
  lists what may not move.
- A digest cannot answer "why did this change". It says two ACLs differ; the stored ACEs say
  how. That is the right split, but it means the hash is never the whole answer.
- Excluding the owner means an ownership change is invisible to the hash. It is not
  invisible to ADG — `ntfs_resources.owner_sid` is stored and reported — but a consumer that
  watches only `acl_hash` will miss it, and Phase 7's change detection must watch both.

**Not decided here.** Whether a child's inherited entries can be *derived* from a parent's
inheritable ones, which would let a scan skip reading a descriptor entirely. That needs the
tree walk and the inheritance algebra, and it belongs with Phase 3B and Phase 4B.
