"""Resources: servers, SMB shares, and directories.

A resource identity must survive renaming and re-sharing, and must not silently merge two
things that are different. Three rules follow from that:

* a **server** is identified by the name it was collected under, plus its computer SID when
  one is known — DNS aliases cannot be proven equivalent without resolution;
* a **share** is identified by (server, share name), case-insensitively, because SMB share
  names are case-insensitive and a share can be re-pointed at a different local path;
* a **directory** is identified by its canonical UNC path. The local path on the server is
  recorded too, since NTFS descriptors are read through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.identity import Sid
from app.domain.paths import LocalPath, UncPath, parse_unc_path


class ShareType(StrEnum):
    """SMB share type. Only ``DISK`` shares carry file-system permissions."""

    DISK = "disk"
    PRINT = "print"
    IPC = "ipc"
    DEVICE = "device"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Server:
    """A Windows computer that hosts shares.

    ``name`` is whatever the collector addressed the machine as; ``dns_host_name`` and
    ``computer_sid`` are recorded when known so that later phases can merge aliases on
    evidence rather than on a guess.
    """

    name: str
    dns_host_name: str | None = None
    netbios_name: str | None = None
    computer_sid: Sid | None = None
    domain_sid: Sid | None = None
    is_domain_member: bool | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise DomainValidationError("A server must have a name.", field="name")
        if any(char in self.name for char in "\\/"):
            raise DomainValidationError(
                f"A server name must not contain path separators; received {self.name!r}. "
                "Pass the host name alone, not a UNC path.",
                value=self.name,
                field="name",
            )
        if self.domain_sid is not None and not self.domain_sid.is_domain_sid:
            raise DomainValidationError(
                f"{self.domain_sid} is not a domain SID.", field="domain_sid"
            )

    @property
    def identity_key(self) -> str:
        """Case-folded host name. Local-group SIDs are scoped by this value."""
        return self.name.casefold()

    @property
    def local_domain_sid(self) -> Sid | None:
        """The machine's own SID namespace, which issues its local accounts and groups."""
        if self.computer_sid is None:
            return None
        return self.computer_sid.domain_sid


@dataclass(frozen=True, slots=True)
class SmbShare:
    """An SMB share exposed by a server.

    A share is a *publication* of a directory, not the directory itself: two shares can
    point at the same path with different share ACLs, and a share can be re-pointed without
    the directory changing. Keeping them as separate entities is what allows ADG to explain
    that access differs depending on which share a user goes through.
    """

    server_key: str
    name: str
    local_path: LocalPath | None = None
    share_type: ShareType = ShareType.DISK
    description: str | None = None
    concurrent_user_limit: int | None = None

    def __post_init__(self) -> None:
        if not self.server_key or not self.server_key.strip():
            raise DomainValidationError("A share must record its server.", field="server_key")
        if not self.name or not self.name.strip():
            raise DomainValidationError("A share must have a name.", field="name")
        if any(char in self.name for char in "\\/"):
            raise DomainValidationError(
                f"A share name must not contain path separators; received {self.name!r}.",
                value=self.name,
                field="name",
            )
        # A disk share without a recorded local path is acceptable: some sources report
        # share ACLs without the backing path, and a missing path is not a wrong path.

    @property
    def identity_key(self) -> str:
        return f"{self.server_key.casefold()}|{self.name.casefold()}"

    @property
    def unc_path(self) -> UncPath:
        return UncPath(server=self.server_key, share=self.name)

    @property
    def is_hidden(self) -> bool:
        """Administrative and hidden shares end in ``$`` (``C$``, ``ADMIN$``, ``IPC$``)."""
        return self.name.endswith("$")

    @property
    def is_administrative(self) -> bool:
        """Default administrative shares, reachable only by local administrators."""
        upper = self.name.upper()
        return upper in {"ADMIN$", "IPC$"} or (
            len(upper) == 2 and upper[0].isalpha() and upper[1] == "$"
        )

    @property
    def carries_file_permissions(self) -> bool:
        return self.share_type is ShareType.DISK


class ResourceKind(StrEnum):
    """Whether a file-system resource is a container or a leaf.

    The distinction is not cosmetic: it decides which inheritance projection applies. A
    directory receives its parent's ``CONTAINER_INHERIT`` entries and keeps propagating
    them; a file receives the ``OBJECT_INHERIT`` ones with every inheritance flag stripped,
    because it has nothing below it to pass them to (:mod:`app.domain.inheritance`).
    Comparing a file's DACL against the container projection would report a boundary on
    every file in the estate.

    ``DIRECTORY`` is the default, and is what every contract 1.2 payload meant: file
    scanning did not exist before 1.3.
    """

    DIRECTORY = "directory"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class DirectoryResource:
    """A file-system resource whose NTFS security descriptor ADG has observed.

    ADG does not inventory every directory: it records the ones where permissions actually
    change. ``is_acl_boundary`` marks a resource whose DACL differs from what its parent
    hands down — the Phase 3 scanner walks down only while inheritance holds, because an
    estate has millions of directories and only thousands of distinct permission decisions.

    The type is named for the only kind it could hold before contract 1.3 added opt-in file
    scanning. The name is kept rather than widened because ``identity_key`` is the stored
    key of every NTFS row ADG holds, and because the phase handoffs are a historical ledger
    that names this type. ``resource_kind`` is what actually says which kind of object a
    row describes.
    """

    path: UncPath
    local_path: LocalPath | None = None
    share_key: str | None = None
    is_acl_boundary: bool = False
    inheritance_enabled: bool = True
    depth_from_share_root: int | None = None
    resource_kind: ResourceKind = ResourceKind.DIRECTORY

    def __post_init__(self) -> None:
        if self.depth_from_share_root is not None and self.depth_from_share_root < 0:
            raise DomainValidationError(
                "depth_from_share_root must be non-negative.",
                value=self.depth_from_share_root,
                field="depth_from_share_root",
            )
        if self.is_acl_boundary is False and self.inheritance_enabled is False:
            raise DomainValidationError(
                "A directory that blocks inheritance is by definition an ACL boundary; "
                "recording otherwise would hide where permissions change.",
                field="is_acl_boundary",
            )
        # A share publishes a directory. Accepting a file here would attach a share's
        # NTFS-layer link to a leaf nothing can be a child of, and `share_root_acl` would
        # then answer with a file's ACL.
        if self.resource_kind is ResourceKind.FILE and self.path.is_share_root:
            raise DomainValidationError(
                f"{self.path.value} is a share root, which is always a directory; it "
                "cannot be reported as a file.",
                value=self.path.value,
                field="resource_kind",
            )

    @property
    def identity_key(self) -> str:
        return self.path.comparison_key

    @property
    def is_share_root(self) -> bool:
        return self.path.is_share_root


@dataclass(frozen=True, slots=True)
class ShareIdentity:
    r"""A share named from outside: the two parts that identify one ``\\server\share``.

    Callers name a share in whatever form is convenient — a UNC path from a ticket, a
    storage key copied out of an earlier response — and both have to land on exactly one
    row. This type is the single normalized result of that parsing, so no endpoint invents
    its own rule.
    """

    server: str
    name: str

    @property
    def identity_key(self) -> str:
        """Matches :attr:`SmbShare.identity_key`, which is the stored key."""
        return f"{self.server.casefold()}|{self.name.casefold()}"

    @property
    def unc_path(self) -> UncPath:
        return UncPath(server=self.server, share=self.name)

    def __str__(self) -> str:
        return self.unc_path.value


def parse_share_identifier(raw: str) -> ShareIdentity:
    r"""Normalize a share identifier, or reject it as ambiguous.

    Accepts the two forms a caller actually has to hand:

    * a storage key, ``fs01|finance``;
    * a UNC path, ``\\FS01\Finance`` — in any of the spellings
      :func:`app.domain.paths.parse_unc_path` canonicalizes (forward slashes, extended
      prefixes, a trailing separator).

    Everything else is refused rather than guessed at. Two refusals are load bearing:

    * ``\\FS01\Finance\Reports`` names a **directory**, not a share. Silently truncating it
      to the share would answer a question about the share ACL when the caller asked about
      a folder, and the two grant different access.
    * a bare ``FS01`` names no share, and a key with extra ``|`` segments names nothing at
      all. Picking an interpretation would return one share's ACL under another's name.

    Raises:
        DomainValidationError: if the identifier is empty, malformed, or ambiguous.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise DomainValidationError(
            "A share identifier must be a non-empty string.", value=raw, field="share"
        )

    text = raw.strip()
    if "|" in text:
        parts = text.split("|")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise DomainValidationError(
                f"{raw!r} is not a share key. Expected exactly 'server|share', or a UNC "
                "path such as \\\\FS01\\Finance.",
                value=raw,
                field="share",
            )
        server, name = (part.strip() for part in parts)
        if any(char in server + name for char in "\\/"):
            raise DomainValidationError(
                f"{raw!r} mixes a share key with a path. Send either 'server|share' or a "
                "UNC path, not both.",
                value=raw,
                field="share",
            )
        return ShareIdentity(server=server, name=name)

    path = parse_unc_path(text)
    if not path.is_share_root:
        raise DomainValidationError(
            f"{raw!r} names a directory inside a share, not the share itself. A share and "
            "a folder beneath it have different ACLs; ask for "
            f"{path.share_root.value} to get the share.",
            value=raw,
            field="share",
        )
    return ShareIdentity(server=path.server, name=path.share)
