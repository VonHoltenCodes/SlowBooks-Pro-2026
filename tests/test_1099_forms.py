"""1099 vendors reach the 1099-NEC and 1096 (2.17.3 exploratory test,
macbase1 F19).

The vendor form's "1099 Vendor: Yes" sets is_1099_vendor, which the 1099
Summary report read — but the 1099-NEC / 1096 data read is_1099_eligible,
which nothing sets, so it listed no vendors at all. No screen linked to the
1099-NEC or 1096 PDFs either.
"""

import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _vendor(client, name, **flags):
    r = client.post("/api/vendors", json={"name": name, **flags})
    assert r.status_code == 201, r.text
    return r.json()


def _paid(client, seed_accounts, vendor, amount, day):
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": f"{vendor['id']}-{day}",
            "date": f"2026-05-{day:02d}",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "Sign installs",
                    "quantity": 1,
                    "rate": amount,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": f"2026-05-{day:02d}",
            "amount": amount,
            "allocations": [{"bill_id": bill.json()["id"], "amount": amount}],
        },
    )
    assert pay.status_code == 201, pay.text


@pytest.fixture
def vendors(client, seed_accounts):
    nec = _vendor(
        client, "Blue Heron Installs", is_1099_vendor=True, vendor_1099_type="NEC"
    )
    misc = _vendor(
        client, "Dock Rent LLC", is_1099_vendor=True, vendor_1099_type="MISC"
    )
    plain = _vendor(client, "Big Box Supply")
    _paid(client, seed_accounts, nec, 700, 1)
    _paid(client, seed_accounts, misc, 900, 2)
    _paid(client, seed_accounts, plain, 800, 3)
    return nec, misc, plain


def test_a_vendor_flagged_on_the_form_reaches_the_1099_nec_and_1096(client, vendors):
    nec, misc, plain = vendors
    summary = client.get("/api/reports/1099-summary?year=2026").json()
    assert {i["vendor_name"] for i in summary["items"]} == {
        "Blue Heron Installs",
        "Dock Rent LLC",
    }
    data = client.get("/api/tax-forms/1099?year=2026").json()
    # 1099-NEC is for nonemployee compensation: a MISC vendor is not on it
    assert [v["name"] for v in data["vendors"]] == ["Blue Heron Installs"]
    assert Decimal(str(data["vendors"][0]["total_paid"])) == Decimal("700.00")
    assert data["vendors"][0]["reportable"] is True
    assert data["transmittal"]["form_count"] == 1
    assert Decimal(str(data["transmittal"]["total_amount"])) == Decimal("700.00")


def test_the_forms_name_the_company_in_settings_as_payer(client, vendors, monkeypatch):
    from app.services import form_1099

    monkeypatch.setattr(form_1099, "render_pdf", lambda html, **kw: html.encode())
    client.put("/api/settings", json={"company_name": "Explore Signs & Co"})
    nec = vendors[0]
    r = client.get(f"/api/tax-forms/1099/{nec['id']}/pdf?year=2026")
    assert r.status_code == 200, r.text
    assert "Blue Heron Installs" in r.text
    assert "Explore Signs &amp; Co" in r.text and "My Company" not in r.text
    r = client.get("/api/tax-forms/1096/pdf?year=2026")
    assert r.status_code == 200, r.text
    assert "Explore Signs &amp; Co" in r.text


def test_the_tax_forms_page_has_1099_nec_and_1096_buttons():
    js = (JS / "tax_forms.js").read_text(encoding="utf-8")
    assert 'onclick="TaxFormsPage.generate1099()"' in js
    assert 'onclick="TaxFormsPage.generate1096()"' in js
    assert "_openForm(`/api/tax-forms/1099/${vendorId}/pdf?year=${year}`)" in js
    assert "_openForm(`/api/tax-forms/1096/pdf?year=${year}`)" in js
    assert "API.get(`/tax-forms/1099?year=${year}`)" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_in_the_desktop_window_the_1099_forms_reach_the_pdf_viewer():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "tax_forms_shim_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert (
        "1099-NEC: GET /api/tax-forms/1099/4/pdf?year=2026 -> native PDF viewer"
        in out.stdout
    ), out.stdout
    assert (
        "1096: GET /api/tax-forms/1096/pdf?year=2026 -> native PDF viewer" in out.stdout
    )


def test_a_1099_type_is_cleared_when_the_vendor_is_not_a_1099_vendor(client):
    v = _vendor(client, "Walk-in Welder", is_1099_vendor=False, vendor_1099_type="NEC")
    assert v["vendor_1099_type"] is None
    v = _vendor(client, "Crane Hire", is_1099_vendor=True, vendor_1099_type="NEC")
    r = client.put(f"/api/vendors/{v['id']}", json={"is_1099_vendor": False})
    assert r.status_code == 200, r.text
    assert r.json()["vendor_1099_type"] is None
    r = client.put(f"/api/vendors/{v['id']}", json={"vendor_1099_type": "MISC"})
    assert r.json()["vendor_1099_type"] is None
    r = client.put(
        f"/api/vendors/{v['id']}",
        json={"is_1099_vendor": True, "vendor_1099_type": "MISC"},
    )
    assert r.json()["vendor_1099_type"] == "MISC"
