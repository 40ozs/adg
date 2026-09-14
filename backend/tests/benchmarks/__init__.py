"""Measured cost of the membership graph.

Not tests. The suite under `tests/` asserts that answers are correct; this measures what
they cost, which is a different question and one that cannot be asserted — a threshold that
passes on a developer's laptop and fails in CI teaches people to ignore it.

The numbers these produce are recorded, with the machine and the dataset size that produced
them, in `docs/architecture/ad-graph-validation.md`. Re-run them when the query path or the
schema changes and update that document; a benchmark nobody re-runs is a number that used
to be true.
"""
