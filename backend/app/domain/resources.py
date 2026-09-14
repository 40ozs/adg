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
from app.domain.paths import LocalPath, UncPath


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


@dataclass(frozen=True, slots=True)
class DirectoryResource:
    """A directory whose NTFS security descriptor ADG has observed or intends to observe.

    ADG does not inventory every directory: it records the ones where permissions actually
    change. ``is_acl_boundary`` marks a directory whose DACL differs from its parent's —
    the Phase 3 scanner walks down only while inheritance holds, because an estate has
    millions of directories and only thousands of distinct permission decisions.
    """

    path: UncPath
    local_path: LocalPath | None = None
    share_key: str | None = None
    is_acl_boundary: bool = False
    inheritance_enabled: bool = True
    depth_from_share_root: int | None = None

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

    @property
    def identity_key(self) -> str:
        return self.path.comparison_key

    @property
    def is_share_root(self) -> bool:
        return self.path.is_share_root
