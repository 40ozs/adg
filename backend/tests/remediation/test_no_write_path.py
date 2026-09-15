r"""No ordinary API call can change Windows, proved four ways rather than asserted once.

This is the phase's central acceptance criterion, and it is the kind of claim that is easy to
believe and hard to keep. The four checks fail for four different reasons, and a future change
that broke the guarantee would trip at least one of them whether or not anybody remembered
this file existed.

**Structurally, from the syntax tree.** No module in ``app/api`` or ``app/remediation``
outside ``executor.py`` calls anything that applies a change. A route handler wired to an
executor fails here the moment it is written.

**By dependency.** ADG's installed packages contain nothing that can write a security
descriptor or a group membership. This is checked by name against the modules the application
imports, so a ``pywin32`` or an ``ldap3`` appearing in ``pyproject.toml`` and being imported
fails.

**By authorization.** ``remediation:execute`` is granted by no role, including the reserved
one that exists to hold its name — so even an execution route would be unreachable.

**By configuration.** The default execution mode is ``disabled``, production refuses anything
else, and the factory's fall-through is the refusing executor rather than a capable one.

And, because a structural test that could never fail is decoration, each guard is fed the
exact mistake it exists to prevent and required to catch it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.auth.roles import RESERVED_CAPABILITIES, ROLE_CAPABILITIES, Capability, Role
from app.config import Settings
from app.domain.remediation import ExecutionMode
from app.main import create_app
from app.models import schema
from app.remediation.executor import DisabledRemediator, remediator_for

# app/models/schema.py -> parents[1] is app/.
APP_ROOT = pathlib.Path(schema.__file__).resolve().parents[1]
API_ROOT = APP_ROOT / "api"
REMEDIATION_ROOT = APP_ROOT / "remediation"

#: The one module allowed to define or invoke an execution path. It is where the refusal
#: lives and where the fixture-backed lab adapter lives, and both are exercised by
#: ``tests/remediation/test_executor.py``.
EXECUTOR_MODULE = REMEDIATION_ROOT / "executor.py"

#: Method names that would carry a change out. ``apply`` is the interface's own; the others
#: are what somebody reaching for a shortcut would plausibly call it instead.
#:
#: ``execute`` is deliberately **not** here. Every repository in ADG calls
#: ``session.execute``, so a guard including it would fire on almost every module and would
#: be switched off within a week -- and a guard nobody can leave on protects nothing.
EXECUTION_CALLS = frozenset({"apply", "remediate", "perform_change", "apply_change"})

#: Receiver names that make an ``apply`` an execution. ``CacheValidator.apply(response)`` in
#: ``app/api/caching.py`` is an unrelated method of the same name, and a check that flagged it
#: would either be suppressed or would force a rename of a Phase 6 API for this phase's
#: convenience. Scoping to the receiver keeps the guard narrow enough to stay on.
EXECUTOR_RECEIVERS = frozenset(
    {"remediator", "_remediator", "executor", "_executor", "adapter", "_adapter"}
)

#: What a module other than the executor may import from it. Everything here is safe:
#: ``describe`` is the half of the interface that cannot write, ``DisabledRemediator`` refuses,
#: and ``Remediator`` is a type. :class:`~app.remediation.executor.LabRemediator` is absent on
#: purpose -- it is the only class in ADG that mutates anything on being asked to, and nothing
#: outside its own tests may reach for it.
IMPORTABLE_FROM_EXECUTOR = frozenset(
    {
        "DISABLED_MESSAGE",
        "ExecutionOutcome",
        "ExecutorDescription",
        "Remediator",
        "remediator_for",
        "DisabledRemediator",
    }
)

#: Anything that could write to Windows or a directory. None of these is a dependency of ADG;
#: the list exists so that adding one and importing it fails a test rather than passing review.
FORBIDDEN_IMPORTS = frozenset(
    {
        "win32security",
        "win32net",
        "win32api",
        "ntsecuritycon",
        "pywintypes",
        "ldap3",
        "ldap",
        "smbprotocol",
        "smbclient",
        "impacket",
        "subprocess",
        "winreg",
    }
)


def modules_under(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in root.rglob("*.py") if path != EXECUTOR_MODULE)


def _receiver_names(node: ast.expr) -> set[str]:
    """Every identifier in the expression a call is made on.

    ``self._executor.apply(...)`` yields ``{"self", "_executor"}``, and
    ``remediator_for(mode).apply(...)`` yields ``{"remediator_for", "mode"}`` -- so both
    spellings of the mistake are recognized without the check needing to know a type.
    """
    return {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Name | ast.Attribute)
    }


def execution_calls_in(source: str) -> list[tuple[str, int]]:
    """Every call in the module that would carry out a change.

    A call counts when its name is one of :data:`EXECUTION_CALLS` **and** either it is a bare
    function call or its receiver mentions something executor-shaped. Narrowing on the
    receiver is what keeps the guard usable: ``apply`` is a common enough method name that a
    check ignoring the receiver fires on unrelated code, and a guard that fires on unrelated
    code is one somebody turns off.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            if function.id in EXECUTION_CALLS:
                found.append((function.id, node.lineno))
            continue
        if not isinstance(function, ast.Attribute) or function.attr not in EXECUTION_CALLS:
            continue
        names = _receiver_names(function.value)
        if names & EXECUTOR_RECEIVERS or "remediator_for" in names:
            found.append((function.attr, node.lineno))
    return found


def executor_imports_in(source: str) -> set[str]:
    """What the module imports from :mod:`app.remediation.executor`."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "app.remediation.executor":
            names.update(alias.name for alias in node.names)
    return names


def imports_in(source: str) -> set[str]:
    """Every top-level package name the module imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


class TestNoCodePathCarriesOutAChange:
    def test_the_roots_are_where_this_test_thinks_they_are(self) -> None:
        """A guard whose glob silently matched nothing would pass forever."""
        assert API_ROOT.is_dir()
        assert REMEDIATION_ROOT.is_dir()
        assert EXECUTOR_MODULE.is_file()
        assert len(modules_under(REMEDIATION_ROOT)) >= 7

    @pytest.mark.parametrize("module", modules_under(REMEDIATION_ROOT), ids=lambda path: path.name)
    def test_no_remediation_module_but_the_executor_applies_anything(
        self, module: pathlib.Path
    ) -> None:
        offenders = [
            f"{module.name}:{line} {name}(...)"
            for name, line in execution_calls_in(module.read_text(encoding="utf-8"))
        ]

        assert not offenders, (
            "A remediation module calls something that would carry out a change:\n  "
            + "\n  ".join(offenders)
            + "\nADG describes changes and makes none. The interface lives in "
            "app/remediation/executor.py and every deployment gets DisabledRemediator, "
            "which refuses. See ADR-0035 and docs/architecture/remediation.md section 8."
        )

    @pytest.mark.parametrize("module", modules_under(API_ROOT), ids=lambda path: path.name)
    def test_no_route_module_applies_anything(self, module: pathlib.Path) -> None:
        offenders = [
            f"{module.name}:{line} {name}(...)"
            for name, line in execution_calls_in(module.read_text(encoding="utf-8"))
        ]

        assert not offenders, (
            "An API module calls something that would carry out a change:\n  "
            + "\n  ".join(offenders)
            + "\nNo HTTP request may mutate Windows or Active Directory."
        )

    def test_the_guard_catches_a_planted_violation(self) -> None:
        """A structural test that could never fail is decoration. This feeds it the exact
        mistake it exists to prevent."""
        violation = "await remediator_for(mode).apply(plan, change)"

        assert execution_calls_in(violation) == [("apply", 1)]

    def test_the_guard_sees_through_an_indirect_receiver(self) -> None:
        """``self._executor.apply`` is the spelling a route handler would actually use."""
        assert execution_calls_in("await self._executor.apply(plan, change)") == [("apply", 1)]

    def test_the_guard_ignores_an_unrelated_method_of_the_same_name(self) -> None:
        """``CacheValidator.apply(response)`` is not remediation, and a guard that said it
        was would be switched off."""
        assert execution_calls_in("validator.apply(response)") == []

    def test_the_guard_ignores_a_database_execute(self) -> None:
        """Every repository in ADG calls this."""
        assert execution_calls_in("await self._session.execute(statement)") == []

    @pytest.mark.parametrize(
        "module",
        modules_under(REMEDIATION_ROOT) + modules_under(API_ROOT),
        ids=lambda path: path.name,
    )
    def test_nothing_reaches_for_the_lab_adapter(self, module: pathlib.Path) -> None:
        """The import guard, which has more teeth than the call guard.

        :class:`~app.remediation.executor.LabRemediator` is the only class in ADG that
        mutates anything when asked to. Nothing outside its own tests may import it, so a
        route that tried to construct one fails here before the question of what it then
        called with it ever arises.
        """
        imported = executor_imports_in(module.read_text(encoding="utf-8"))
        forbidden = imported - IMPORTABLE_FROM_EXECUTOR

        assert not forbidden, (
            f"{module.name} imports {', '.join(sorted(forbidden))} from the executor module. "
            "Only the refusing executor, its description and the factory may be imported "
            "outside app/remediation/executor.py."
        )

    def test_the_import_guard_catches_a_planted_import(self) -> None:
        planted = "from app.remediation.executor import LabRemediator"

        assert executor_imports_in(planted) - IMPORTABLE_FROM_EXECUTOR == {"LabRemediator"}


class TestNothingImportedCanWriteToWindows:
    @pytest.mark.parametrize(
        "module",
        modules_under(REMEDIATION_ROOT) + modules_under(API_ROOT),
        ids=lambda path: path.name,
    )
    def test_no_module_imports_a_directory_or_descriptor_client(self, module: pathlib.Path) -> None:
        offending = imports_in(module.read_text(encoding="utf-8")) & FORBIDDEN_IMPORTS

        assert not offending, (
            f"{module.name} imports {', '.join(sorted(offending))}, which could write to "
            "Windows or a directory. ADG's collectors are read-only PowerShell and the "
            "backend touches neither."
        )

    def test_the_executor_itself_imports_none_of_them_either(self) -> None:
        """The module that is *allowed* to define an execution path still has nothing to
        execute with. The lab adapter mutates a dictionary."""
        offending = imports_in(EXECUTOR_MODULE.read_text(encoding="utf-8")) & FORBIDDEN_IMPORTS

        assert not offending

    def test_the_guard_catches_a_planted_import(self) -> None:
        assert imports_in("import win32security") & FORBIDDEN_IMPORTS == {"win32security"}


class TestTheApiExposesNoExecutionRoute:
    def test_no_path_offers_to_carry_a_plan_out(self) -> None:
        """Read off the OpenAPI document -- what the application actually serves -- rather
        than a list of routes somebody maintains."""
        spec = create_app().openapi()

        suspicious = [
            path
            for path in spec["paths"]
            if any(word in path for word in ("/execute", "/apply", "/remediate"))
        ]

        assert not suspicious, (
            f"The API serves {suspicious}, which reads as a path that carries out a change. "
            "ADG has no such route; adding one requires everything in "
            "docs/architecture/remediation.md section 8."
        )

    def test_every_remediation_route_is_a_read_or_a_document(self) -> None:
        """The most dangerous thing any remediation route produces is a signed document."""
        spec = create_app().openapi()
        remediation = {
            path: set(methods)
            for path, methods in spec["paths"].items()
            if path.startswith("/api/v1/remediation")
        }

        assert remediation, "the remediation router is not mounted"
        for path, methods in remediation.items():
            assert methods <= {"get", "post", "put"}, (path, methods)


class TestNoRoleCanExecute:
    def test_the_capability_is_granted_by_nothing(self) -> None:
        reachable = frozenset().union(*ROLE_CAPABILITIES.values())

        assert Capability.REMEDIATION_EXECUTE not in reachable

    def test_it_is_still_declared_reserved(self) -> None:
        assert Capability.REMEDIATION_EXECUTE in RESERVED_CAPABILITIES

    def test_the_remediator_role_grants_nothing_at_all(self) -> None:
        """Holding it must be indistinguishable from holding no role, so that a tenant can
        provision the name ahead of a feature that does not exist."""
        assert ROLE_CAPABILITIES[Role.REMEDIATOR] == frozenset()


class TestTheDefaultConfigurationRefuses:
    def test_execution_is_disabled_out_of_the_box(self) -> None:
        assert Settings().remediation_execution_mode == "disabled"

    def test_the_default_deployment_gets_the_refusing_executor(self) -> None:
        settings = Settings()

        remediator = remediator_for(
            ExecutionMode(settings.remediation_execution_mode),
            environment=settings.environment,
            fixture_path=settings.remediation_lab_fixture_path,
        )

        assert isinstance(remediator, DisabledRemediator)

    def test_there_is_no_execution_mode_naming_a_real_adapter(self) -> None:
        """Shipping a write path is an adapter, a credential and a capability grant. A
        setting that could switch one on would mean the write path already existed."""
        assert {mode.value for mode in ExecutionMode} == {"disabled", "lab"}

    def test_production_refuses_anything_but_disabled(self) -> None:
        with pytest.raises(ValueError, match="cannot be used with ADG_ENVIRONMENT=production"):
            Settings(
                environment="production",
                remediation_execution_mode="lab",
                remediation_lab_fixture_path="fixture.json",
                database_url="postgresql+psycopg://adg:secret@db.example.test:5432/adg",
                auth_mode="oidc",
                oidc_issuer="https://login.example.test/v2.0",
                oidc_audience="api://adg",
                oidc_jwks_url="https://login.example.test/keys",
            )
