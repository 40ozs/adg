r"""What an installation may change about the rules, and the one thing it must supply.

Everything here is a deliberate decision by an operator, expressed as data. A rule can be
turned off, its bands re-graded, and its thresholds moved; none of that changes a predicate,
so a finding produced under one configuration and one produced under another were produced by
the same logic and can be compared.

**Sensitivity is the thing that must be supplied.** ADG cannot look at a directory and know
that it holds payroll data. It can see a folder called ``HR`` and it will not conclude
anything from that, because a folder called ``HR`` in a test estate holds nothing and a folder
called ``Misc`` holds everything. So the sensitive-resource rule fires only for resources this
configuration names explicitly, and with no configuration it produces nothing at all — not a
guess, not a heuristic, not a keyword list. See ADR-0024.

A configuration is loaded from a JSON document, validated on the way in, and refused loudly
rather than partially. Every error names the entry it came from, because a configuration file
that silently drops the one sensitive resource somebody cared about is worse than one that
refuses to load.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.access_engine import CATEGORY_REQUIRED_MASKS, RightsCategory
from app.domain.errors import DomainValidationError
from app.risk_engine.catalog import CATALOG, RuleDefinition, RuleId, definition_for
from app.risk_engine.facts import ResourceFacts, ShareFacts
from app.risk_engine.severity import Severity, SeverityBand

__all__ = [
    "DEFAULT_CONFIGURATION",
    "RiskConfiguration",
    "RuleConfig",
    "SensitiveResource",
    "load_configuration",
    "parse_configuration",
]


class ConfigurationError(DomainValidationError):
    """A risk configuration that cannot be applied as written."""


def _normalize_path(value: str) -> str:
    """A UNC path folded for comparison: case-insensitive, no trailing separator.

    Windows paths are case-insensitive, and ``\\\\FS01\\Finance`` and ``\\\\FS01\\Finance\\``
    are the same directory. Both are folded here so that a tag written either way matches.
    """
    return value.strip().rstrip("\\").casefold()


@dataclass(frozen=True, slots=True)
class SensitiveResource:
    """One resource, or one subtree, an operator has declared sensitive.

    Exactly one selector is set. A tag with several would be ambiguous about what it covers,
    and an operator reading a report needs to know which of their entries produced a finding.
    """

    label: str
    """What makes it sensitive, in the operator's own words. Rendered on the finding."""

    resource_key: str | None = None
    path_prefix: str | None = None
    share_key: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ConfigurationError(
                "A sensitive-resource entry needs a label saying what makes it sensitive. "
                "The label is what a reader is shown in place of a judgment ADG cannot make.",
                field="label",
            )
        selectors = [self.resource_key, self.path_prefix, self.share_key]
        set_count = sum(1 for selector in selectors if selector)
        if set_count != 1:
            raise ConfigurationError(
                "A sensitive-resource entry sets exactly one of resource_key, path_prefix "
                f"or share_key; this one sets {set_count}.",
                field="selector",
            )
        if self.path_prefix is not None:
            object.__setattr__(self, "path_prefix", _normalize_path(self.path_prefix))
            if not self.path_prefix:
                raise ConfigurationError("path_prefix must not be blank.", field="path_prefix")

    @property
    def selector(self) -> str:
        """The single selector, rendered, so a finding can name which tag matched it."""
        if self.resource_key is not None:
            return f"resource_key={self.resource_key}"
        if self.share_key is not None:
            return f"share_key={self.share_key}"
        return f"path_prefix={self.path_prefix}"

    def matches_resource(self, resource: ResourceFacts) -> bool:
        """Whether this tag covers ``resource``.

        The prefix test compares whole path segments. ``\\\\FS01\\Finance`` covers
        ``\\\\FS01\\Finance\\Payroll`` and does **not** cover ``\\\\FS01\\Finance-Archive``;
        a bare string prefix would cover both, and the second is a different share whose
        sensitivity nobody declared.
        """
        if self.resource_key is not None:
            return resource.resource_key == self.resource_key
        if self.share_key is not None:
            return resource.share_key == self.share_key
        assert self.path_prefix is not None  # guaranteed by __post_init__
        candidate = _normalize_path(resource.path)
        return candidate == self.path_prefix or candidate.startswith(f"{self.path_prefix}\\")

    def matches_share(self, share: ShareFacts) -> bool:
        """Whether this tag covers a share's own ACL.

        A ``path_prefix`` tag never matches a share: a share is not at a path, and treating
        the two as interchangeable would let one tag mean different things on the two layers.
        """
        if self.share_key is not None:
            return share.share_key == self.share_key
        return False


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """One rule's settings, already merged with the catalog's defaults.

    Never constructed by hand outside tests: :meth:`RiskConfiguration.for_rule` builds it, so
    a rule always sees a complete set of options and never has to ask whether a threshold was
    configured.
    """

    definition: RuleDefinition
    enabled: bool
    severities: Mapping[SeverityBand, Severity]
    options: Mapping[str, Any]

    sensitive_resources: tuple[SensitiveResource, ...] = ()
    """The installation's sensitivity tags, carried on every rule's settings.

    Only one rule reads them, and it is carried here rather than handed to that rule
    specially so that every rule has one signature and the engine has no branch in it. A
    branch for one rule is where the next special case goes, and the rule after that is the
    one that quietly stops being evaluated the same way as the rest.
    """

    @property
    def rule_id(self) -> RuleId:
        return self.definition.id

    def severity_for(self, band: SeverityBand) -> Severity:
        """The severity this installation assigns to ``band``.

        Raises:
            ConfigurationError: the rule does not report that band. A rule returning a band
                it never declared is a bug in the rule, and inventing a severity for it would
                put an unexplained number in a report.
        """
        try:
            return self.severities[band]
        except KeyError as error:
            raise ConfigurationError(
                f"Rule {self.rule_id.value!r} reported band {band.value!r}, which it does "
                f"not declare. Declared bands: {[b.value for b in self.definition.bands]}.",
                field="band",
            ) from error

    def option(self, name: str) -> Any:
        """One threshold.

        Raises:
            ConfigurationError: the rule declares no such option. A rule reading an option
                that does not exist would silently read ``None`` and stop matching.
        """
        try:
            return self.options[name]
        except KeyError as error:
            raise ConfigurationError(
                f"Rule {self.rule_id.value!r} read option {name!r}, which it does not "
                f"declare. Declared options: {sorted(self.definition.default_options)}.",
                field=name,
            ) from error

    def option_int(self, name: str, *, minimum: int = 0) -> int:
        value = self.option(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must be a whole number; "
                f"received {value!r}.",
                field=name,
            )
        if value < minimum:
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must be at least {minimum}; "
                f"received {value}.",
                field=name,
            )
        return value

    def option_bool(self, name: str) -> bool:
        value = self.option(name)
        if not isinstance(value, bool):
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must be true or false; "
                f"received {value!r}.",
                field=name,
            )
        return value

    def option_category(self, name: str) -> RightsCategory:
        """A rights category threshold, as a value the rights algebra recognizes."""
        value = self.option(name)
        try:
            category = RightsCategory(str(value))
        except ValueError as error:
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must name a rights "
                f"category; received {value!r}. Valid values: "
                f"{sorted(item.value for item in CATEGORY_REQUIRED_MASKS)}.",
                field=name,
            ) from error
        if category not in CATEGORY_REQUIRED_MASKS:
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} names category "
                f"{category.value!r}, which has no required mask and so cannot be a "
                "threshold. 'none' and 'special' describe a result, not a level.",
                field=name,
            )
        return category

    def option_strings(self, name: str) -> tuple[str, ...]:
        value = self.option(name)
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must be a list of strings; "
                f"received {value!r}.",
                field=name,
            )
        return tuple(str(item) for item in value)

    def option_ints(self, name: str) -> tuple[int, ...]:
        value = self.option(name)
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ConfigurationError(
                f"Option {name!r} of rule {self.rule_id.value!r} must be a list of whole "
                f"numbers; received {value!r}.",
                field=name,
            )
        numbers: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ConfigurationError(
                    f"Option {name!r} of rule {self.rule_id.value!r} contains {item!r}, "
                    "which is not a whole number.",
                    field=name,
                )
            numbers.append(item)
        return tuple(numbers)


@dataclass(frozen=True, slots=True)
class RiskConfiguration:
    """The whole installation's risk settings.

    ``version`` is the operator's own label for this configuration, carried onto every
    evaluation so that a report can say which settings produced it. It is not validated
    beyond being a string: ADG has no opinion about how an installation versions its own
    policy, only that a stored finding should be traceable to one.
    """

    version: str = "default"
    disabled_rules: frozenset[RuleId] = frozenset()
    severity_overrides: Mapping[RuleId, Mapping[SeverityBand, Severity]] = field(
        default_factory=dict
    )
    option_overrides: Mapping[RuleId, Mapping[str, Any]] = field(default_factory=dict)
    sensitive_resources: tuple[SensitiveResource, ...] = ()

    def for_rule(self, rule_id: RuleId) -> RuleConfig:
        """This rule's settings, merged over the catalog's defaults."""
        definition = definition_for(rule_id)
        severities = dict(definition.default_severities)
        severities.update(self.severity_overrides.get(rule_id, {}))
        options = dict(definition.default_options)
        options.update(self.option_overrides.get(rule_id, {}))
        return RuleConfig(
            definition=definition,
            enabled=definition.default_enabled and rule_id not in self.disabled_rules,
            severities=severities,
            options=options,
            sensitive_resources=self.sensitive_resources,
        )

    def enabled_rules(self) -> tuple[RuleId, ...]:
        """Every rule this installation runs, in catalog order."""
        return tuple(rule_id for rule_id in CATALOG if self.for_rule(rule_id).enabled)

    def tags_for_resource(self, resource: ResourceFacts) -> tuple[SensitiveResource, ...]:
        """Every sensitivity tag covering ``resource``, in declaration order."""
        return tuple(tag for tag in self.sensitive_resources if tag.matches_resource(resource))

    def tags_for_share(self, share: ShareFacts) -> tuple[SensitiveResource, ...]:
        return tuple(tag for tag in self.sensitive_resources if tag.matches_share(share))

    @property
    def marks_anything_sensitive(self) -> bool:
        """Whether any resource is declared sensitive at all.

        Read by callers that want to say *"no resource is marked sensitive, so this rule
        reported nothing"* rather than showing an empty section that reads as a clean bill of
        health. That distinction is the same one ADR-0016 makes about empty access answers.
        """
        return bool(self.sensitive_resources)


DEFAULT_CONFIGURATION: Final = RiskConfiguration()
"""Every rule at its shipped severity, and nothing marked sensitive.

The sensitive-resource rule is *enabled* under this configuration and produces nothing, which
is the honest state: the rule is running and has been given nothing to look at. Disabling it
by default would make a later operator think they had turned something on when they had only
declared a tag.
"""


def _parse_severity(value: Any, *, where: str) -> Severity:
    try:
        return Severity(str(value))
    except ValueError as error:
        raise ConfigurationError(
            f"{where}: {value!r} is not a severity. Valid values: "
            f"{sorted(item.value for item in Severity)}.",
            field=where,
        ) from error


def _parse_band(value: Any, definition: RuleDefinition, *, where: str) -> SeverityBand:
    try:
        band = SeverityBand(str(value))
    except ValueError as error:
        raise ConfigurationError(
            f"{where}: {value!r} is not a severity band. Valid values: "
            f"{sorted(item.value for item in SeverityBand)}.",
            field=where,
        ) from error
    if band not in definition.bands:
        raise ConfigurationError(
            f"{where}: rule {definition.id.value!r} never reports band {band.value!r}. "
            f"It reports {[item.value for item in definition.bands]}. An override for a "
            "band a rule cannot produce would look like a setting and do nothing.",
            field=where,
        )
    return band


def _parse_rule_id(value: Any) -> RuleId:
    try:
        return RuleId(str(value))
    except ValueError as error:
        raise ConfigurationError(
            f"{value!r} is not a known rule. Valid rules: {sorted(item.value for item in RuleId)}.",
            field="rule",
        ) from error


def _require_mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{where} must be an object; received {value!r}.", field=where)
    return value


def parse_configuration(document: Mapping[str, Any]) -> RiskConfiguration:
    """Build a configuration from a parsed JSON document.

    The document's shape, with every part optional::

        {
          "version": "2026-q3",
          "rules": {
            "everyone_broad_access": {
              "enabled": true,
              "severities": {"read": "critical"},
              "options": {"minimum_category": "modify"}
            }
          },
          "sensitive_resources": [
            {"label": "Payroll", "path_prefix": "\\\\FS01\\Finance\\Payroll"}
          ]
        }

    Raises:
        ConfigurationError: any entry that cannot be applied as written, naming the entry.
    """
    document = _require_mapping(document, where="configuration")
    version = str(document.get("version", "default"))

    disabled: set[RuleId] = set()
    severities: dict[RuleId, dict[SeverityBand, Severity]] = {}
    options: dict[RuleId, dict[str, Any]] = {}

    rules = _require_mapping(document.get("rules", {}), where="rules")
    for raw_id, raw_rule in rules.items():
        rule_id = _parse_rule_id(raw_id)
        definition = definition_for(rule_id)
        entry = _require_mapping(raw_rule, where=f"rules.{rule_id.value}")

        if "enabled" in entry:
            enabled = entry["enabled"]
            if not isinstance(enabled, bool):
                raise ConfigurationError(
                    f"rules.{rule_id.value}.enabled must be true or false; received {enabled!r}.",
                    field="enabled",
                )
            if not enabled:
                disabled.add(rule_id)

        raw_severities = _require_mapping(
            entry.get("severities", {}), where=f"rules.{rule_id.value}.severities"
        )
        for raw_band, raw_severity in raw_severities.items():
            where = f"rules.{rule_id.value}.severities.{raw_band}"
            band = _parse_band(raw_band, definition, where=where)
            severities.setdefault(rule_id, {})[band] = _parse_severity(raw_severity, where=where)

        raw_options = _require_mapping(
            entry.get("options", {}), where=f"rules.{rule_id.value}.options"
        )
        for name, value in raw_options.items():
            if name not in definition.default_options:
                raise ConfigurationError(
                    f"rules.{rule_id.value}.options names {name!r}, which this rule does "
                    f"not have. Its options are {sorted(definition.default_options)}.",
                    field=name,
                )
            options.setdefault(rule_id, {})[str(name)] = value

    tags = _parse_sensitive(document.get("sensitive_resources", []))

    return RiskConfiguration(
        version=version,
        disabled_rules=frozenset(disabled),
        severity_overrides={rule: dict(bands) for rule, bands in severities.items()},
        option_overrides={rule: dict(values) for rule, values in options.items()},
        sensitive_resources=tags,
    )


def _parse_sensitive(raw: Any) -> tuple[SensitiveResource, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ConfigurationError(
            f"sensitive_resources must be a list; received {raw!r}.", field="sensitive_resources"
        )
    entries: list[SensitiveResource] = []
    for index, item in enumerate(raw):
        where = f"sensitive_resources[{index}]"
        entry = _require_mapping(item, where=where)
        unknown = set(entry) - {"label", "resource_key", "path_prefix", "share_key"}
        if unknown:
            raise ConfigurationError(
                f"{where} has unrecognized keys {sorted(unknown)}. A misspelled selector "
                "would silently mark nothing sensitive.",
                field=where,
            )
        try:
            entries.append(
                SensitiveResource(
                    label=str(entry.get("label", "")),
                    resource_key=_optional_text(entry.get("resource_key")),
                    path_prefix=_optional_text(entry.get("path_prefix")),
                    share_key=_optional_text(entry.get("share_key")),
                )
            )
        except ConfigurationError as error:
            raise ConfigurationError(f"{where}: {error}", field=where) from error
    return tuple(entries)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_configuration(path: str | Path) -> RiskConfiguration:
    """Read and validate a configuration file.

    Raises:
        ConfigurationError: the file is missing, is not valid JSON, or cannot be applied.
            A missing file is an error rather than a silent fall back to the defaults: an
            operator who configured a path and typed it wrongly would otherwise get a clean
            report produced by settings they never wrote.
    """
    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"The risk configuration at {candidate} could not be read: {error}.",
            field="path",
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"The risk configuration at {candidate} is not valid JSON: {error}.", field="path"
        ) from error
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"The risk configuration at {candidate} must be a JSON object.", field="path"
        )
    return parse_configuration(document)


def describe_configuration(configuration: RiskConfiguration) -> tuple[str, ...]:
    """Lines an operator can read to confirm what is actually in force.

    Deliberately includes the rules that are *off* and the fact that nothing is marked
    sensitive. A summary that listed only what was enabled would make a configuration with
    the interesting rules disabled look identical to a healthy one.
    """
    lines = [f"Risk configuration version {configuration.version!r}."]
    for rule_id in CATALOG:
        rule = configuration.for_rule(rule_id)
        state = "enabled" if rule.enabled else "disabled"
        bands = ", ".join(
            f"{band.value}={rule.severities[band].value}" for band in rule.definition.bands
        )
        lines.append(f"  {rule_id.value}: {state} ({bands})")
    if configuration.marks_anything_sensitive:
        lines.append(f"  {len(configuration.sensitive_resources)} resource tags declared.")
    else:
        lines.append(
            "  No resource is marked sensitive, so the sensitive-resource rule will report "
            "nothing. That is a configuration state, not a clean result."
        )
    return tuple(lines)


def merge_option_overrides(
    base: RiskConfiguration, rule_id: RuleId, **overrides: Any
) -> RiskConfiguration:
    """A copy of ``base`` with extra option overrides for one rule. Used by tests and tools."""
    definition = definition_for(rule_id)
    unknown = set(overrides) - set(definition.default_options)
    if unknown:
        raise ConfigurationError(
            f"Rule {rule_id.value!r} has no options {sorted(unknown)}.", field="options"
        )
    merged: dict[RuleId, dict[str, Any]] = {
        key: dict(value) for key, value in base.option_overrides.items()
    }
    merged.setdefault(rule_id, {}).update(overrides)
    return RiskConfiguration(
        version=base.version,
        disabled_rules=base.disabled_rules,
        severity_overrides=base.severity_overrides,
        option_overrides=merged,
        sensitive_resources=base.sensitive_resources,
    )


def only_rules(base: RiskConfiguration, rules: Iterable[RuleId]) -> RiskConfiguration:
    """A copy of ``base`` with every rule but ``rules`` disabled."""
    keep = frozenset(rules)
    return RiskConfiguration(
        version=base.version,
        disabled_rules=frozenset(rule for rule in RuleId if rule not in keep),
        severity_overrides=base.severity_overrides,
        option_overrides=base.option_overrides,
        sensitive_resources=base.sensitive_resources,
    )
