"""The register's payee links the entry to a vendor (#590, from discussion
#561). A payee that is exactly a vendor's name, or a vendor_id sent with the
entry, names the vendor on the entry (Transaction.vendor_id, as a bank-feed
line does, #491), so it shows on the vendor's payment history (#569). A
payee no vendor has stays text. The page side is source-checked here; the
browser walks the suggestion box in test_browser_register_vendor.py."""

from datetime import date
from decimal import Decimal
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)


def _post(client, seed_accounts, **extra):
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-09-10",
        "amount": "-120.00",
        "category_account_id": seed_accounts["6000"].id,
        **extra,
    }
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client, name="Pine Street Lumber"):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _history(client, vendor):
    r = client.get(
        f"/api/vendors/{vendor['id']}/payment-history",
        params={"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_an_exact_name_links_the_entry_ignoring_case_and_spaces(client, seed_accounts):
    v = _vendor(client)
    out = _post(client, seed_accounts, payee="  pine street LUMBER ")
    assert out["vendor_id"] == v["id"] and out["vendor_name"] == v["name"]


def test_an_explicit_vendor_id_links_whatever_the_payee_says(client, seed_accounts):
    v = _vendor(client)
    out = _post(client, seed_accounts, payee="PSL check", vendor_id=v["id"])
    assert out["vendor_id"] == v["id"]
    assert out["payee"] == "PSL check"


def test_a_payee_that_is_not_a_vendor_stays_text(client, seed_accounts):
    _vendor(client)
    out = _post(client, seed_accounts, payee="Pine Street")  # not exact
    assert out["vendor_id"] is None and out["vendor_name"] is None
    out = _post(client, seed_accounts)  # no payee at all
    assert out["vendor_id"] is None


def test_an_unknown_vendor_id_is_a_404_and_posts_nothing(client, seed_accounts):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-09-10",
            "amount": "-5.00",
            "category_account_id": seed_accounts["6000"].id,
            "vendor_id": 99999,
        },
    )
    assert r.status_code == 404


def test_the_vendors_payment_history_lists_the_register_entry(client, seed_accounts):
    v = _vendor(client)
    linked = _post(client, seed_accounts, payee="Pine Street Lumber")
    _post(client, seed_accounts, payee="Someone Else")
    rows = _history(client, v)["rows"]
    assert [(r["kind"], r["amount"]) for r in rows] == [("bank_entry", "120.00")]
    assert rows[0]["id"] == linked["id"]


def test_editing_the_payee_follows_the_name_and_an_explicit_id_wins(
    client, seed_accounts
):
    a = _vendor(client, "Alpha Supply")
    b = _vendor(client, "Beta Tools")
    out = _post(client, seed_accounts, payee="Alpha Supply")
    url = f"/api/banking/entries/{out['id']}"
    assert client.get(url).json()["vendor_id"] == a["id"]
    # payee changed to another vendor's exact name: the link follows
    assert client.put(url, json={"payee": "Beta Tools"}).json()["vendor_id"] == b["id"]
    # to a name no vendor has: the link is dropped
    assert client.put(url, json={"payee": "Cash"}).json()["vendor_id"] is None
    # an explicit id, with the payee left as it is
    got = client.put(url, json={"vendor_id": a["id"]}).json()
    assert got["vendor_id"] == a["id"] and got["payee"] == "Cash"
    # a save that leaves payee and vendor out keeps the link
    assert client.put(url, json={"description": "memo"}).json()["vendor_id"] == a["id"]
    # null clears it
    assert client.put(url, json={"vendor_id": None}).json()["vendor_id"] is None


def test_a_transfer_takes_no_vendor(client, seed_accounts):
    _vendor(client, "Savings")
    out = _post(
        client,
        seed_accounts,
        payee="Savings",
        category_account_id=seed_accounts["1010"].id,
    )
    assert out["vendor_id"] is None


def test_the_page_suggests_vendors_and_sends_the_one_picked():
    # both payee boxes list the vendors, and both posts carry the link
    assert JS.count('list="') >= 2 and "_vendorDatalist(" in JS
    assert JS.count("vendor_id:") >= 2 or "_payeeVendorId(" in JS


def _nec(client, vendor_id, year=2026):
    rows = client.get(f"/api/tax-forms/1099?year={year}")
    assert rows.status_code == 200, rows.text
    return next(
        (v for v in rows.json()["vendors"] if v["vendor_id"] == vendor_id), None
    )


def _summary(client, vendor_id):
    r = client.get("/api/reports/1099-summary?year=2026").json()
    return next(i for i in r["items"] if i["vendor_id"] == vendor_id)


def test_a_linked_register_entry_counts_toward_the_1099(client, seed_accounts):
    v = client.post(
        "/api/vendors", json={"name": "Sub Co", "is_1099_vendor": True}
    ).json()
    _post(client, seed_accounts, payee="Sub Co", amount="-1500.00")
    assert _nec(client, v["id"])["total_paid"] == 1500.00
    assert not _nec(client, v["id"])["reportable"]
    _post(client, seed_accounts, payee="x", vendor_id=v["id"], amount="-600.00")
    nec = _nec(client, v["id"])
    assert nec["total_paid"] == 2100.00 and nec["reportable"]  # 2026: $2,000
    assert _summary(client, v["id"])["total_paid"] == 2100.0
    # a refund linked to the vendor nets off; a voided entry is left out
    _post(client, seed_accounts, payee="Sub Co", amount="25.00")
    assert _nec(client, v["id"])["total_paid"] == 2075.00
    last = _post(client, seed_accounts, payee="Sub Co", amount="-100.00")
    assert client.post(f"/api/banking/entries/{last['id']}/void").status_code in (
        200,
        201,
    )
    assert _nec(client, v["id"])["total_paid"] == 2075.00


def test_an_unlinked_payee_and_a_transfer_do_not_count(client, seed_accounts):
    v = client.post(
        "/api/vendors", json={"name": "Sub Co", "is_1099_vendor": True}
    ).json()
    _post(client, seed_accounts, payee="Sub Co Inc", amount="-900.00")  # text only
    _post(
        client,
        seed_accounts,
        payee="Sub Co",
        amount="-900.00",
        category_account_id=seed_accounts["1010"].id,
    )  # a transfer
    assert _nec(client, v["id"])["total_paid"] == 0.00
    assert _summary(client, v["id"])["total_paid"] == 0


def test_a_linked_entry_to_a_vendor_off_1099_is_not_on_the_form(client, seed_accounts):
    v = client.post("/api/vendors", json={"name": "Lumber Yard"}).json()
    _post(client, seed_accounts, payee="Lumber Yard", amount="-900.00")
    assert _nec(client, v["id"]) is None


def _1099_vendor(client):
    return client.post(
        "/api/vendors", json={"name": "Sub Co", "is_1099_vendor": True}
    ).json()


def test_a_card_charge_to_a_1099_vendor_does_not_count(client, seed_accounts):
    v = _1099_vendor(client)
    _post(
        client,
        seed_accounts,
        account_id=seed_accounts["2100"].id,
        payee="Sub Co",
        amount="-700.00",
    )
    assert seed_accounts["2100"].bank_kind == "credit_card"
    assert _nec(client, v["id"])["total_paid"] == 0.00
    _post(client, seed_accounts, payee="Sub Co", amount="-300.00")  # a check
    assert _nec(client, v["id"])["total_paid"] == 300.00


def test_an_entry_to_accounts_payable_alone_counts_zero(client, seed_accounts):
    v = _1099_vendor(client)
    _post(
        client,
        seed_accounts,
        payee="Sub Co",
        amount="-900.00",
        category_account_id=seed_accounts["2000"].id,
    )
    assert _nec(client, v["id"])["total_paid"] == 0.00


def test_a_split_counts_only_its_expense_and_cogs_lines(
    client, db_session, seed_accounts
):
    from app.models.transactions import Transaction, TransactionLine

    v = _1099_vendor(client)
    t = Transaction(
        date=date(2026, 9, 10),
        source_type="bank_entry",
        vendor_id=v["id"],
        description="split",
    )
    amounts = [
        ("6000", "400.00", "0"),  # expense
        ("5000", "100.00", "0"),  # cost of goods
        ("2000", "250.00", "0"),  # A/P, not the vendor's pay for work
        ("1000", "0", "750.00"),  # the bank pays the lot
    ]
    for num, debit, credit in amounts:
        t.lines.append(
            TransactionLine(
                account_id=seed_accounts[num].id,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    db_session.add(t)
    db_session.commit()
    assert _nec(client, v["id"])["total_paid"] == 500.00
