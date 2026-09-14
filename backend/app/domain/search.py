"""Reading a search box.

One text field has to serve four different lookups — a SID, a UNC path, a share name, a
display name — and guessing wrong wastes a query or, worse, returns a confident answer to
the wrong question. So the guess is made once, here, as a pure function, and the API
*reports* the interpretation it chose alongside the results. A user who typed a path and
got name matches can see why.

Interpretation never narrows the search on its own: it orders the categories and supplies
the parsed value where one was recognized. Anything that parses as a SID is still worth
looking for as a name, because ``S-1-5-21-…`` is a perfectly legal (if unhelpful) display
name for an object somebody created badly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.identity import Sid
from app.domain.paths import UncPath, parse_unc_path

__all__ = ["MAX_SEARCH_TERM", "MIN_SEARCH_TERM", "QueryShape", "SearchQuery", "interpret_query"]

#: Two characters is the shortest prefix worth a LIKE across the estate. One character
#: matches most of it, which is not a search result, it is a table scan with a spinner.
MIN_SEARCH_TERM = 2
MAX_SEARCH_TERM = 512


class QueryShape(StrEnum):
    """What the typed text most likely is."""

    SID = "sid"
    UNC_PATH = "unc_path"
    SERVER = "server"
    SHARE = "share"
    NAME = "name"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A parsed search term, with whatever structure was recognized in it."""

    text: str
    shape: QueryShape
    sid: Sid | None = None
    unc_path: UncPath | None = None
    server: str | None = None
    share: str | None = None

    @property
    def explanation(self) -> str:
        """One sentence naming the interpretation, shown next to the results."""
        if self.shape is QueryShape.SID:
            return f"Read as a security identifier ({self.text})."
        if self.shape is QueryShape.UNC_PATH:
            return f"Read as a UNC path on server {self.server}."
        if self.shape is QueryShape.SERVER:
            return f"Read as a server name ({self.server})."
        if self.shape is QueryShape.SHARE:
            return f"Read as a share name ({self.share})."
        return "Read as a name. Matching is by prefix, case-insensitive."


class SearchTermError(ValueError):
    """The search term cannot be used. The message says what to type instead."""


def interpret_query(raw: str) -> SearchQuery:
    """Decide what the user meant, or explain why the term is unusable."""
    text = raw.strip()
    if len(text) < MIN_SEARCH_TERM:
        raise SearchTermError(
            f"A search term needs at least {MIN_SEARCH_TERM} characters. A shorter one "
            "matches most of the estate, which is not a search result."
        )
    if len(text) > MAX_SEARCH_TERM:
        raise SearchTermError(
            f"A search term is limited to {MAX_SEARCH_TERM} characters; this one is {len(text)}."
        )

    sid = Sid.try_parse(text)
    if sid is not None:
        return SearchQuery(text=text, shape=QueryShape.SID, sid=sid)

    if text.startswith("\\\\") or text.startswith("//"):
        return _unc_or_server(text)

    return SearchQuery(text=text, shape=QueryShape.NAME)


def _unc_or_server(text: str) -> SearchQuery:
    """``\\\\FS01``, ``\\\\FS01\\Finance`` and ``\\\\FS01\\Finance\\HR`` are three questions.

    A bare server is a server lookup; a server and share is a share lookup; anything deeper
    is a directory lookup. ``parse_unc_path`` requires at least a share, so the bare-server
    case is split off before parsing rather than by catching its rejection — a caught
    exception would also swallow a genuinely malformed path.
    """
    stripped = text.replace("/", "\\").lstrip("\\")
    segments = [segment for segment in stripped.split("\\") if segment]

    if len(segments) == 1:
        return SearchQuery(text=text, shape=QueryShape.SERVER, server=segments[0])

    try:
        path = parse_unc_path(text)
    except ValueError:
        # Not a usable path, but the text is still perfectly good as a name fragment;
        # refusing it outright would reject a paste that merely ends in a stray separator.
        return SearchQuery(text=text, shape=QueryShape.NAME)

    if path.is_share_root:
        return SearchQuery(
            text=text,
            shape=QueryShape.SHARE,
            unc_path=path,
            server=path.server,
            share=path.share,
        )
    return SearchQuery(
        text=text,
        shape=QueryShape.UNC_PATH,
        unc_path=path,
        server=path.server,
        share=path.share,
    )
