"""Environment-based configuration for the ADG backend.

Configuration is read from the process environment (and from a local ``.env`` file
during development). No secret value is ever committed to source control; see
``.env.example`` at the repository root for the documented variables.
"""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.alerts.configuration import DEFAULT_POLICY, AlertPolicy, load_policy
from app.auth.dev_users import DEFAULT_DEV_AUTH_USERS, DevelopmentUser, parse_development_users
from app.auth.roles import Role, parse_roles
from app.demo_mode.datasets import CANONICAL_DATASET, DATASETS
from app.history.retention import RetentionPolicy
from app.risk_engine.configuration import (
    DEFAULT_CONFIGURATION,
    RiskConfiguration,
    load_configuration,
)
from app.runtime_mode import RuntimeMode, parse_runtime_mode

Environment = Literal["development", "test", "production"]
LogFormat = Literal["json", "text"]
#: Which remediator a deployment has. There is deliberately no value naming a real
#: write adapter: see app/remediation/executor.py.
RemediationExecutionMode = Literal["disabled", "lab"]

AuthMode = Literal["oidc", "development"]

# Development-only default. Production deployments must supply ADG_DATABASE_URL;
# `Settings` refuses to start in production while this value is still in place.
DEV_DATABASE_URL = "postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg"

SUPPORTED_DB_SCHEME = "postgresql+psycopg"

#: Shortest collector key ADG will accept. A collector key is a bearer secret that lives in
#: a scheduled task's configuration on a file server; a guessable one is worse than none,
#: because it looks like a control.
MIN_COLLECTOR_KEY_LENGTH = 32

#: Shortest change-plan signing key ADG will accept, for the reason above and one more. The
#: HMAC over an exported change plan is the only thing that distinguishes an instruction this
#: deployment produced from one somebody typed, and the administrator executing it at two in
#: the morning has nothing else to check. A short key makes that signature forgeable while
#: leaving every procedure around it looking intact, which is the failure ADR-0035 exists to
#: prevent. Refusing an unsigned export (app/remediation/export.py) and then accepting a
#: four-character key would be a control in name only.
MIN_SIGNING_KEY_LENGTH = 32

#: How a demo deployment's database must be named: ``demo`` as a whole word of the database
#: name (``adg_demo``, ``adg-demo``, ``demo``). A convention rather than a proof -- the
#: deployment marker in the database itself is the proof -- but a cheap, independent one
#: that stops a demo being pointed at ``adg``, the name every other deployment uses.
DEMO_DATABASE_NAME = re.compile(r"(?:^|[^a-z0-9])demo(?:[^a-z0-9]|$)")

#: The only alert sink kind a demo deployment may hold. A demo has nobody to page and no
#: business making outbound requests on a visitor's behalf.
DEMO_ALERT_SINK_KINDS = frozenset({"log"})


class Settings(BaseSettings):
    """Runtime configuration for the ADG backend."""

    model_config = SettingsConfigDict(
        env_prefix="ADG_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ADG"
    environment: Environment = "development"

    # --- Runtime mode ---------------------------------------------------------------
    #
    # Which world this deployment is connected to. A deployment boundary read once at
    # startup, never a preference a request can change. Unset means production, which is
    # what every deployment that predates the setting is; any value that is not exactly a
    # runtime mode is a startup error, never a fall back. See app/runtime_mode.py and
    # docs/architecture/demo-mode.md.
    runtime_mode: RuntimeMode = RuntimeMode.PRODUCTION
    demo_dataset: str = ""
    """The synthetic dataset a demo deployment runs on (a key of
    app.demo_mode.datasets.DATASETS). Empty means the canonical dataset. Meaningless, and
    refused, in production."""
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    database_url: str = DEV_DATABASE_URL
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_pool_size: int = Field(default=5, ge=1, le=50)

    # Comma-separated list of browser origins allowed to call the API.
    cors_allow_origins: str = "http://localhost:3000"

    # --- Authentication -------------------------------------------------------------
    #
    # 'oidc' is the production mode. 'development' is a local convenience that the
    # validators below refuse to combine with ADG_ENVIRONMENT=production.
    auth_mode: AuthMode = "development"

    # OIDC / Microsoft Entra ID. Every endpoint is named explicitly rather than
    # discovered: a startup that silently reaches out to the network to learn where to
    # verify signatures is a startup whose security depends on DNS.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_roles_claim: str = "roles"
    oidc_groups_claim: str = "groups"
    # Comma-separated '<group object id>:<adg role>' pairs, for tenants that assign access
    # by security group rather than by app role.
    oidc_group_role_map: str = ""

    # Development mode only. The secret signs the tokens this process itself issues; when
    # it is blank a random one is generated per process, so restarting invalidates every
    # development session and no default secret exists to be reused somewhere real.
    dev_auth_secret: str = ""
    dev_auth_users: str = DEFAULT_DEV_AUTH_USERS
    dev_auth_token_lifetime_minutes: int = Field(default=480, ge=1, le=1440)

    # --- History retention ----------------------------------------------------------
    #
    # Two switches rather than one, and the destructive one defaults to off. A deployment
    # that sets a window while thinking about disk capacity has not thereby authorized the
    # deletion of recorded permission history; see app/history/retention.py.
    history_retention_days: int = 0
    """0 means keep every version indefinitely, which is the default. A non-zero value must
    be at least 30 days: a shorter window would let an ordinary audit cycle find the evidence
    of a permission change already gone."""

    history_retention_enabled: bool = False
    """Whether anything acts on the window. Nothing in the API or the collectors calls the
    prune; it is an operator action."""

    # --- Risk rules ------------------------------------------------------------------
    #
    # The rules themselves ship with the application; this names a file that may re-grade
    # their severities, move their thresholds, turn one off, and -- the part ADG cannot
    # supply -- declare which resources are sensitive. Empty means the shipped defaults, in
    # which case the sensitive-resource rule runs and reports nothing, because nothing has
    # been marked. That is a configuration state rather than a clean result; see ADR-0024.
    risk_configuration_path: str = ""
    """Path to a risk configuration JSON document. Empty uses the shipped defaults.

    A path that is set and cannot be read is a startup error rather than a silent fall back:
    an operator who configured a policy and typed the path wrongly would otherwise receive a
    report produced by settings they never wrote."""

    # --- Alerting ----------------------------------------------------------------------
    #
    # Which triggers are live, how loud, and where an alert goes. Empty means the shipped
    # policy: every trigger on, a fifteen-minute cooldown, critical findings only, and one
    # sink that writes to the operator log. A log sink rather than none, deliberately -- an
    # installation with nowhere to deliver would enqueue alerts nothing ever drains.
    alert_policy_path: str = ""
    """Path to an alert policy JSON document. Empty uses the shipped defaults.

    A path that is set and cannot be read is a startup error rather than a silent fall back,
    for the reason the risk configuration gives: an operator who configured a destination and
    typed the path wrongly would otherwise get an installation that delivers nowhere and says
    nothing about it. **The file may carry a webhook credential, so it is configuration and
    never source control.**"""

    alerts_on_run_completion: bool = False
    """Whether closing a scan run re-evaluates the risk rules and the watches inline.

    **Off by default, and that is a cost decision rather than a doubt about the feature.**
    Turning it on adds an incremental risk evaluation, a change-feed scan per watch and a
    delivery attempt to the collector's final request, on an engine nobody has profiled
    against an estate with millions of access control entries. An installation that has
    measured it, or whose estate is small, should turn it on and get alerts within seconds of
    a scan; everything else should drive both from a schedule:

        python -m app.operations evaluate-risks
        python -m app.operations drain-alerts --loop

    Being off is never silent: every completion logs ``post_run`` saying so, and the risk
    report's ``coverage`` block says when the rules were last evaluated -- so an installation
    that turned neither on sees "the rules have not been evaluated" rather than a clean
    report.

    It cannot cost the estate an observation either way. The work runs in its own session
    **after** the ingestion transaction has committed, and both the function and its call
    site swallow failures into a log line."""

    # --- Collection coverage -----------------------------------------------------------
    #
    # Which collector kinds this estate is supposed to run. ADG cannot otherwise tell a
    # collector nobody configured from one that has never reported, so a kind that is
    # expected and has never reported makes collection coverage incomplete (and every risk
    # total "not exhaustive"), while one that is not expected is reported as not
    # configured. Empty means no expectation; a demo deployment uses its dataset's own.
    expected_collectors: str = ""
    """Comma-separated collector kinds: any of active_directory, smb, ntfs, local_groups."""

    # --- Remediation -------------------------------------------------------------------
    #
    # ADG describes changes and performs none of them. These three settings say what the
    # deployment may do with a change plan, and the defaults say "write one, and hand it to
    # a person". There is deliberately no setting that enables a real write path: shipping
    # one is an adapter, a credential and a capability grant, not a configuration change.
    # See docs/architecture/remediation.md section 8 and ADR-0035.
    remediation_execution_mode: RemediationExecutionMode = "disabled"
    """Which remediator implementation this deployment gets. ``disabled`` is the only value a
    production deployment may hold, refused at startup otherwise.

    ``lab`` selects a fixture-backed executor that mutates an in-memory copy of a JSON file
    and can reach nothing else -- no Windows object, no collected table, no file it was not
    pointed at. It exists so the remediator interface can be exercised; nothing in the API
    constructs one."""

    remediation_lab_fixture_path: str = ""
    """The fixture ``lab`` mode reads. Required in that mode and meaningless otherwise.

    Required rather than optional because a lab executor without a fixture would have to
    invent the objects it is asked about, and would then report every plan as carried out --
    which is exactly the failure a lab adapter exists to make visible."""

    remediation_signing_key: str = ""
    """The HMAC key that signs an exported change plan. **A secret: configuration, never
    source control.**

    Empty means this deployment cannot export. That is a refusal rather than a fallback to an
    unsigned document: an unsigned change plan is indistinguishable from one somebody typed,
    and the administrator executing it at two in the morning has no way to tell them apart.
    The key identifier published beside a signature is a digest prefix of this value, so
    rotating it changes the identifier and a verifier holding the old key can say "signed
    with a key I do not have" instead of reporting tampering that did not happen."""

    # Comma-separated '<key id>:<secret>' pairs that collectors present as
    # X-ADG-Collector-Key. Empty means ingestion requires an administrator's bearer token.
    collector_api_keys: str = ""

    @property
    def history_retention_policy(self) -> RetentionPolicy:
        """The retention policy as a validated domain value.

        Built here rather than read field by field so that the two settings can only be
        combined in the ways the policy allows -- an enabled policy with no window is
        refused, which is the combination that reads as protection that is not there.
        """
        return RetentionPolicy(
            retain_days=self.history_retention_days, enabled=self.history_retention_enabled
        )

    @property
    def risk_configuration(self) -> RiskConfiguration:
        """The risk rules as this installation has configured them.

        Read on each access rather than cached on the settings object: the file is read once
        per evaluation, not once per request, and an operator who edits it does not have to
        restart the API to have the next report use it.
        """
        path = self.risk_configuration_path.strip()
        if path:
            return load_configuration(path)
        if self.is_demo:
            # A synthetic dataset may declare its own sensitive resources, as an operator
            # would for a real estate; without them the sensitive-resource rule runs and
            # reports nothing. Supplied through the dataset contract, never hard-coded here.
            return _dataset_risk_configuration(self.resolved_demo_dataset)
        return DEFAULT_CONFIGURATION

    @property
    def alert_policy(self) -> AlertPolicy:
        """The alert policy as this installation has configured it.

        Read on each access rather than cached on the settings object, matching
        :attr:`risk_configuration`: the file is read once per evaluation rather than once per
        request, and an operator who adds a webhook does not have to restart the API for the
        next alert to use it.
        """
        path = self.alert_policy_path.strip()
        policy = load_policy(path) if path else DEFAULT_POLICY
        if self.is_demo:
            # Checked on every read, not only at startup, because the file is re-read on every
            # read: a webhook added to a running demo's policy is refused at the next
            # evaluation rather than delivered to.
            _refuse_outbound_alert_sinks(policy)
        return policy

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_demo(self) -> bool:
        """Whether this deployment runs on synthetic data. Distinct from :attr:`is_production`,
        which is about how strict the deployment is, not what it is connected to: a public
        demo is a production-grade deployment of demo mode."""
        return self.runtime_mode is RuntimeMode.DEMO

    @property
    def resolved_demo_dataset(self) -> str:
        """The dataset key a demo deployment runs on: the configured one, or the canonical."""
        return self.demo_dataset.strip() or CANONICAL_DATASET

    @property
    def is_development_auth(self) -> bool:
        return self.auth_mode == "development"

    @property
    def oidc_scope_list(self) -> list[str]:
        return [scope for scope in self.oidc_scopes.split() if scope]

    @property
    def resolved_dev_auth_secret(self) -> str:
        """The signing secret for development tokens, configured or generated."""
        return self.dev_auth_secret

    @property
    def development_users(self) -> tuple[DevelopmentUser, ...]:
        return parse_development_users(self.dev_auth_users)

    @property
    def group_role_map(self) -> dict[str, Role]:
        """Group object id (casefolded) -> role, as validated at startup."""
        return _parse_group_role_map(self.oidc_group_role_map)

    @property
    def expected_collector_kinds(self) -> tuple[str, ...]:
        """The configured expected collector kinds, validated at startup, in name order."""
        return _parse_expected_collectors(self.expected_collectors)

    @property
    def collector_key_map(self) -> dict[str, str]:
        """Key id -> secret, as validated at startup. Empty when none are configured."""
        return _parse_collector_keys(self.collector_api_keys)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"ADG_LOG_LEVEL must be one of {sorted(allowed)}; received {value!r}.")
        return normalized

    @field_validator("expected_collectors")
    @classmethod
    def _validate_expected_collectors(cls, value: str) -> str:
        _parse_expected_collectors(value)
        return value

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def _parse_runtime_mode(cls, value: object) -> RuntimeMode:
        return parse_runtime_mode(value)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(f"{SUPPORTED_DB_SCHEME}://"):
            raise ValueError(
                "ADG_DATABASE_URL must use the "
                f"{SUPPORTED_DB_SCHEME!r} scheme (PostgreSQL via psycopg); "
                f"received {value.split('://', 1)[0]!r}."
            )
        return value

    @model_validator(mode="after")
    def _reject_development_defaults_in_production(self) -> Settings:
        if self.is_production and self.database_url == DEV_DATABASE_URL:
            raise ValueError(
                "ADG_DATABASE_URL is still the development default while "
                "ADG_ENVIRONMENT=production. Set ADG_DATABASE_URL to the production "
                "connection string before starting the API."
            )
        return self

    @model_validator(mode="after")
    def _reject_development_auth_in_production(self) -> Settings:
        """The rule that makes development authentication safe to ship.

        Development mode signs its own tokens and asks for no credential. It is a
        configuration error rather than a risk decision, so it is refused at startup: the
        process does not begin, instead of beginning with an open front door.
        """
        if self.is_production and self.is_development_auth:
            raise ValueError(
                "ADG_AUTH_MODE=development cannot be used with ADG_ENVIRONMENT=production. "
                "Development authentication issues its own tokens and verifies no "
                "credential. Set ADG_AUTH_MODE=oidc and configure ADG_OIDC_ISSUER, "
                "ADG_OIDC_AUDIENCE, and ADG_OIDC_JWKS_URL."
            )
        return self

    @model_validator(mode="after")
    def _require_a_strong_signing_key(self) -> Settings:
        """A configured signing key must be long enough to be worth having.

        Empty is allowed and means this deployment cannot export, which is a refusal the
        export path states in full. What is refused here is the middle case: a key short
        enough to guess, which produces signatures that verify, documents that look
        authoritative, and a control that is not one.
        """
        key = self.remediation_signing_key.strip()
        if key and len(key) < MIN_SIGNING_KEY_LENGTH:
            raise ValueError(
                f"ADG_REMEDIATION_SIGNING_KEY is shorter than {MIN_SIGNING_KEY_LENGTH} "
                "characters. It is the HMAC key over an exported change plan, which is the "
                "only thing separating an instruction this deployment produced from one "
                "somebody typed. Generate one with "
                "[Convert]::ToBase64String((1..32 | ForEach-Object "
                "{ Get-Random -Maximum 256 })), or leave it empty to disable export."
            )
        return self

    @model_validator(mode="after")
    def _reject_lab_remediation_in_production(self) -> Settings:
        """Lab remediation mode cannot be combined with a production environment.

        Stated here as well as in :func:`app.remediation.executor.remediator_for`, on
        purpose. A guard written once is a guard somebody moves; this one refuses at startup,
        so the process does not begin rather than beginning with an executor that reports
        changes it made to a fixture as though they had happened in the estate.
        """
        if self.is_production and self.remediation_execution_mode != "disabled":
            raise ValueError(
                f"ADG_REMEDIATION_EXECUTION_MODE={self.remediation_execution_mode} cannot be "
                "used with ADG_ENVIRONMENT=production. Lab mode applies changes to a test "
                "fixture and reports them as applied; a production deployment running it "
                "would be one misreading away from somebody believing ADG had carried out a "
                "change it had not. Set ADG_REMEDIATION_EXECUTION_MODE=disabled."
            )
        return self

    @model_validator(mode="after")
    def _require_a_lab_fixture(self) -> Settings:
        """Lab mode without a fixture is refused rather than degraded to ``disabled``.

        A test that asked for a lab executor and silently received a refusing one would pass
        for the wrong reason, which is worse than a startup failure naming the setting.
        """
        if (
            self.remediation_execution_mode == "lab"
            and not self.remediation_lab_fixture_path.strip()
        ):
            raise ValueError(
                "ADG_REMEDIATION_EXECUTION_MODE=lab requires "
                "ADG_REMEDIATION_LAB_FIXTURE_PATH. A lab executor without a fixture would "
                "have to invent the objects it is asked about, and would then report every "
                "plan as carried out."
            )
        return self

    @model_validator(mode="after")
    def _require_oidc_configuration(self) -> Settings:
        if self.auth_mode != "oidc":
            return self
        missing = [
            name
            for name, value in (
                ("ADG_OIDC_ISSUER", self.oidc_issuer),
                ("ADG_OIDC_AUDIENCE", self.oidc_audience),
                ("ADG_OIDC_JWKS_URL", self.oidc_jwks_url),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError(
                f"ADG_AUTH_MODE=oidc requires {', '.join(missing)}. For Microsoft Entra ID "
                "these are https://login.microsoftonline.com/<tenant>/v2.0, the application "
                "ID URI or client id ADG's tokens are issued for, and "
                "https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys."
            )
        for name, value in (
            ("ADG_OIDC_ISSUER", self.oidc_issuer),
            ("ADG_OIDC_JWKS_URL", self.oidc_jwks_url),
        ):
            if not value.strip().startswith("https://"):
                raise ValueError(
                    f"{name} must be an https URL; received {value!r}. Token verification "
                    "over plain http would let anything on the network mint identities."
                )
        return self

    @model_validator(mode="after")
    def _validate_auth_tables(self) -> Settings:
        """Parse everything table-shaped once, at startup, where the error is visible.

        A malformed role map discovered on the first sign-in attempt is a mystery; the same
        error at startup names the entry.
        """
        _parse_group_role_map(self.oidc_group_role_map)
        _parse_collector_keys(self.collector_api_keys)
        if self.is_development_auth:
            parse_development_users(self.dev_auth_users)
        return self

    @model_validator(mode="after")
    def _validate_runtime_mode(self) -> Settings:
        """The demo deployment's startup contract. Every refusal names what to change.

        Production is checked for one thing only -- that it was not handed a demo setting,
        which is a contradiction about what the deployment is. Everything else about a
        production deployment is exactly as it was before demo mode existed.
        """
        if not self.is_demo:
            if self.demo_dataset.strip():
                raise ValueError(
                    f"ADG_DEMO_DATASET={self.demo_dataset!r} is set but ADG_RUNTIME_MODE is "
                    "production. A production deployment runs on what its collectors report, "
                    "never on a synthetic dataset. Unset ADG_DEMO_DATASET, or set "
                    "ADG_RUNTIME_MODE=demo if this deployment is a demo."
                )
            return self

        if self.resolved_demo_dataset not in DATASETS:
            raise ValueError(
                f"ADG_DEMO_DATASET={self.demo_dataset!r} is not a registered demo dataset. "
                f"Registered: {', '.join(sorted(DATASETS))}."
            )
        database = (make_url(self.database_url).database or "").casefold()
        if not DEMO_DATABASE_NAME.search(database):
            raise ValueError(
                f"ADG_RUNTIME_MODE=demo requires a dedicated demo database, named with 'demo' "
                f"as a word (for example adg_demo); ADG_DATABASE_URL names {database!r}. A demo "
                "deployment wipes and refills its database, so it is never pointed at the "
                "database a production or development deployment uses."
            )
        if self.remediation_execution_mode != "disabled":
            raise ValueError(
                "ADG_REMEDIATION_EXECUTION_MODE must be 'disabled' when ADG_RUNTIME_MODE=demo. "
                "A demo simulates remediation by serving its runbook inert; it holds no "
                "executor of any kind."
            )
        if self.collector_api_keys.strip():
            raise ValueError(
                "ADG_COLLECTOR_API_KEYS must be empty when ADG_RUNTIME_MODE=demo. A demo "
                "deployment accepts no observations from collectors -- its data comes from the "
                "synthetic dataset alone -- so a collector credential configured here could "
                "only be a production credential in the wrong place."
            )
        _refuse_outbound_alert_sinks(self.alert_policy)
        return self

    @model_validator(mode="after")
    def _generate_development_secret(self) -> Settings:
        """Give development mode a signing secret when none was configured.

        Generated per process rather than defaulted to a constant: a constant in the
        repository is a secret in the repository, and somebody would eventually verify a
        token with it somewhere that mattered.
        """
        if self.is_development_auth and not self.dev_auth_secret.strip():
            self.dev_auth_secret = secrets.token_urlsafe(48)
        return self


def _dataset_risk_configuration(key: str) -> RiskConfiguration:
    """The optional ``risk_configuration()`` a demo dataset supplies, or the defaults.

    A dataset that cannot load is an error here, not the shipped defaults: a demo whose
    dataset is unavailable has refused to start and has no estate to evaluate, and a
    configuration quietly swapped for another would make the risk report a different demo's.

    Raises:
        DatasetUnavailable: the selected dataset cannot be loaded.
    """
    from app.demo_mode.datasets import cached_dataset

    supplier = getattr(cached_dataset(key), "risk_configuration", None)
    if not callable(supplier):
        return DEFAULT_CONFIGURATION
    configuration = supplier()
    if not isinstance(configuration, RiskConfiguration):
        raise TypeError(
            f"Demo dataset {key!r} returned {type(configuration).__name__} from "
            "risk_configuration(); expected app.risk_engine.configuration.RiskConfiguration."
        )
    return configuration


def _refuse_outbound_alert_sinks(policy: AlertPolicy) -> None:
    outbound = sorted(
        f"{sink.name} ({sink.kind})"
        for sink in policy.sinks
        if sink.enabled and sink.kind not in DEMO_ALERT_SINK_KINDS
    )
    if outbound:
        raise ValueError(
            f"ADG_RUNTIME_MODE=demo allows only log alert sinks; the alert policy enables "
            f"{', '.join(outbound)}. A demo deployment makes no outbound deliveries."
        )


def _parse_group_role_map(raw: str) -> dict[str, Role]:
    mapping: dict[str, Role] = {}
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        group, separator, role_name = candidate.rpartition(":")
        if not separator or not group.strip():
            raise ValueError(
                f"ADG_OIDC_GROUP_ROLE_MAP entry {candidate!r} is not '<group object id>:<role>'."
            )
        roles, unknown = parse_roles([role_name])
        if unknown or not roles:
            raise ValueError(
                f"ADG_OIDC_GROUP_ROLE_MAP entry {candidate!r} names unknown role "
                f"{role_name.strip()!r}. Valid roles: "
                f"{', '.join(sorted(role.value for role in Role))}."
            )
        mapping[group.strip().casefold()] = next(iter(roles))
    return mapping


def _parse_collector_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        key_id, separator, secret = candidate.partition(":")
        if not separator or not key_id.strip() or not secret.strip():
            raise ValueError(
                f"ADG_COLLECTOR_API_KEYS entry {key_id.strip()!r} is not '<key id>:<secret>'."
            )
        if key_id.strip() in keys:
            raise ValueError(
                f"ADG_COLLECTOR_API_KEYS names key id {key_id.strip()!r} more than once."
            )
        if len(secret.strip()) < MIN_COLLECTOR_KEY_LENGTH:
            raise ValueError(
                f"The collector key {key_id.strip()!r} is shorter than "
                f"{MIN_COLLECTOR_KEY_LENGTH} characters. Generate one with "
                "[Convert]::ToBase64String((1..32 | ForEach-Object "
                "{ Get-Random -Maximum 256 }))."
            )
        keys[key_id.strip()] = secret.strip()
    return keys


def _parse_expected_collectors(raw: str) -> tuple[str, ...]:
    from app.domain.observation import CollectorKind

    known = {kind.value for kind in CollectorKind}
    kinds: set[str] = set()
    for entry in raw.split(","):
        candidate = entry.strip().casefold()
        if not candidate:
            continue
        if candidate not in known:
            raise ValueError(
                f"ADG_EXPECTED_COLLECTORS names unknown collector {candidate!r}. Valid "
                f"collectors: {', '.join(sorted(known))}."
            )
        kinds.add(candidate)
    return tuple(sorted(kinds))


def build_settings(**overrides: Any) -> Settings:
    """Build settings from explicit values only, ignoring any local ``.env`` file.

    Tests use this so that a developer's local ``.env`` cannot change assertions. The
    private ``_env_file`` keyword is part of the pydantic-settings initializer but is not
    described by its type stubs.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that configuration is parsed and validated exactly once per process.
    Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
