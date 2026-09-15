r"""Change detection: what moved in the estate, whether it matters, and what it did.

Phase 7A recorded every state every object was observed to hold. This package turns that
record into the thing an operator actually opens on a Monday morning — *what changed since
Friday, and is any of it a problem?* — without either of the two failures an ordinary scan
diff makes:

* **It never calls a gap a removal.** Absence is only ever read from a tombstone, which only
  a reconciled scope can write (:mod:`app.history.closure`). A scan that did not mention a
  share produced fewer observations, not evidence of a deletion.
* **It never calls the start of observation a creation.** An object's first version is
  distinguished from an object that appeared inside something already under observation, and
  the two are different actions with different severities.

## The layers, and what each one is allowed to know

===================================== ===============================================
:mod:`app.changes.model`              the vocabulary: four judgments, each with an
                                      "ADG cannot tell" value
:mod:`app.changes.fields`             what each stored column means when it moves, and
                                      what contains what
:mod:`app.changes.principals`         broad trustees and privileged groups
:mod:`app.changes.rules`              the ordered severity table, first match wins
:mod:`app.changes.classify`           two versions in, one classified change out. Pure
:mod:`app.changes.scope`              what "changes on this share" selects, kind by kind
:mod:`app.changes.repository`         the windowed reads over ``object_versions``
:mod:`app.changes.correlation`        ACE edits paired back together; whether a
                                      reordering moved the normalized ACL
:mod:`app.changes.service`            the feed, the summary, the comparison
:mod:`app.changes.impact`             why access changed, over the live engine
===================================== ===============================================

Everything above :mod:`app.changes.repository` is pure and tested without PostgreSQL.
"""

from app.changes.classify import action_for, classify, deltas_between, significance_for
from app.changes.correlation import AceEdit, Correlation, correlate, ordering_materiality
from app.changes.fields import FIELD_SIGNIFICANCE, container_kind_of, significance_of
from app.changes.impact import (
    AccessDelta,
    ChangeImpact,
    ChangeImpactService,
    ImpactVerdict,
    MembershipDelta,
)
from app.changes.model import (
    SEVERITY_ORDER,
    ChangeAction,
    ChangeDirection,
    ChangeSeverity,
    ChangeSignificance,
    ChangeSummary,
    FieldDelta,
    FieldSignificance,
    ObjectChange,
    SubjectRef,
    at_least,
)
from app.changes.principals import is_broad_trustee, is_privileged_group
from app.changes.repository import ChangeRepository, VersionCursor
from app.changes.rules import RULES, ChangeFacts, Rule, RuleOutcome, evaluate
from app.changes.scope import ChangeScope, ScopeTarget
from app.changes.service import (
    DEFAULT_ACTIONS,
    DEFAULT_SIGNIFICANCE,
    ChangeComparison,
    ChangeFeed,
    ChangeFilter,
    ChangeService,
    ObjectChanges,
)

__all__ = [
    "DEFAULT_ACTIONS",
    "DEFAULT_SIGNIFICANCE",
    "FIELD_SIGNIFICANCE",
    "RULES",
    "SEVERITY_ORDER",
    "AccessDelta",
    "AceEdit",
    "ChangeAction",
    "ChangeComparison",
    "ChangeDirection",
    "ChangeFacts",
    "ChangeFeed",
    "ChangeFilter",
    "ChangeImpact",
    "ChangeImpactService",
    "ChangeRepository",
    "ChangeScope",
    "ChangeService",
    "ChangeSeverity",
    "ChangeSignificance",
    "ChangeSummary",
    "Correlation",
    "FieldDelta",
    "FieldSignificance",
    "ImpactVerdict",
    "MembershipDelta",
    "ObjectChange",
    "ObjectChanges",
    "Rule",
    "RuleOutcome",
    "ScopeTarget",
    "SubjectRef",
    "VersionCursor",
    "action_for",
    "at_least",
    "classify",
    "container_kind_of",
    "correlate",
    "deltas_between",
    "evaluate",
    "is_broad_trustee",
    "is_privileged_group",
    "ordering_materiality",
    "significance_for",
    "significance_of",
]
