"""Shared contract primitives: version, source, scope, and the observation base.

These pydantic models mirror `docs/contracts/v1/common.schema.json`. The JSON Schema is the
published contract a PowerShell collector author reads; these models are what the API
validates against. A parity test keeps them from drifting.

Enums are imported from `app.domain` rather than redeclared, so a contract value can never
mean something the domain does not.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain import (
    ACL_HASH_ALGORITHM,
    ACL_HASH_LENGTH,
    MAX_CHECKPOINT_TOKEN_LENGTH,
    AceSource,
    AceType,
    AclBoundaryReason,
    Checkpoint,
    CheckpointKind,
    CollectorKind,
    DomainValidationError,
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    ObservationSource,
    PrincipalKind,
    ResourceKind,
    SharePermission,
    ShareType,
    Sid,
    UnresolvedReason,
)

SCHEMA_VERSION: Final = "1.4"
"""Current contract version. Minor bumps are additive; a breaking change means v2.

Only a default for payloads this codebase constructs. Every ``1.x`` is accepted on the
wire, which is what makes an additive bump additive: a collector still sending ``1.0`` is
correct, it simply omits the fields later minors added.
"""

SCHEMA_VERSION_PATTERN: Final = re.compile(r"^1\.([0-9]+)$")


def schema_minor(value: str) -> int:
    """The minor number of a ``1.x`` version string, or ``0`` when it is not one.

    A rule a later minor introduces must apply only to payloads claiming that minor, or the
    bump is not additive: a collector still sending ``1.0`` is correct, and holding it to a
    field it has never heard of would reject it for being old rather than for being wrong.
    ``0`` for an unparsable value errs in the same direction, and the version validator has
    already rejected such a payload by the time any rule looks at it.
    """
    match = SCHEMA_VERSION_PATTERN.match(value.strip())
    return int(match.group(1)) if match else 0


MAX_BATCH_OBSERVATIONS: Final = 1000
"""An oversized batch is rejected, never truncated: silent truncation looks like coverage."""

MAX_BATCH_AFFIRMATIONS: Final = 5000
"""Affirmations get their own, higher ceiling (contract 1.4).

An affirmation is a key and a digest, not an object: a thousand of them cost less to parse
and to apply than a hundred full NTFS resources with their entries. Capping them at the
observation ceiling would force a collector re-reading a large, quiet tree to open batches
for no reason other than an accounting rule, and every extra batch is another round trip
that can fail. The ceiling still exists, because a batch has to remain something a server
can reject whole.
"""

MAX_ACCESS_MASK: Final = 0xFFFFFFFF
MAX_ACE_FLAGS: Final = 0xFF

__all__ = [
    "ACL_HASH_ALGORITHM",
    "ACL_HASH_LENGTH",
    "MAX_ACCESS_MASK",
    "MAX_ACE_FLAGS",
    "MAX_BATCH_AFFIRMATIONS",
    "MAX_BATCH_OBSERVATIONS",
    "MAX_HOST_NAME_LENGTH",
    "SCHEMA_VERSION",
    "AceSource",
    "AceType",
    "AclBoundaryReason",
    "Affirmation",
    "CheckpointKind",
    "CollectorCheckpoint",
    "CollectorKind",
    "ContractModel",
    "GroupScope",
    "GroupType",
    "MembershipEdgeKind",
    "ObservationBase",
    "ObservationKind",
    "PrincipalKind",
    "ResourceKind",
    "Scope",
    "ScopeKind",
    "SharePermission",
    "ShareType",
    "SourceDescriptor",
    "UnresolvedReason",
    "canonical_sid",
    "normalize_host",
    "schema_minor",
    "to_utc",
]


class ContractModel(BaseModel):
    """Base for every contract model.

    ``extra="forbid"`` matters more than it looks: a collector that misspells a field would
    otherwise have that field silently ignored, and the resulting observation would be
    quietly incomplete rather than loudly wrong.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def canonical_sid(value: str) -> str:
    """Validate and canonicalize a string-form SID, or raise with an actionable message."""
    try:
        return Sid(value).value
    except DomainValidationError as exc:
        raise ValueError(str(exc)) from exc


MAX_HOST_NAME_LENGTH: Final = 255
"""Matches ``hostName`` in `common.schema.json`. A host name is part of a storage key."""


def normalize_host(value: str) -> str:
    """Validate a bare host name against the published ``hostName`` definition.

    Every host-like field on every observation funnels through here, so this is the one
    place the published constraints have to hold. They are enforced rather than documented
    because a host name is not decoration: it is half of the storage key of every local
    group (``host|sid``), so an unbounded or unprintable one becomes an unbounded or
    unprintable identity that later shows up in an API response and an operator's report.
    """
    text = value.strip()
    if not text:
        raise ValueError("A host name must not be empty.")
    if any(char in text for char in "\\/"):
        raise ValueError(
            f"A host name must not contain path separators; received {value!r}. "
            "Send the host name alone, not a UNC path."
        )
    if any(char < " " for char in text):
        raise ValueError(
            f"A host name must not contain control characters; received {value!r}. "
            "The published contract (common.schema.json#/$defs/hostName) forbids "
            "U+0000-U+001F, and a host name is part of a stored identity key."
        )
    if len(text) > MAX_HOST_NAME_LENGTH:
        raise ValueError(
            f"A host name may be at most {MAX_HOST_NAME_LENGTH} characters; received "
            f"{len(text)}. The published contract "
            "(common.schema.json#/$defs/hostName) sets that bound, and a longer name "
            "would be rejected later as an oversized source_key with no hint of which "
            "field caused it."
        )
    return text


def to_utc(value: dt.datetime) -> dt.datetime:
    """Normalize an aware timestamp to UTC.

    Naive timestamps never reach here: the field type is ``AwareDatetime``, so pydantic
    rejects them before this runs.
    """
    return value.astimezone(dt.UTC)


SidField = Annotated[str, Field(description="String-form SID; canonicalized on ingest.")]
HostField = Annotated[str, Field(min_length=1, max_length=MAX_HOST_NAME_LENGTH)]


class ObservationKind(StrEnum):
    """Discriminator identifying which fact an observation carries."""

    PRINCIPAL = "principal"
    MEMBERSHIP_EDGE = "membership_edge"
    SERVER = "server"
    SMB_SHARE = "smb_share"
    SMB_ACE = "smb_ace"
    NTFS_RESOURCE = "ntfs_resource"
    NTFS_ACE = "ntfs_ace"


class ScopeKind(StrEnum):
    """What a run claims to have enumerated completely."""

    DOMAIN = "domain"
    SERVER = "server"
    SHARE = "share"
    DIRECTORY_TREE = "directory_tree"
    LOCAL_GROUPS_HOST = "local_groups_host"


class Scope(ContractModel):
    """A reconciliation scope: the boundary inside which absence may be inferred."""

    kind: ScopeKind
    key: str = Field(min_length=1, max_length=512)

    @field_validator("key")
    @classmethod
    def _casefold_key(cls, value: str) -> str:
        # Scope keys are compared, never displayed: host names, share keys, and UNC paths
        # are all case-insensitive on Windows.
        return value.casefold()


class SourceDescriptor(ContractModel):
    """Who observed a fact and how. Mirrors :class:`app.domain.ObservationSource`."""

    collector: CollectorKind
    collector_host: HostField
    method: str = Field(min_length=1, max_length=200)
    collector_version: str | None = Field(default=None, max_length=50)
    target: str | None = Field(default=None, max_length=1024)

    @field_validator("collector_host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        return normalize_host(value)

    def to_domain(self) -> ObservationSource:
        return ObservationSource(
            collector=self.collector,
            collector_host=self.collector_host,
            method=self.method,
            collector_version=self.collector_version,
            target=self.target,
        )


class CollectorCheckpoint(ContractModel):
    """How far a delta run got, and who issued the cursor (contract 1.4).

    The issuer is not optional decoration. ``uSNChanged`` is a counter local to one domain
    controller, so DC1's watermark replayed against DC2 skips every object whose USN on DC2
    happens to fall below it, and a DC restored from backup reissues numbers it has already
    handed out. The server compares a new checkpoint against the stored one only when both
    name the same issuer, and refuses it otherwise rather than storing a number it cannot
    show to be ahead.
    """

    kind: CheckpointKind
    token: str = Field(
        min_length=1,
        max_length=MAX_CHECKPOINT_TOKEN_LENGTH,
        description=(
            "The cursor. A decimal integer for 'usn', an RFC 3339 timestamp with an offset "
            "for 'timestamp', anything the collector likes for 'opaque'."
        ),
    )
    issuer: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Whatever the cursor is local to. For a domain controller, its dsServiceName "
            "and invocationId joined: the first changes when the collector binds a "
            "different DC, the second when the same DC is restored from backup."
        ),
    )
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def _validate(self) -> Self:
        object.__setattr__(self, "issued_at", to_utc(self.issued_at))
        # Constructing the domain value is the validation: its rules are the ones the
        # server enforces later, so a token this endpoint accepts is one the checkpoint
        # store can compare. Duplicating them here would let the two drift.
        try:
            self.to_domain()
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_domain(self) -> Checkpoint:
        return Checkpoint(
            kind=self.kind,
            token=self.token,
            issuer=self.issuer,
            issued_at=to_utc(self.issued_at),
        )


class Affirmation(ContractModel):
    """An object the collector re-read and found unchanged (contract 1.4).

    An affirmation is a full observation's equal in what it *claims* — this object exists,
    in this state, as of ``observed_at`` — and a fraction of its size. It exists because the
    file system has no change metadata a DACL edit reliably touches: writing an ACL does
    not move ``LastWriteTime``, so a scan that skipped unchanged directories on a timestamp
    would skip exactly the changes ADG is for. The collector therefore still reads every
    descriptor; what it sends for the unchanged ones is this.

    Two rules make it safe, and neither is a matter of trusting the collector:

    * **The digest is recomputed, not believed.** The server compares ``digest`` against the
      value it holds for that object and *refuses* the affirmation when they differ, naming
      it in the response so the collector re-sends the object in full. A collector that
      affirms a stale state cannot make the server keep one.
    * **The digest must be computed from this scan's reading**, never copied out of the
      collector's own cache of what it sent last time. A cached digest would make an
      affirmation a statement about the collector's memory instead of about the object, and
      the two diverge precisely when an ACL has changed.

    An affirmed object counts as observed: the run has seen it, so it extends the object's
    timeline and a run that affirmed everything it did not re-send has still enumerated its
    whole scope and may reconcile it.
    """

    kind: Literal[ObservationKind.NTFS_RESOURCE] = Field(
        description=(
            "Only 'ntfs_resource' may be affirmed in contract 1.4. It is the one kind with "
            "a published whole-object digest (acl_hash, contract 1.2) that the server "
            "already recomputes from what it stores."
        )
    )
    source_key: str = Field(min_length=1, max_length=512)
    digest: str = Field(
        min_length=1,
        max_length=ACL_HASH_LENGTH,
        description=(
            "The kind's content digest as this scan read it. For 'ntfs_resource' it is "
            "exactly the acl_hash of contract 1.2 — over the whole DACL, in the reading "
            "being affirmed, including dacl_present and dacl_protected."
        ),
    )
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def _validate(self) -> Self:
        object.__setattr__(self, "observed_at", to_utc(self.observed_at))
        digest = self.digest.strip().lower()
        if len(digest) != ACL_HASH_LENGTH or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(
                f"An affirmation's digest must be {ACL_HASH_LENGTH} lower-case hexadecimal "
                f"characters ({ACL_HASH_ALGORITHM}); received {self.digest!r}. The server "
                "compares it against the digest it holds, and a value that cannot be one "
                "would be refused as a mismatch with no way to tell a wrong digest from a "
                "changed object."
            )
        object.__setattr__(self, "digest", digest)
        return self


class ObservationBase(ContractModel):
    """Fields present on every observation.

    ``source_key`` is supplied by the collector and re-derived by the server; subclasses
    implement :meth:`derive_source_key` and the check runs on every observation.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)
    kind: str
    run_id: str
    observed_at: AwareDatetime
    source_key: str = Field(min_length=1, max_length=512)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        if not SCHEMA_VERSION_PATTERN.match(value):
            raise ValueError(
                f"Unsupported schema_version {value!r}. This endpoint speaks contract v1 "
                f"(current {SCHEMA_VERSION}); a v2 payload must be sent to a v2 endpoint."
            )
        return value

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _validate_uuid(value, field_name="run_id")

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: dt.datetime) -> dt.datetime:
        return to_utc(value)

    def derive_source_key(self) -> str:  # pragma: no cover - overridden by every subclass
        raise NotImplementedError

    def check_source_key(self) -> None:
        """Raise when the declared key does not match the derivation for this kind."""
        expected = self.derive_source_key()
        if self.source_key != expected:
            raise ValueError(
                f"source_key {self.source_key!r} does not match the derivation for a "
                f"{self.kind!r} observation, which is {expected!r}. See "
                "docs/contracts/collector-protocol.md; a collector that derives keys "
                "differently creates duplicate rows for one object."
            )


_UUID_RE: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _validate_uuid(value: str, *, field_name: str) -> str:
    if not _UUID_RE.match(value):
        raise ValueError(
            f"{field_name} must be a UUID; received {value!r}. The collector generates it "
            "and reuses it on retry, which is what makes the operation idempotent."
        )
    return value.lower()
