"""Export parity (#70): everything the IIF importer reads, the exporter
writes — classes (with the CLASS column on every transaction block),
Customer:Job rows, bills, deposits and sales receipts — and the CSV
exporter covers the same record types. A full export re-imports into the
same books without errors or duplicates (idempotent round trip)."""

import io

from app.models.accounts import Account, AccountType
from app.services import iif_import
from app.services.iif_export import export_all


def _import(client, text):
    return client.post(
        "/api/iif/import",
        files={"file": ("export.iif", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )


def _seed(client, db_session, seed_customer):
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .first()
    )
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    bank = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.ASSET)
        .first()
    )
    cls = client.post("/api/classes", json={"name": "Field:North"}).json()
    client.post("/api/classes", json={"name": "Old"})
    old = next(c for c in client.get("/api/classes").json() if c["name"] == "Old")
    client.put(f"/api/classes/{old['id']}", json={"is_archived": True})
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Deck"}
    ).json()
    vendor = client.post("/api/vendors", json={"name": "Lumber & Co"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "L-100",
            "date": "2026-07-03",
            "class_id": cls["id"],
            "job_id": job["id"],
            "tax_rate": 0.05,
            "lines": [
                {
                    "account_id": expense.id,
                    "description": "2x6 lumber",
                    "quantity": 10,
                    "rate": 12.5,
                },
                {
                    "account_id": expense.id,
                    "description": "delivery",
                    "quantity": 1,
                    "rate": 40,
                },
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text
    sr = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-04",
            "class_id": cls["id"],
            "deposit_to_account_id": bank.id,
            "payment_method": "cash",
            "lines": [{"description": "Walk-in sale", "quantity": 2, "rate": 30}],
        },
    )
    assert sr.status_code in (200, 201), sr.text
    # A Make Deposits entry is a journal-only transaction (source_type
    # "deposit") — post one the way the deposits route does.
    from datetime import date as _date
    from decimal import Decimal

    from app.services.accounting import create_journal_entry

    create_journal_entry(
        db_session,
        _date(2026, 7, 5),
        "Deposit to Checking",
        [
            {
                "account_id": bank.id,
                "debit": Decimal("150"),
                "credit": 0,
                "description": "Deposit to Checking",
            },
            {
                "account_id": income.id,
                "debit": 0,
                "credit": Decimal("150"),
                "description": "Cash from the day",
            },
        ],
        source_type="deposit",
        reference="DEP-9",
        class_id=cls["id"],
    )
    db_session.commit()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-06",
            "class_id": cls["id"],
            "job_id": job["id"],
            "lines": [{"description": "Deck framing", "quantity": 1, "rate": 900}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    return cls, job, vendor


def test_export_all_carries_classes_jobs_bills_deposits_and_sales_receipts(
    client, db_session, seed_accounts, seed_customer
):
    cls, job, vendor = _seed(client, db_session, seed_customer)
    text = export_all(db_session)
    parsed = iif_import.parse_iif(text)

    classes = {r["NAME"]: r for r in parsed["CLASS"]}
    assert classes["Field:North"]["HIDDEN"] == "N"
    assert classes["Old"]["HIDDEN"] == "Y"
    assert "Uncategorized" not in classes  # the system default is ours, not QuickBooks'

    cust_names = [r["NAME"] for r in parsed["CUST"]]
    assert f"{seed_customer.name}:Deck" in cust_names

    blocks = parsed["TRNS"]
    by_type = {}
    for blk in blocks:
        by_type.setdefault(blk["trns"]["TRNSTYPE"], []).append(blk)
    assert {"INVOICE", "BILL", "DEPOSIT", "CASH SALE"} <= set(by_type)

    inv_blk = by_type["INVOICE"][0]
    assert inv_blk["trns"]["CLASS"] == "Field:North"
    assert all(s["CLASS"] == "Field:North" for s in inv_blk["spl"])
    assert inv_blk["trns"]["NAME"] == seed_customer.name

    bill_blk = by_type["BILL"][0]
    assert bill_blk["trns"]["NAME"] == "Lumber & Co"
    assert bill_blk["trns"]["DOCNUM"] == "L-100"
    assert bill_blk["trns"]["CLASS"] == "Field:North"
    spl_amounts = sorted(float(s["AMOUNT"]) for s in bill_blk["spl"])
    assert spl_amounts == [8.25, 40.0, 125.0]  # two lines + 5% tax
    assert abs(float(bill_blk["trns"]["AMOUNT"])) == 173.25
    assert (
        sum(float(s["AMOUNT"]) for s in bill_blk["spl"])
        + float(bill_blk["trns"]["AMOUNT"])
        == 0
    )

    dep_blk = by_type["DEPOSIT"][0]
    assert (
        float(dep_blk["trns"]["AMOUNT"]) == 150.0
        and dep_blk["trns"]["DOCNUM"] == "DEP-9"
    )
    assert [float(s["AMOUNT"]) for s in dep_blk["spl"]] == [-150.0]

    sale_blk = by_type["CASH SALE"][0]
    assert float(sale_blk["trns"]["AMOUNT"]) == 60.0
    assert sale_blk["trns"]["NAME"] == seed_customer.name
    assert sale_blk["trns"]["CLASS"] == "Field:North"
    # the receipt is not also exported as a plain invoice
    assert all(
        b["trns"]["DOCNUM"] != sale_blk["trns"]["DOCNUM"] for b in by_type["INVOICE"]
    )


def test_full_export_reimports_without_errors_or_duplicates(
    client, db_session, seed_accounts, seed_customer
):
    _seed(client, db_session, seed_customer)
    before = {
        "bills": len(client.get("/api/bills").json()),
        "invoices": len(client.get("/api/invoices").json()),
        "receipts": len(client.get("/api/sales-receipts").json()),
        "classes": len(client.get("/api/classes?include_archived=true").json()),
        "customers": len(client.get("/api/customers").json()),
        "jobs": len(client.get("/api/jobs?include_inactive=true").json()),
    }
    text = export_all(db_session)
    resp = _import(client, text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not body.get("errors"), body.get("errors")
    after = {
        "bills": len(client.get("/api/bills").json()),
        "invoices": len(client.get("/api/invoices").json()),
        "receipts": len(client.get("/api/sales-receipts").json()),
        "classes": len(client.get("/api/classes?include_archived=true").json()),
        "customers": len(client.get("/api/customers").json()),
        "jobs": len(client.get("/api/jobs?include_inactive=true").json()),
    }
    assert after == before, (before, after)


def test_section_endpoints_and_csv_exports(
    client, db_session, seed_accounts, seed_customer
):
    _seed(client, db_session, seed_customer)
    for path in ("classes", "bills", "deposits", "sales-receipts"):
        r = client.get(f"/api/iif/export/{path}")
        assert r.status_code == 200, (path, r.text)
        assert "!" in r.text
    assert "!CLASS" in client.get("/api/iif/export/classes").text
    assert "BILL" in client.get("/api/iif/export/bills").text
    assert "DEPOSIT" in client.get("/api/iif/export/deposits").text
    assert "CASH SALE" in client.get("/api/iif/export/sales-receipts").text

    bills_csv = client.get("/api/csv/export/bills").text
    assert (
        "L-100" in bills_csv
        and "Field:North" in bills_csv
        and "2x6 lumber" in bills_csv
    )
    assert "Deck" in bills_csv  # the job on the bill
    deposits_csv = client.get("/api/csv/export/deposits").text
    assert "DEP-9" in deposits_csv and "150" in deposits_csv
    receipts_csv = client.get("/api/csv/export/sales-receipts").text
    assert "Walk-in sale" in receipts_csv
    classes_csv = client.get("/api/csv/export/classes").text
    assert "Field:North" in classes_csv and "Old,Y" in classes_csv.replace('"', "")
    jobs_csv = client.get("/api/csv/export/jobs").text
    assert f"{seed_customer.name}:Deck" in jobs_csv
