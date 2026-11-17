"""A check prints the vendor's company name when one is set (#1004): the
payee line, the address block and the batch list; a vendor without one
prints its name."""

from pathlib import Path

from app.routes.checks import vendor_payee
from tests.test_register_check_print import _pdf_text

ROOT = Path(__file__).resolve().parents[1]


class _V:
    def __init__(self, name, company=None):
        self.name, self.company = name, company


def test_the_payee_is_the_company_name_when_set():
    assert vendor_payee(_V("RILEYS", "RILEY'S JERKY")) == "RILEY'S JERKY"
    assert vendor_payee(_V("PONY EXPRESS", "  ")) == "PONY EXPRESS"
    assert vendor_payee(_V("Corner Hardware")) == "Corner Hardware"
    assert vendor_payee(None) == ""


def test_an_expense_check_prints_the_company_name(client, seed_accounts):
    v = client.post(
        "/api/vendors",
        json={
            "name": "RILEYS",
            "company": "RILEY'S JERKY",
            "address1": "12 Smoke Rd",
            "city": "Elko",
            "state": "NV",
            "zip": "89801",
        },
    )
    assert v.status_code == 201, v.text
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "vendor_id": v.json()["id"],
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "48.00",
            "reference": "2201",
            "memo": "jerky",
        },
    )
    assert r.status_code == 201, r.text
    text = _pdf_text(client.get(f"/api/checks/print?transaction_id={r.json()['id']}"))
    assert "RILEY'S JERKY" in text
    assert "RILEYS" not in text.replace("RILEY'S", "")


def test_bill_payment_checks_and_the_batch_list_read_the_same_name():
    # the single bill-payment check and the batch list build their payee
    # through vendor_payee too
    src = (ROOT / "app/routes/checks.py").read_text(encoding="utf-8")
    assert '"payee": vendor_payee(vendor) if vendor else "Unknown",' in src
    assert "v.id: vendor_payee(v)" in src
    assert "vendor.name if vendor else" not in src
