r"""ADG-DEMO-02: the access question the demo opens with, as the dataset defines it.

Pure: no database. The seed names a principal and a folder and nothing about the answer, so
what is checked here is that it names the *right* ones — read off the authored ACL by the
same intents the estate placed them with, synthetic, and stable from one build to the next.
What ADG answers about the pair is pinned against a reset estate in
``tests/db/test_genesee_press_demo.py::TestTheAccessInvestigation``.
"""

from __future__ import annotations

import datetime as dt

from app.demo.genesee_press.canon import DOMAIN_SID, Canon, build_canon
from app.demo.genesee_press.dataset import GeneseePressDataset
from app.demo.genesee_press.estate import AceIntent, build_estate
from app.demo.genesee_press.generations import GENERATIONS
from app.demo.genesee_press.scenarios import APOLLO, SCENARIO_IDS, investigations
from app.demo_mode.datasets import dataset_investigations

CANON: Canon = build_canon()
FIRST_SCAN: dt.date = GENERATIONS[0].day


def test_there_is_one_and_it_asks_about_the_apollo_folder() -> None:
    [seed] = investigations(CANON)

    assert seed["id"] == "apollo"
    assert seed["resource"] == APOLLO
    assert seed["access_path"] == "remote_smb"
    assert seed["question"] == f"Why does {seed['person']['name']} have access to {APOLLO}?"
    assert set(seed["scenarios"]) <= set(SCENARIO_IDS)


def test_the_principal_is_the_historical_engineer_the_estate_named_on_that_acl() -> None:
    [seed] = investigations(CANON)
    estate = build_estate()
    folder = next(item for item in estate.folders if item.path == APOLLO)
    share = next(item for item in estate.shares if item.unc == folder.share)
    [entry] = [ace for ace in folder.explicit if ace.intent is AceIntent.HISTORICAL_ENGINEER]
    person = CANON.employees_by_id[entry.trustee]

    assert share.scenario == "C"
    assert seed["person"]["sid"] == person.sid
    assert person.is_active
    assert person.department == "engineering"


def test_the_orphan_is_a_former_employee_gone_before_adg_first_looked() -> None:
    """Which is why no collector could resolve the SID: it never saw the account."""
    [seed] = investigations(CANON)
    former = next(person for person in CANON.employees if person.sid == seed["former"]["sid"])

    assert not former.is_active
    assert former.termination_date is not None
    assert former.termination_date < FIRST_SCAN


def test_every_identifier_is_synthetic() -> None:
    [seed] = investigations(CANON)

    assert seed["person"]["sid"].startswith(f"{DOMAIN_SID}-")
    assert seed["former"]["sid"].startswith(f"{DOMAIN_SID}-")
    assert seed["person"]["email"].endswith("@geneseepress.example")
    assert APOLLO.startswith("\\\\GP-")


def test_it_is_deterministic() -> None:
    assert investigations(CANON) == investigations(build_canon())


def test_the_dataset_serves_it_through_the_demo_contract() -> None:
    [served] = dataset_investigations(GeneseePressDataset())
    [seed] = investigations(CANON)

    assert served.key == seed["id"]
    assert served.principal.kind == "person"
    assert served.principal.sid == seed["person"]["sid"]
    assert served.resource == APOLLO
    assert served.scenarios == ("C", "H")
