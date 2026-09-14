# ADR-0012: Effective-access queries are bounded by inverting the question

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 4B (`phase-04/02-effective-access-resolver.md`)
- **Deciders:** Phase 4B implementation

## Context

Two of the three effective-access questions have an obvious implementation whose cost grows
with the size of the estate, and both look perfectly correct while doing it.

**"Who can reach this resource?"** The direct form evaluates every principal in the domain
against this ACL: N principals × M ACEs, with an upward membership traversal per principal.
On a real directory — tens of thousands of accounts — that is tens of thousands of bounded
traversals for one page of results.

**"What can this principal reach?"** The direct form evaluates every directory the estate
holds. Phase 3C measured a real tree scan; a mid-sized share is hundreds of thousands of
rows, and none of them can be excluded without evaluating it.

Phase 3C also established the standard this project holds reads to: statement counts are
asserted rather than timed, and no endpoint's count may grow with the size of its answer
(`tests/db/test_query_cost.py`). Two duplicated reads were removed there on that basis.
Phase 4B is where an N+1 is most expensive, because each extra row is not one query but
four — a DACL, a share ACL, a membership traversal, and a label lookup.

## Decision

**Neither listing iterates the population it is reporting on. Each starts from a small,
indexed set and expands outward once.**

1. **Resource → principals inverts the traversal.** A principal's rights on a resource
   depend on nothing except which of that resource's trustees it belongs to. So each ACL
   **trustee** is expanded *downward* once (`GraphService.effective_members`), and the map is
   inverted: every reached principal arrives with the trustee that reached it and the chain
   between them. Cost is one traversal per trustee — tens — rather than one per principal,
   and the result is exact, not sampled. Trustees are capped at
   `MAX_EXPANDED_TRUSTEES = 128`, reported as `enumeration.trustees_truncated`.
2. **Principal → resources starts from the reference index.** Candidates are
   `principal_references` rows for the token's own trustees, keyset-paged, **unioned with
   the `ntfs_resources` rows whose DACL is absent**. A NULL DACL names nobody and so appears
   in no reference row; omitting exactly the resources that are open to the whole estate
   would invert the finding this tool exists to produce. Migration `0006_effective_access`
   adds the partial index `ix_ntfs_resources_null_dacl` so that union is two indexed reads.
3. **Only the page is evaluated, and its inputs are read in bulk.**
   `ntfs_acls_for` / `share_acls_for` / `ntfs_resources_by_keys` / `shares_by_keys` each take
   the page's keys and issue one statement, so the count is flat in the page size.
4. **A projection walks no tree.** The ancestors of a UNC path are a pure function of the
   path, so every candidate key is known before a row is read and the whole chain costs one
   `ntfs_resources_by_keys` call. Capped at `MAX_ANCESTOR_LEVELS = 64`.
5. **A whole ACL is read for an evaluation, never a page of one.** `full_ntfs_acl` and
   `full_share_acl` fetch one row past `MAX_ACL_FETCH` so truncation is detectable rather
   than silent. Evaluating a page of a DACL would compute a Deny the next page cancels.
6. **Candidates that grant nothing are returned with their verdict, not filtered.** Filtering
   inside a page would make `has_more` a claim about a different set than the one being
   paged, and could return an empty page while results remained — and "named on the ACL and
   holding no access" is the distinction the engine exists to draw.
7. **The principal listings report no `total`.** Counting a union of two indexed reads would
   double the cost of every page to produce a number that counts candidates rather than
   grants.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Evaluate every principal against the ACL | Quadratic, and the shape the prompt for this phase forbids outright. |
| Evaluate every resource for the principal | Same, one table larger. |
| Materialize an effective-access table on ingest | A derived cache of a join over three independently collected layers, invalidated by any of them. ADR-0003 and ADR-0007 both push against storing what can be derived, and a stale access answer is worse than a slow one. |
| Cache resolved answers with a TTL | Correctness for an audit tool cannot be traded for latency on a value whose whole purpose is to be current. Measured cost did not justify it. |
| Sample or approximate the principal listing | An audit answer that is "mostly" the people with access is not an audit answer. |
| Filter zero-rights candidates inside the page | Breaks `has_more`, hides the listed-but-no-access finding, and can return an empty page with more results behind it. |
| Omit NULL-DACL resources from the principal listing | Omits precisely the resources open to the whole estate. |
| Sequential scan for NULL-DACL rows instead of an index | A seq scan of the largest table on every call, to find rows that should be rare. The partial index costs nothing when they are. |
| Expand trustees lazily, per page | The traversal would re-fan-out per page and the offset cursor would slice a set that changed between pages. |

## Consequences

**Positive**

- Every access endpoint's statement count is flat in the size of its answer, asserted rather
  than assumed.
- The inversion is exact. It is a restatement of the access check, not an approximation of
  it, so nothing is traded for the bound.
- Nothing derived is stored, so no access answer can go stale relative to the observations it
  was computed from.

**Negative / accepted costs**

- The resource → principals listing re-runs its traversals per page, so paging deep into a
  large result repeats work. Offset paging already carries that caveat, and the response
  reports its own truncation.
- A resource whose ACL names a very large number of distinct trustees is truncated at 128
  rather than answered slowly. Reported, not silent.
- One migration for one partial index, to make a union cheap that would otherwise be a table
  scan.

**Follow-up required**

- The trustee expansions for one resource are independent and are currently sequential. If a
  real estate shows it matters, they can be issued together without changing the shape.
- Phase 5's risk engine will want the same answer across many resources at once, which is a
  different query shape than any of these three.

## Compliance

- `tests/db/test_query_cost.py::test_an_access_listing_costs_the_same_whatever_the_page_holds`
  — one row and a hundred rows cost the same statements, on all three listings.
- `tests/db/test_query_cost.py::test_listing_who_can_reach_a_resource_does_not_read_per_member`
  — forty members cost a handful of reads of `membership_edges`, not forty.
- `tests/db/test_query_cost.py::test_resolving_one_principal_against_one_resource_is_a_constant_read`
  — each table read exactly once.
- `tests/db/test_access_api.py::TestResourcesNobodyIsNamedOn` — a NULL-DACL resource appears
  in the principal listing although no ACE names anybody.
- `tests/access_engine/test_ceilings.py` — the fetch ceiling and the evaluation ceiling are
  pinned equal.
