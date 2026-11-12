"""A 1099 vendor paid $0.00 in the year is off the Tax Forms list and its
picker (#813): it has no form to file. `unpaid` counts them, so the picker
can say "No 1099-NEC vendor was paid in 2026" rather than telling the
person to mark vendors who are marked already."""

from pathlib import Path

from tests.test_register_vendor_link import _post

ROOT = Path(__file__).resolve().parents[1]


def test_an_unpaid_1099_vendor_is_not_listed(client, seed_accounts):
    paid = client.post(
        "/api/vendors", json={"name": "Paid Sub", "is_1099_vendor": True}
    ).json()
    unpaid = client.post(
        "/api/vendors", json={"name": "Idle Sub", "is_1099_vendor": True}
    ).json()
    _post(client, seed_accounts, payee="Paid Sub", amount="-250.00")
    data = client.get("/api/tax-forms/1099?year=2026").json()
    assert [v["vendor_id"] for v in data["vendors"]] == [paid["id"]]
    assert data["unpaid"] == 1
    # a year nobody was paid in lists none, and says how many were left off
    data = client.get("/api/tax-forms/1099?year=2024").json()
    assert data["vendors"] == [] and data["unpaid"] == 2
    assert unpaid["id"] not in [v["vendor_id"] for v in data["vendors"]]


def test_the_picker_says_nobody_was_paid():
    js = (ROOT / "app/static/js/tax_forms.js").read_text(encoding="utf-8")
    assert "No 1099-NEC vendor was paid in ${escapeHtml(String(data.year))}" in js
    assert "data.unpaid" in js
