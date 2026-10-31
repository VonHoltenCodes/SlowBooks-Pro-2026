"""A vendor's W-9 on file (#257, 2.22.2).

`Vendor.w9_on_file` was on the model, `compute_1099_data` put it on every
row and the 1099-NEC printed "W-9 on file: Yes/No" from it, but the vendor
schema and form never had it: nothing could set it, so every 1099 printed
No. Now the vendor form has it next to the 1099 question, the vendor page
shows it, and the 1099 Summary shows each vendor's and counts the reportable
vendors without one.
"""

from app.services.form_1099 import compute_1099_data

JS = "app/static/js/"


def _vendor(client, name, **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, vendor_id, amount, accounts):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": "2026-03-10",
            "lines": [
                {
                    "description": "Work",
                    "account_id": accounts["6000"].id,
                    "quantity": 1,
                    "rate": str(amount),
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": "2026-03-20",
            "amount": str(amount),
            "pay_from_account_id": accounts["1000"].id,
            "allocations": [{"bill_id": bill["id"], "amount": str(amount)}],
        },
    )
    assert r.status_code == 201, r.text


def test_the_flag_can_be_set_and_cleared(client, seed_accounts):
    v = _vendor(
        client,
        "Harbor Supply",
        is_1099_vendor=True,
        vendor_1099_type="NEC",
        w9_on_file=True,
    )
    assert v["w9_on_file"] is True
    assert client.get(f"/api/vendors/{v['id']}").json()["w9_on_file"] is True
    r = client.put(f"/api/vendors/{v['id']}", json={"w9_on_file": False})
    assert r.status_code == 200 and r.json()["w9_on_file"] is False
    # left alone by an edit that doesn't mention it
    client.put(f"/api/vendors/{v['id']}", json={"w9_on_file": True})
    r = client.put(f"/api/vendors/{v['id']}", json={"terms": "Net 15"})
    assert r.json()["w9_on_file"] is True
    assert _vendor(client, "Pier Lumber")["w9_on_file"] is False


def test_the_1099_nec_and_summary_say_who_has_one(client, db_session, seed_accounts):
    with_w9 = _vendor(
        client,
        "Harbor Supply",
        is_1099_vendor=True,
        vendor_1099_type="NEC",
        w9_on_file=True,
    )
    without = _vendor(
        client, "Pier Lumber", is_1099_vendor=True, vendor_1099_type="NEC"
    )
    small = _vendor(
        client, "Corner Hardware", is_1099_vendor=True, vendor_1099_type="NEC"
    )
    _pay(client, with_w9["id"], 2700, seed_accounts)
    _pay(client, without["id"], 2600, seed_accounts)
    _pay(
        client, small["id"], 100, seed_accounts
    )  # under the 2026 threshold ($2,000): not reportable
    rows = {d["name"]: d for d in compute_1099_data(db_session, 2026)}
    assert (
        rows["Harbor Supply"]["w9_on_file"] is True
        and rows["Harbor Supply"]["reportable"]
    )
    assert (
        rows["Pier Lumber"]["w9_on_file"] is False and rows["Pier Lumber"]["reportable"]
    )
    s = client.get("/api/reports/1099-summary", params={"year": 2026}).json()
    by_name = {i["vendor_name"]: i for i in s["items"]}
    assert by_name["Harbor Supply"]["w9_on_file"] is True
    assert by_name["Pier Lumber"]["w9_on_file"] is False
    assert s["vendors_above_threshold"] == 2
    assert s["vendors_missing_w9"] == 1  # Pier Lumber; Corner Hardware isn't reportable


def test_the_form_the_page_and_the_report_show_it():
    vendors = open(JS + "vendors.js", encoding="utf-8").read()
    assert '<select name="w9_on_file"' in vendors
    assert "data.w9_on_file = data.w9_on_file === 'true'" in vendors
    assert "W-9 ${vendor.w9_on_file ? 'on file'" in vendors
    reports = open(JS + "reports.js", encoding="utf-8").read()
    assert '<th scope="col">W-9</th>' in reports
    assert "No W-9" in reports and "with no W-9" in reports
