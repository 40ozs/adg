"""Failures that belong to remediation planning and to nothing else.

Five types rather than one, because the five things that go wrong here want five different
responses and four different status codes. A caller that collapsed them would tell an
operator "the plan could not be exported" and leave them to guess whether the fix is to ask
somebody else, to wait for a scan, to write a new plan, or to configure a signing key.

:class:`RemediationDisabled` is the odd one out and is deliberately not a validation error.
Nothing the caller sends can make execution available: no request body, no capability, no
role. It is raised by the executor interface itself, and the only thing that changes it is a
deployment that does not exist yet (``docs/architecture/remediation.md`` §8).
"""

from __future__ import annotations

from app.domain.errors import DomainError, DomainValidationError

__all__ = [
    "RemediationConflict",
    "RemediationDisabled",
    "RemediationForbidden",
    "RemediationNotFound",
    "RemediationValidationError",
]


class RemediationValidationError(DomainValidationError):
    """A plan, a change or an approval was not well formed. → 422."""


class RemediationNotFound(Exception):
    """A plan, change, approval or export that does not exist. → 404."""


class RemediationConflict(Exception):
    """The plan is real and its state does not permit this. → 409.

    Covers the lifecycle refusals — approving a draft, exporting something nobody approved —
    and the stale-state refusal, which is the one this phase exists for: an approved plan
    whose preconditions no longer hold is not a plan that can be exported, and saying so is
    the feature rather than the error.
    """


class RemediationForbidden(Exception):
    """The caller holds the capability and lacks the authority. → 403.

    Capability is not authority here, exactly as it is not in governance: holding
    ``remediation:approve`` admits a request to the approval route, and the separation of
    duties decides whether *this* approver may approve *this* plan. The requestor may not,
    however many roles they hold.
    """


class RemediationDisabled(DomainError):
    """Something asked ADG to change the estate. ADG has no way to do that. → 501.

    Raised by :class:`app.remediation.executor.DisabledRemediator`, which is the executor
    every deployment gets. The message names the three things that would each have to be
    true before any execution path existed, so that somebody who reaches this by accident
    learns what the product's posture is rather than filing it as a bug.
    """
