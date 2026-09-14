"""Resource path identity and normalization.

Two collectors can describe the same directory in a dozen ways:
``\\\\FS01\\Finance\\Reports``, ``\\\\fs01.corp.example.com\\finance\\reports\\``,
``\\\\?\\UNC\\FS01\\Finance\\Reports``, ``D:/Shares/Finance/Reports`` on the server itself.
If those become different rows, ADG will report that a directory has two different sets of
permissions. Path identity therefore has exactly one canonical form and one comparison key.

Two rules drive everything here:

* **Comparison is case-insensitive, display is case-preserving.** Windows file systems are
  case-insensitive by default, so ``comparison_key`` is case-folded while ``value`` keeps
  what was observed.
* **Normalization never guesses.** ``..`` cannot be resolved without touching the file
  system, and a host alias cannot be resolved without DNS, so both are rejected or
  preserved rather than silently rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.domain.errors import DomainValidationError

SEPARATOR: Final = "\\"
UNC_PREFIX: Final = "\\\\"
EXTENDED_PREFIXES: Final = ("\\\\?\\UNC\\", "\\\\.\\UNC\\", "\\\\?\\", "\\\\.\\")

# Characters Windows forbids in a path component, plus control characters.
_INVALID_COMPONENT_CHARS: Final = set('<>:"/\\|?*') | {chr(code) for code in range(0x00, 0x20)}
_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:$")


@dataclass(frozen=True, slots=True, eq=False)
class UncPath:
    """A UNC path: ``\\\\server\\share`` plus an optional relative path beneath it.

    ``server`` is kept exactly as observed (case-folded for comparison) because a host name
    and its DNS alias cannot be proven equivalent without resolving them; Phase 2 attaches
    observed host names to a server identity instead.
    """

    server: str
    share: str
    relative_path: str = ""

    def __post_init__(self) -> None:
        if not self.server:
            raise DomainValidationError("A UNC path must name a server.", field="server")
        if not self.share:
            raise DomainValidationError("A UNC path must name a share.", field="share")
        _validate_component(self.server, "server")
        _validate_component(self.share, "share")
        for segment in self.segments:
            _validate_component(segment, "path segment")

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(part for part in self.relative_path.split(SEPARATOR) if part)

    @property
    def value(self) -> str:
        """The canonical, display-ready UNC path."""
        base = f"{UNC_PREFIX}{self.server}{SEPARATOR}{self.share}"
        return f"{base}{SEPARATOR}{self.relative_path}" if self.relative_path else base

    @property
    def share_root(self) -> UncPath:
        return UncPath(server=self.server, share=self.share)

    @property
    def is_share_root(self) -> bool:
        return self.relative_path == ""

    @property
    def comparison_key(self) -> str:
        """The case-folded key used for equality, joins, and deduplication."""
        return self.value.casefold()

    @property
    def parent(self) -> UncPath | None:
        """The containing directory, or ``None`` at the share root."""
        if self.is_share_root:
            return None
        segments = self.segments[:-1]
        return UncPath(self.server, self.share, SEPARATOR.join(segments))

    def child(self, name: str) -> UncPath:
        _validate_component(name, "path segment")
        relative = f"{self.relative_path}{SEPARATOR}{name}" if self.relative_path else name
        return UncPath(self.server, self.share, relative)

    def __eq__(self, other: object) -> bool:
        r"""Case-insensitive, like the file systems these paths name.

        Equality follows ``comparison_key`` rather than the stored spelling: otherwise
        ``\\FS01\Finance`` and ``\\fs01\finance`` would be two directories, and their
        permissions would appear to differ.
        """
        if not isinstance(other, UncPath):
            return NotImplemented
        return self.comparison_key == other.comparison_key

    def __hash__(self) -> int:
        return hash(self.comparison_key)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, eq=False)
class LocalPath:
    """A drive-letter path as seen on the server itself, for example ``D:\\Shares\\Finance``.

    Share definitions point at local paths, so these are needed to tie a share to the
    directory tree an NTFS scan walks.
    """

    drive: str
    relative_path: str = ""

    def __post_init__(self) -> None:
        drive = self.drive.upper()
        if not _DRIVE_RE.match(drive):
            raise DomainValidationError(
                f"{self.drive!r} is not a drive specification such as 'C:'.",
                value=self.drive,
                field="drive",
            )
        object.__setattr__(self, "drive", drive)
        for segment in self.segments:
            _validate_component(segment, "path segment")

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(part for part in self.relative_path.split(SEPARATOR) if part)

    @property
    def value(self) -> str:
        return (
            f"{self.drive}{SEPARATOR}{self.relative_path}"
            if self.relative_path
            else f"{self.drive}{SEPARATOR}"
        )

    @property
    def comparison_key(self) -> str:
        return self.value.casefold()

    @property
    def is_root(self) -> bool:
        return self.relative_path == ""

    def to_unc(self, server: str) -> UncPath:
        """Express this path as an administrative-share UNC path (``\\\\FS01\\D$\\...``).

        Administrative shares are usually reachable only by administrators; this is a
        naming convenience, not a claim that the path is accessible that way.
        """
        return UncPath(server=server, share=f"{self.drive[0]}$", relative_path=self.relative_path)

    def __eq__(self, other: object) -> bool:
        """Case-insensitive, for the same reason as :class:`UncPath`."""
        if not isinstance(other, LocalPath):
            return NotImplemented
        return self.comparison_key == other.comparison_key

    def __hash__(self) -> int:
        return hash(self.comparison_key)

    def __str__(self) -> str:
        return self.value


def _validate_component(component: str, field_name: str) -> None:
    if not component:
        raise DomainValidationError(f"A {field_name} must not be empty.", field=field_name)
    if component in (".", ".."):
        raise DomainValidationError(
            f"A {field_name} must not be '.' or '..'; relative traversal cannot be "
            "resolved without touching the file system.",
            value=component,
            field=field_name,
        )
    invalid = sorted(_INVALID_COMPONENT_CHARS & set(component))
    if invalid:
        rendered = ", ".join(repr(char) for char in invalid)
        raise DomainValidationError(
            f"A {field_name} may not contain {rendered}; received {component!r}.",
            value=component,
            field=field_name,
        )


def _strip_extended_prefix(text: str) -> tuple[str, bool]:
    """Remove a ``\\\\?\\`` / ``\\\\.\\`` prefix. Returns the rest and whether it was UNC."""
    for prefix in EXTENDED_PREFIXES:
        if text.upper().startswith(prefix.upper()):
            remainder = text[len(prefix) :]
            is_unc = prefix.upper().endswith("UNC\\")
            return (UNC_PREFIX + remainder if is_unc else remainder), is_unc
    return text, False


def _normalize_separators(text: str) -> str:
    collapsed = text.replace("/", SEPARATOR)
    # Collapse runs of separators, preserving a leading UNC "\\".
    leading = UNC_PREFIX if collapsed.startswith(UNC_PREFIX) else ""
    body = collapsed[len(leading) :]
    while SEPARATOR * 2 in body:
        body = body.replace(SEPARATOR * 2, SEPARATOR)
    return leading + body


def _clean_segments(segments: list[str], *, original: str) -> list[str]:
    cleaned: list[str] = []
    for segment in segments:
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            raise DomainValidationError(
                f"{original!r} contains '..'. A resource path must be stated in resolved "
                "form; ADG will not guess what it refers to.",
                value=original,
                field="path",
            )
        cleaned.append(segment)
    return cleaned


def parse_unc_path(raw: str) -> UncPath:
    """Parse and canonicalize a UNC path.

    Accepts forward slashes, redundant separators, ``.`` segments, extended-length
    prefixes, and trailing separators.

    Raises:
        DomainValidationError: for anything that is not an absolute UNC path.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise DomainValidationError("A UNC path must be a non-empty string.", value=raw)

    text, _ = _strip_extended_prefix(_normalize_separators(raw.strip()))
    text = _normalize_separators(text)
    if not text.startswith(UNC_PREFIX):
        raise DomainValidationError(
            f"{raw!r} is not a UNC path. Expected \\\\server\\share[\\path].",
            value=raw,
            field="path",
        )

    parts = _clean_segments(text[len(UNC_PREFIX) :].split(SEPARATOR), original=raw)
    if len(parts) < 2:
        raise DomainValidationError(
            f"{raw!r} names no share. Expected \\\\server\\share[\\path].",
            value=raw,
            field="path",
        )

    server, share, *rest = parts
    return UncPath(server=server, share=share, relative_path=SEPARATOR.join(rest))


def parse_local_path(raw: str) -> LocalPath:
    """Parse and canonicalize a drive-letter path such as ``D:\\Shares\\Finance``."""
    if not isinstance(raw, str) or not raw.strip():
        raise DomainValidationError("A local path must be a non-empty string.", value=raw)

    text, was_unc = _strip_extended_prefix(_normalize_separators(raw.strip()))
    if was_unc:
        raise DomainValidationError(
            f"{raw!r} is a UNC path; use parse_unc_path.", value=raw, field="path"
        )
    text = _normalize_separators(text)

    drive, separator, remainder = text.partition(SEPARATOR)
    if not _DRIVE_RE.match(drive) or not separator:
        raise DomainValidationError(
            f"{raw!r} is not an absolute local path. Expected a drive-letter path such as "
            "C:\\data. Relative paths and device paths are not resource identities.",
            value=raw,
            field="path",
        )

    segments = _clean_segments(remainder.split(SEPARATOR), original=raw)
    return LocalPath(drive=drive, relative_path=SEPARATOR.join(segments))


def parse_windows_path(raw: str) -> UncPath | LocalPath:
    """Parse either a UNC or a drive-letter path, whichever the input is."""
    if not isinstance(raw, str) or not raw.strip():
        raise DomainValidationError("A path must be a non-empty string.", value=raw)

    normalized = _normalize_separators(raw.strip())
    stripped, was_unc = _strip_extended_prefix(normalized)
    if was_unc or _normalize_separators(stripped).startswith(UNC_PREFIX):
        return parse_unc_path(raw)
    return parse_local_path(raw)
