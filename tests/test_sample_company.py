"""Try a sample company (#684).

Henry Brown's Auto Body Shop (IRS Publication 583) was a script only a
checkout could run. Companies -> Try a sample company now makes it as a
company file of its own, through the path + New Company takes, and fills
it from app/seed/irs_sample.py, the one source the script shares. The
sample is marked on the manifest and the picker, a second one is
numbered, and the company the request works in is never touched."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from app.main import app
from app.models.contacts import Customer, Vendor
from app.models.estimates import Estimate
from app.models.invoices import Invoice
from app.models.items import Item
from app.models.payments import Payment
from app.models.settings import Settings
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]
COUNTED = {
    "customers": Customer,
    "vendors": Vendor,
    "items": Item,
    "invoices": Invoice,
    "payments": Payment,
    "estimates": Estimate,
}
EXPECTED = {
    "customers": 8,
    "vendors": 13,
    "items": 8,
    "invoices": 10,
    "payments": 5,
    "estimates": 3,
}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "slowbooks-data"
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(d))
    return d


def _tables(session) -> dict:
    out = {k: session.query(m).count() for k, m in COUNTED.items()}
    out["transactions"] = session.query(Transaction).count()
    out["lines"] = session.query(TransactionLine).count()
    out["settings"] = sorted((s.key, s.value) for s in session.query(Settings).all())
    return out


def _as(client, role):
    r = client.post("/api/tokens", json={"label": f"as-{role}", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def test_try_a_sample_company_makes_henry_browns_books_in_a_file_of_their_own(
    client, data_dir, seed_accounts, db_session
):
    before = _tables(db_session)

    r = client.post("/api/companies/sample")
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["name"] == "Sample: Henry Brown's Auto Body Shop"
    assert made["file"] == "sample-henry-brown-s-auto-body-shop.db"
    path = data_dir / "companies" / made["file"]
    assert path.exists()

    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        with Session(engine) as s:
            got = {k: s.query(m).count() for k, m in COUNTED.items()}
            assert got == EXPECTED
            # dated in the current year, as the script always dated them
            years = {d.year for (d,) in s.query(Invoice.date).all()}
            assert years == {date.today().year}
            # every invoice and payment has its journal entry, and the
            # books balance
            assert (
                s.query(Invoice).filter(Invoice.transaction_id.is_(None)).count() == 0
            )
            assert (
                s.query(Payment).filter(Payment.transaction_id.is_(None)).count() == 0
            )
            debit, credit = s.query(
                func.sum(TransactionLine.debit), func.sum(TransactionLine.credit)
            ).one()
            assert debit == credit and debit > 0

            from app.routes.reports.financial import trial_balance

            tb = trial_balance(
                start_date=None,
                end_date=None,
                db=s,
                exclude_adjusting=False,
                basis="accrual",
                hide_zero=False,
            )
            assert tb["total_debit"] == tb["total_credit"] > 0
            assert tb["difference"] == 0

            settings = {x.key: x.value for x in s.query(Settings).all()}
            assert settings["company_name"] == "Henry Brown's Auto Body Shop"
            assert settings["company_city"] == "Anytown"
            assert not settings.get("company_tax_id")
    finally:
        engine.dispose()

    # the manifest and the list mark it a sample
    manifest = json.loads((data_dir / "companies.json").read_text())
    assert manifest["companies"] == [
        {
            "name": "Sample: Henry Brown's Auto Body Shop",
            "file": made["file"],
            "sample": True,
        }
    ]
    listed = client.get("/api/companies").json()
    assert [(c["name"], c["sample"]) for c in listed] == [
        ("Sample: Henry Brown's Auto Body Shop", True)
    ]

    # a second is numbered, and is a file of its own
    r = client.post("/api/companies/sample")
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Sample: Henry Brown's Auto Body Shop 2"
    assert r.json()["file"] == "sample-henry-brown-s-auto-body-shop-2.db"
    assert (data_dir / "companies" / r.json()["file"]).exists()

    # the company this request works in is untouched
    db_session.expire_all()
    assert _tables(db_session) == before


def test_a_plain_new_company_is_not_a_sample(client, data_dir):
    r = client.post("/api/companies", json={"name": "Acme Desktop"})
    assert r.status_code == 201, r.text
    assert [c["sample"] for c in client.get("/api/companies").json()] == [False]
    manifest = json.loads((data_dir / "companies.json").read_text())
    assert "sample" not in manifest["companies"][0]


def test_only_an_administrator_makes_a_sample(client, data_dir):
    for role in ("readonly", "bookkeeper"):
        r = _as(client, role).post("/api/companies/sample")
        assert r.status_code == 403, (role, r.status_code, r.text)
    assert not (data_dir / "companies").exists()


def test_the_script_and_the_route_share_one_source():
    script = (ROOT / "scripts/seed_irs_mock_data.py").read_text(encoding="utf-8")
    assert "from app.seed.irs_sample import" in script
    assert "VENDORS = [" not in script
    from app.seed import irs_sample

    assert len(irs_sample.CUSTOMERS) == 8 and len(irs_sample.VENDORS) == 13


def test_the_companies_page_offers_the_sample_beside_new_company():
    js = (ROOT / "app/static/js/companies.js").read_text(encoding="utf-8")
    button = (
        '${!ac.is_copy ? \'<button class="btn btn-secondary" data-admin data-write '
        "onclick=\"CompaniesPage.trySample(this)\">Try a sample company</button> ' : ''}"
    )
    assert button in js
    assert js.index(button) < js.index("+ New Company</button>")
    assert '${c.sample ? \' <span class="badge badge-draft" data-sample' in js
