r"""Which watch keys the API accepts, pinned against the change feed's own scoping.

A watch whose key the change feed cannot scope on is the quietest possible failure: it is
accepted by every other validator, saved, listed in the interface, and silent forever — and
before ``AlertService.evaluate_window`` guarded its per-watch loop, it also stopped every
*other* alert in the estate on each pass.

``_checked_key`` refuses it at creation by building the scope the detection pass will build
and letting it object, so what the API accepts cannot drift from what the feed can match.
These tests are what keep the **documentation** from drifting from either: the operations
reference names these exact shapes, and the share row in it was wrong until this file was
written — it named the ``share|`` prefixed *observation source key* rather than the stored
share key.

Hermetic: `_checked_key` touches no database.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.alerts import WatchKind
from app.api.alerts import _checked_key

ACCEPTED = [
    (WatchKind.RESOURCE, r"\\fs01\finance"),
    (WatchKind.RESOURCE, r"\\FS01\Finance\Payroll"),
    # The stored share key is `<server>|<share>`. The `share|` prefixed form is what an
    # observation carries as its source key, and it is *not* what the change feed selects on.
    (WatchKind.SHARE, "fs01|finance"),
    (WatchKind.GROUP, "principal|S-1-5-21-1-2-3-5001"),
    (WatchKind.GROUP, "S-1-5-21-1-2-3-5001"),
]

REFUSED = [
    # One separator rather than two: not a UNC path, and exactly what a JSON escape or a
    # shell heredoc makes of a correct one.
    (WatchKind.RESOURCE, r"\fs01\finance"),
    # A local path names nothing without saying which server.
    (WatchKind.RESOURCE, r"C:\finance"),
    (WatchKind.SHARE, "share|fs01|finance"),
    (WatchKind.SHARE, r"\\fs01\finance"),
    # A display name is metadata, never an identity (ADR-0001).
    (WatchKind.GROUP, "Finance-RW"),
]


@pytest.mark.parametrize(("kind", "key"), ACCEPTED, ids=lambda value: str(value))
def test_a_key_the_change_feed_can_scope_on_is_accepted(kind: WatchKind, key: str) -> None:
    assert _checked_key(kind, key) == key


@pytest.mark.parametrize(("kind", "key"), REFUSED, ids=lambda value: str(value))
def test_a_key_nothing_can_scope_is_refused_with_a_reason(kind: WatchKind, key: str) -> None:
    with pytest.raises(HTTPException) as error:
        _checked_key(kind, key)

    assert error.value.status_code == 422
    detail = str(error.value.detail)
    # The message says what is wrong *and* why it is refused rather than saved, because the
    # person reading it is about to retype the value. Compared against `repr(key)`, which is
    # what the message embeds and therefore what the reader actually sees -- a UNC path whose
    # separators were doubled by a repr is a different string from the one they typed, and
    # asserting the raw form would pass only for the keys that contain no backslash.
    assert repr(key) in detail
    assert "silent forever" in detail


def test_constructing_the_scope_alone_would_not_have_caught_it() -> None:
    """The regression that made the first version of the check useless.

    ``ChangeScope``'s own validation refuses only an empty key; the parse that rejects a
    malformed UNC path happens when the **predicates** are built. A check that constructed the
    scope and stopped there accepted every bad key above and failed on every later pass — and
    it passed its own test, because the test only asserted a 422 for the empty case.
    """
    from app.changes import ChangeScope
    from app.changes.scope import ScopeTarget, predicates_for

    # Accepted by the constructor...
    scope = ChangeScope(target=ScopeTarget.DIRECTORY_TREE, key=r"\fs01\finance")

    # ...and refused the moment anything tries to use it.
    with pytest.raises(Exception, match="UNC"):
        predicates_for(scope)
