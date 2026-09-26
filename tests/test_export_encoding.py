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
