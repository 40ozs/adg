"""The two bounds that have to agree, and nothing tells you when they stop.

The evaluator will read up to :data:`app.access_engine.MAX_ACL_ENTRIES` entries of a DACL
and reports truncation past that. The repository fetches up to
:data:`app.repositories.resources.MAX_ACL_FETCH` of them. They are stated twice on purpose —
the persistence layer keeps no dependency on the authorization engine — and a drift between
them would be silent in the worst possible way: the database would hand over fewer entries
than the evaluator was willing to evaluate, the evaluator would see a short list rather than
a truncated one, and the answer would be computed over part of a DACL with nothing saying so.
"""

from __future__ import annotations

from app.access_engine import MAX_ACL_ENTRIES
from app.repositories.resources import MAX_ACL_FETCH


def test_the_repository_fetches_exactly_what_the_evaluator_will_evaluate():
    assert MAX_ACL_FETCH == MAX_ACL_ENTRIES, (
        "The ACL fetch ceiling and the evaluation ceiling must be equal. A smaller fetch "
        "silently evaluates part of a DACL; a larger one reads rows nothing will look at."
    )


def test_the_ceiling_is_above_the_largest_dacl_windows_can_store():
    """A 64 KB ACL of minimum-size ACEs holds well under four thousand entries."""
    assert MAX_ACL_ENTRIES >= 4096
