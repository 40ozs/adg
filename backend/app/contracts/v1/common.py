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
from typing import Annotated, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domain import (
    AceSource,
    AceType,
    CollectorKind,
    DomainValidationError,
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    ObservationSource,
    PrincipalKind,
    SharePermission,
    ShareType,
    Sid,
    UnresolvedReason,
)

SCHEMA_VERSION: Final = "1.0"
"""Current contract version. Minor bumps are additive; a breaking change means v2."""

SCHEMA_VERSION_PATTERN: Final = re.compile(r"^1\.[0-9]+$")

MAX_BATCH_OBSERVATIONS: Final = 1000
"""An oversized batch is rejected, never truncated: silent truncation looks like coverage."""

MAX_ACCESS_MASK: Final = 0xFFFFFFFF
MAX_ACE_FLAGS: Final = 0xFF

__all__ = [
    "MAX_ACCESS_MASK",
    "MAX_ACE_FLAGS",
    "MAX_BATCH_OBSERVATIONS",
    "MAX_HOST_NAME_LENGTH",
    "SCHEMA_VERSION",
    "AceSource",
    "AceType",
    "CollectorKind",
    "ContractModel",
    "GroupScope",
    "GroupType",
    "MembershipEdgeKind",
    "ObservationBase",
    "ObservationKind",
    "PrincipalKind",
    "Scope",
    "ScopeKind",
    "SharePermission",
    "ShareType",
    "SourceDescriptor",
    "UnresolvedReason",
    "canonical_sid",
    "normalize_host",
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
