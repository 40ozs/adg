r"""The risk configuration a Genesee Press operator would have written.

ADG does not decide what is sensitive (ADR-0024); an operator declares it. A demo whose
sensitive-resource rule reported nothing because nobody had declared anything would be
demonstrating a configuration gap rather than the rule, so Demo Mode uses this declaration
when ``ADG_RISK_CONFIGURATION_PATH`` is not set. Every severity stays at its shipped value.

One threshold is moved, and it is the kind of tuning a real operator makes in the first
week. ``redundant_access_paths`` reports every person who reaches a folder through two or
more trustees on its ACL. At Genesee Press every share migrated to the 2023 standard carries
its legacy group *and* its ``sec-share-*`` replacement on purpose until the migration
finishes, so two routes is the expected state of most of the estate and reporting it would
bury everything else under ~175,000 informational findings. The operator raises the bar to
four routes: the people reported are the ones the migration did not explain.
"""

from __future__ import annotations

from typing import Any, Final

from app.risk_engine.configuration import RiskConfiguration, parse_configuration

__all__ = ["DEMO_RISK_CONFIGURATION", "DEMO_RISK_DOCUMENT"]

DEMO_RISK_DOCUMENT: Final[dict[str, Any]] = {
    "version": "genesee-press-v1",
    "rules": {"redundant_access_paths": {"options": {"minimum_paths": 4}}},
    "sensitive_resources": [
        {"label": "Finance", "path_prefix": r"\\GP-FIN-FS01\Finance"},
        {"label": "Payroll", "path_prefix": r"\\GP-FIN-FS01\Payroll"},
        {"label": "Finance planning", "path_prefix": r"\\GP-FIN-FS02\Finance-Planning"},
        {"label": "Treasury", "path_prefix": r"\\GP-FIN-FS02\Treasury"},
        {"label": "People / HR", "path_prefix": r"\\GP-HR-FS01\People"},
        {"label": "Legal", "path_prefix": r"\\GP-LEGAL-FS01\Legal"},
        {"label": "Legal matters", "path_prefix": r"\\GP-LEGAL-FS02\Legal-Matters"},
        {"label": "Executive", "path_prefix": r"\\GP-EXEC-FS01\Executive"},
        {"label": "Board", "path_prefix": r"\\GP-EXEC-FS01\Board"},
        {"label": "Security incidents", "path_prefix": r"\\GP-SEC-FS01\Incidents"},
        {"label": "Sales commissions", "path_prefix": r"\\GP-SALES-FS01\Commissions"},
    ],
}

DEMO_RISK_CONFIGURATION: Final[RiskConfiguration] = parse_configuration(DEMO_RISK_DOCUMENT)
