"""The Jobs page and the Reseller Permit form offer active customers
(explore 2.17.3 integration, I20).

Both asked for every customer — inactive ones too — so a customer made
inactive kept turning up in the job form's Customer list, the Jobs
filter, and the permit form's list (the permit form's vendors likewise).
The pickers now ask for active customers and vendors; the one a job or a
permit already names stays listed, marked inactive, so an edit never
drops it or moves the record to whoever is first. The Jobs filter offers
the customers the jobs belong to, an inactive customer's included.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pickers():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "customer_pickers_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


ACTIVE = [["6", "Acme", False], ["7", "Bravo Bakery", False]]


def test_no_picker_asks_for_every_customer_or_vendor(pickers):
    assert "/customers" not in pickers["asked"]
    assert "/vendors" not in pickers["asked"]


def test_the_job_form_lists_active_customers(pickers):
    assert pickers["newJob"] == ACTIVE


def test_a_jobs_own_inactive_customer_stays_on_its_form(pickers):
    assert pickers["editJob"] == ACTIVE + [["5", "Old Diner (inactive)", True]]
    # a new job started from an inactive customer's page
    assert pickers["newJobForInactive"] == ACTIVE + [
        ["5", "Old Diner (inactive)", True]
    ]


def test_the_jobs_filter_offers_the_customers_the_jobs_belong_to(pickers):
    assert pickers["jobFilter"] == [["6", "Acme", False], ["5", "Old Diner", False]]


def test_the_permit_form_lists_active_customers_and_vendors(pickers):
    assert pickers["newPermit"] == {
        "customers": [["6", "Acme", True], ["7", "Bravo Bakery", False]],
        "vendors": [["9", "Paper Co", False]],
    }


def test_a_permits_own_inactive_holder_stays_on_its_form(pickers):
    assert pickers["editCustomerPermit"] == {
        "customers": ACTIVE + [["5", "Old Diner (inactive)", True]],
        "vendors": [["9", "Paper Co", False]],
    }
    assert pickers["editVendorPermit"] == {
        "customers": ACTIVE,
        "vendors": [["9", "Paper Co", False], ["10", "Gone Supply (inactive)", True]],
    }


def test_active_only_leaves_out_an_inactive_customer(client, db_session):
    from app.models.contacts import Customer

    db_session.add_all(
        [
            Customer(name="Acme", is_active=True),
            Customer(name="Old Diner", is_active=False),
        ]
    )
    db_session.commit()
    names = [c["name"] for c in client.get("/api/customers?active_only=true").json()]
    assert names == ["Acme"]
