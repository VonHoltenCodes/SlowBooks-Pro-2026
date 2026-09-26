"""The customer form: default terms, credit limit, tax exemption, active.

Explore 2.17.3:
- macbase1 F5 / S-d: with Settings → Default terms = Net 15, new customers
  were created Net 30 (the form and the API both hard-coded it).
- skytech W-L4: a negative credit limit (−500) was accepted.
- skytech W-L20: customers carry is_taxable (2.16.2 made it drive invoice
  tax), but the form never showed it, so a church or a reseller could not
  be marked exempt from the screen.
- skytech W-M16: the "Active/Inactive" badge existed but nothing on screen
  set it, so a customer could never be retired from the pick lists.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _read(name):
    return (JS / name).read_text(encoding="utf-8")


def _form():
    js = _read("customers.js")
    return js[js.index("async showForm(id = null)") : js.index("_confirmDuplicate(")]


def test_a_new_customer_gets_the_company_default_terms(client, seed_accounts):
    assert (
        client.put("/api/settings", json={"default_terms": "Net 15"}).status_code == 200
    )
    r = client.post("/api/customers", json={"name": "Harbor Light Bakery"})
    assert r.status_code == 201, r.text
    assert r.json()["terms"] == "Net 15"
    # an explicit choice still wins
    r = client.post(
        "/api/customers", json={"name": "Tidewater Cafe", "terms": "Due on Receipt"}
    )
    assert r.json()["terms"] == "Due on Receipt"


def test_the_new_customer_form_starts_on_the_default_terms():
    form = _form()
    assert "API.get('/settings')" in form
    assert "c.terms = settings.default_terms" in form
    # a customer on terms outside the fixed list keeps them when edited
    assert "termChoices.unshift(c.terms)" in form


def test_a_negative_credit_limit_is_refused(client, seed_accounts):
    r = client.post(
        "/api/customers", json={"name": "Salt & Pine", "credit_limit": -500}
    )
    assert r.status_code == 422, r.text
    assert "credit limit can't be negative" in r.text
    r = client.post("/api/customers", json={"name": "Salt & Pine", "credit_limit": 500})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.put(f"/api/customers/{cid}", json={"credit_limit": -1})
    assert r.status_code == 422, r.text
    # clearing it means no limit
    r = client.put(f"/api/customers/{cid}", json={"credit_limit": None})
    assert r.status_code == 200 and r.json()["credit_limit"] is None
    form = _form()
    assert 'name="credit_limit" type="number" step="0.01" min="0"' in form
    assert "data.credit_limit = null" in form


def test_the_form_sets_and_the_page_shows_tax_exempt(client, seed_accounts):
    form = _form()
    assert 'name="tax_exempt"' in form
    assert "c.is_taxable === false ? 'checked' : ''" in form
    assert (
        "data.is_taxable = !(e.target.tax_exempt && e.target.tax_exempt.checked)"
        in form
    )
    assert "delete data.tax_exempt" in form
    page = _read("customers.js")
    details = page[page.index("async showDetails(") : page.index("async _saveNotes(")]
    assert "customer.is_taxable === false" in details and "Tax exempt" in details

    # what the box sets is what invoices honour (2.16.2)
    cid = client.post("/api/customers", json={"name": "First Church"}).json()["id"]
    assert (
        client.put(f"/api/customers/{cid}", json={"is_taxable": False}).status_code
        == 200
    )
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0.0825,
            "lines": [{"description": "Banner", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    assert float(r.json()["tax_amount"]) == 0.0


def test_a_customer_can_be_made_inactive_and_leaves_the_pick_lists(
    client, seed_accounts
):
    form = _form()
    assert 'name="is_active"' in form
    assert "data.is_active = e.target.is_active.checked" in form
    # only the edit form has it (the create schema refuses unknown fields)
    assert '${id ? `<div class="form-group"><label>Status</label>' in form

    cid = client.post("/api/customers", json={"name": "Old Client"}).json()["id"]
    r = client.put(f"/api/customers/{cid}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    picked = [c["id"] for c in client.get("/api/customers?active_only=true").json()]
    assert cid not in picked
    assert cid in [c["id"] for c in client.get("/api/customers").json()]

    # every document form picks from active customers only
    for name in (
        "payments.js",
        "invoices.js",
        "estimates.js",
        "sales_receipts.js",
        "credit_memos.js",
        "recurring.js",
    ):
        assert "/customers?active_only=true" in _read(name), name
    # and the Customer Center marks the inactive ones
    center = _read("customers.js")
    assert "c.is_active === false ? ' <span" in center
