"""Exported files open correctly in the programs people open them with
(2.17.3 exploratory test, W-M14).

* Every CSV starts with a UTF-8 byte-order mark. Without it Excel on
  Windows reads the file in the ANSI code page, and "Bäckerei Müller &
  Söhne" opened as "BÃ¤ckerei MÃ¼ller & SÃ¶hne" — every list export and
  every report CSV.
* The IIF file is written the way QuickBooks reads it (Windows-1252), a
  character it cannot hold degrades to its plain letter rather than breaking
  the file, a formula-shaped name is neutralised as the CSV export does,
  and a foreign-currency document goes out at the home-currency amounts the
  ledger booked.
"""

from decimal import Decimal

import pytest

BOM = b"\xef\xbb\xbf"
ACCENTED = "Bäckerei Müller & Söhne"

CSV_ENDPOINTS = (
    "/api/csv/export/customers",
    "/api/csv/export/vendors",
    "/api/csv/export/items",
    "/api/csv/export/invoices",
    "/api/csv/export/accounts",
    "/api/csv/export/classes",
    "/api/csv/export/jobs",
    "/api/csv/export/bills",
    "/api/csv/export/deposits",
    "/api/csv/export/sales-receipts",
    "/api/reports/trial-balance/csv",
    "/api/reports/general-ledger/csv",
    "/api/reports/profit-loss/csv",
    "/api/reports/balance-sheet/csv",
    "/api/reports/statement-of-financial-position/csv",
    "/api/reports/statement-of-activities/csv",
    "/api/reports/fund-balances/csv",
    "/api/reports/functional-expenses/csv",
    "/api/reports/pledges/csv",
    "/api/tax/schedule-c/csv",
    "/api/analytics/export.csv",
)


@pytest.mark.parametrize("path", CSV_ENDPOINTS)
def test_every_csv_starts_with_a_utf8_byte_order_mark(client, seed_accounts, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.text)
    assert r.headers["content-type"].startswith("text/csv"), path
    assert r.content.startswith(BOM), path
    # exactly one mark, and the rest is plain UTF-8
    assert not r.content[len(BOM) :].startswith(BOM), path
    r.content[len(BOM) :].decode("utf-8")


def test_an_accented_name_survives_the_customer_export(client):
    r = client.post("/api/customers", json={"name": ACCENTED, "bill_city": "München"})
    assert r.status_code == 201, r.text
    body = client.get("/api/csv/export/customers").content
    assert body.startswith(BOM)
    text = body.decode("utf-8-sig")
    assert ACCENTED in text and "München" in text


def test_the_desktop_shell_still_gets_the_file_inline_with_the_mark(client):
    r = client.get("/api/analytics/export.csv", headers={"X-Slowbooks-Desktop": "1"})
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content.startswith(BOM)


# ── IIF: the encoding QuickBooks reads ───────────────────────────────────


def _rows(content: bytes):
    """The IIF file as QuickBooks reads it: Windows-1252, tab-separated."""
    return [line.split("\t") for line in content.decode("cp1252").splitlines()]


def test_the_iif_file_is_windows_1252(client):
    r = client.post("/api/customers", json={"name": ACCENTED, "bill_city": "München"})
    assert r.status_code == 201, r.text
    r = client.get("/api/iif/export/customers")
    assert r.status_code == 200
    assert "windows-1252" in r.headers["content-type"].lower()
    assert ACCENTED.encode("cp1252") in r.content
    assert ACCENTED.encode("utf-8") not in r.content
    assert "München".encode("cp1252") in r.content


def test_a_character_windows_1252_lacks_becomes_its_plain_letter(client):
    r = client.post("/api/customers", json={"name": "Łódź Őrség 名古屋 €"})
    assert r.status_code == 201, r.text
    body = client.get("/api/iif/export/customers").content
    names = [row[1] for row in _rows(body) if row[0] == "CUST"]
    assert names == ["Lódz Orség ??? €"]


def test_to_ansi_keeps_what_windows_1252_has():
    from app.services.iif_export import to_ansi

    text = "naïve – “quoted” … 5€ ﬁne ő"
    assert to_ansi(text) == "naïve – “quoted” … 5€ fine o".encode("cp1252")


# ── IIF: formula-shaped names, neutralised and restored ──────────────────

EVIL = '=HYPERLINK("http://example.test","click")'


def test_formula_shaped_names_go_out_neutralised(client, seed_accounts):
    cust = client.post("/api/customers", json={"name": EVIL}).json()
    assert client.post("/api/vendors", json={"name": "+Plus Supply"}).status_code == 201
    r = client.post(
        "/api/items", json={"name": "@Home visit", "item_type": "service", "rate": 50}
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-07-01",
            "lines": [{"description": "-5% promo", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    rows = _rows(client.get("/api/iif/export/all").content)

    assert ["'" + EVIL] == [r[1] for r in rows if r[0] == "CUST"]
    assert ["'+Plus Supply"] == [r[1] for r in rows if r[0] == "VEND"]
    assert ["'@Home visit"] == [r[1] for r in rows if r[0] == "INVITEM"]
    # the invoice names the customer exactly as the list row does, the memo
    # is text, and the amount stays a number
    trns = next(r for r in rows if r[:2] == ["TRNS", "INVOICE"])
    spl = next(r for r in rows if r[:2] == ["SPL", "INVOICE"])
    assert trns[4] == "'" + EVIL and trns[5] == "100.00"
    assert spl[9] == "'-5% promo" and spl[5] == "-100.00"


def test_reimporting_our_own_iif_gives_the_names_back(client):
    assert client.post("/api/customers", json={"name": EVIL}).status_code == 201
    exported = client.get("/api/iif/export/customers").content
    r = client.post(
        "/api/iif/import", files={"file": ("customers.iif", exported, "text/plain")}
    )
    assert r.status_code == 200, r.text
    names = [c["name"] for c in client.get("/api/customers").json()]
    assert names.count(EVIL) == 1
    assert "'" + EVIL not in names


def test_the_iif_importer_takes_the_guard_off(client):
    """A guarded name read back is the name, not a new "'=..." customer."""
    iif = "!CUST\tNAME\tCOMPANYNAME\r\nCUST\t'=HYPERLINK(1)\t'@Acme\r\n"
    r = client.post(
        "/api/iif/import",
        files={"file": ("c.iif", iif.encode("cp1252"), "text/plain")},
    )
    assert r.status_code == 200, r.text
    cust = next(c for c in client.get("/api/customers").json())
    assert cust["name"] == "=HYPERLINK(1)" and cust["company"] == "@Acme"


# ── IIF: foreign-currency documents at the amounts the ledger booked ─────


def _gl(db_session, source_type, source_id):
    from app.models.transactions import Transaction, TransactionLine

    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == source_type, Transaction.source_id == source_id
        )
        .one()
    )
    return db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()


def _block(rows, trnstype, docnum):
    """The TRNS row and its SPL rows for one document."""
    for i, r in enumerate(rows):
        if r[:2] == ["TRNS", trnstype] and docnum in r:
            spls = []
            for s in rows[i + 1 :]:
                if s[0] != "SPL":
                    break
                spls.append(s)
            return r, spls
    raise AssertionError(f"no {trnstype} {docnum}")


def test_a_foreign_currency_invoice_goes_out_at_home_amounts(
    client, db_session, seed_accounts, seed_customer
):
    # 1.13579 makes the per-line rounding drift a cent, which the ledger
    # puts on the A/R line; the file must say what the ledger says
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "currency": "EUR",
            "exchange_rate": "1.13579",
            "lines": [
                {"description": "panel A", "quantity": 1, "rate": "33.33"},
                {"description": "panel B", "quantity": 1, "rate": "33.33"},
            ],
        },
    ).json()
    assert Decimal(str(inv["total"])) == Decimal("66.66")  # document currency
    gl = _gl(db_session, "invoice", inv["id"])
    ar = seed_accounts["1100"].id
    ar_debit = sum(ln.debit for ln in gl if ln.account_id == ar)
    income = sorted(ln.credit for ln in gl if ln.account_id != ar and ln.credit)

    trns, spls = _block(
        _rows(client.get("/api/iif/export/invoices").content),
        "INVOICE",
        inv["invoice_number"],
    )
    assert Decimal(trns[5]) == ar_debit == Decimal("75.72")
    assert sorted(-Decimal(s[5]) for s in spls) == income
    assert Decimal(trns[5]) + sum(Decimal(s[5]) for s in spls) == 0


def test_a_foreign_currency_payment_carries_the_exchange_gain(
    client, db_session, seed_accounts, seed_customer
):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [{"description": "consulting", "quantity": 1, "rate": 100}],
        },
    ).json()
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-06",
            "amount": 100,
            "currency": "EUR",
            "exchange_rate": "1.20",
            "reference": "EUR-PAY-1",
            "allocations": [{"invoice_id": inv["id"], "amount": 100}],
        },
    )
    assert pay.status_code == 201, pay.text
    trns, spls = _block(
        _rows(client.get("/api/iif/export/payments").content), "PAYMENT", "EUR-PAY-1"
    )
    assert trns[5] == "120.00"  # the cash, at the payment's rate
    ar = next(s for s in spls if s[6] == inv["invoice_number"])
    assert ar[5] == "-110.00"  # A/R relieved at the invoice's booked rate
    fx = [s for s in spls if s is not ar]
    assert [s[5] for s in fx] == ["-10.00"] and "Exchange" in fx[0][3]


def test_a_payment_with_money_left_over_still_balances(
    client, seed_accounts, seed_customer
):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "lines": [{"description": "work", "quantity": 1, "rate": 100}],
        },
    ).json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-02",
            "amount": 150,
            "reference": "OVER-1",
            "allocations": [{"invoice_id": inv["id"], "amount": 100}],
        },
    )
    assert r.status_code == 201, r.text
    trns, spls = _block(
        _rows(client.get("/api/iif/export/payments").content), "PAYMENT", "OVER-1"
    )
    assert trns[5] == "150.00"
    assert sorted(s[5] for s in spls) == ["-100.00", "-50.00"]


def test_a_foreign_currency_bill_goes_out_at_home_amounts(
    client, db_session, seed_accounts
):
    from app.models.contacts import Vendor

    vendor = Vendor(name="Euro Supplier", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "bill_number": "EUR-B1",
            "date": "2026-07-02",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [
                {
                    "account_id": seed_accounts["6400"].id,
                    "description": "supplies",
                    "quantity": 1,
                    "rate": 200,
                }
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text
    trns, spls = _block(
        _rows(client.get("/api/iif/export/bills").content), "BILL", "EUR-B1"
    )
    assert trns[5] == "-220.00" and [s[5] for s in spls] == ["220.00"]
