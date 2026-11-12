"""The JSON the API answers says it is UTF-8 (#742).

A Windows desktop user saw the QuickBooks Online export import's own
separators — " — " in a journal entry's description and " · " in its
reference — as "â€”" and "Â·", and read the same through GET /api/journal.
The stored text and the bytes on the wire were right; the Content-Type was
a bare "application/json", and a client that finds no charset may guess.
Windows PowerShell 5.1's Invoke-WebRequest / Invoke-RestMethod guesses
ISO-8859-1, which turns every UTF-8 em dash into "â€”". Every JSON response
— a route's, an error handler's, the sign-in gate's refusal — now names
its charset, so such a client reads the text as stored.

The CSV half of the same story (Excel reading a file in the ANSI code page)
was settled by the byte-order mark every CSV download starts with; the
tests at the end pin it for the importer's separators and pin that a file
carrying the mark still imports.
"""

import csv
import io
from pathlib import Path

from app.services import qbo_export_import as qbo

UTF8_JSON = "application/json; charset=utf-8"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
BUNDLE = (
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "General_ledger.csv",
    "Trial_balance.csv",
    "Customers.csv",
    "Vendors.csv",
    "Employees.csv",
)


def _bundle(bom_on=()):
    files = []
    for name in BUNDLE:
        data = (FIXTURES / name).read_bytes()
        if name in bom_on:
            data = b"\xef\xbb\xbf" + data
        files.append(("files", (name, io.BytesIO(data), "text/csv")))
    return files


def _as_a_charset_guessing_client_reads_it(response) -> str:
    """The body decoded the way Windows PowerShell 5.1 decodes it: by the
    charset the Content-Type names, else ISO-8859-1."""
    charset = "iso-8859-1"
    for part in response.headers["content-type"].split(";")[1:]:
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset":
            charset = value.strip().strip('"')
    return response.content.decode(charset)


def test_a_route_answer_names_utf8_as_its_charset(client, seed_accounts):
    r = client.get("/api/accounts")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == UTF8_JSON


def test_a_not_found_and_a_refused_body_name_it_too(client):
    missing = client.get("/api/journal/987654")
    assert missing.status_code == 404, missing.text
    assert missing.headers["content-type"] == UTF8_JSON
    refused = client.post("/api/journal", json={"no_such_field": 1})
    assert refused.status_code == 422, refused.text
    assert refused.headers["content-type"] == UTF8_JSON
    unknown = client.get("/api/no-such-route")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"] == UTF8_JSON


def test_the_sign_in_gates_refusal_names_it_too(unauthed_client):
    r = unauthed_client.get("/api/accounts")
    assert r.status_code == 401, r.text
    assert r.headers["content-type"] == UTF8_JSON


def test_the_qbo_importers_separators_read_back_as_written(client, seed_accounts):
    resp = client.post("/api/migration/qbo_export/import", files=_bundle())
    assert resp.json()["ok"] is True, resp.json()["errors"]
    r = client.get(
        "/api/journal",
        params={"source_type": qbo.SOURCE_TYPE, "start_date": "1900-01-01"},
    )
    assert r.status_code == 200, r.text
    # the bytes are UTF-8 ...
    assert "—".encode() in r.content and "·".encode() in r.content
    # ... and a client that goes by the header reads them as written
    text = _as_a_charset_guessing_client_reads_it(r)
    assert " — " in text and " · " in text
    assert "â€”" not in text and "Â·" not in text


def test_the_separators_survive_a_csv_opened_the_way_excel_opens_it(
    client, seed_accounts
):
    resp = client.post("/api/migration/qbo_export/import", files=_bundle())
    assert resp.json()["ok"] is True, resp.json()["errors"]
    r = client.get(
        "/api/reports/general-ledger/csv",
        params={"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"\xef\xbb\xbf")
    cells = [
        cell
        for row in csv.reader(io.StringIO(r.content.decode("utf-8-sig")))
        for cell in row
    ]
    assert any(" — " in cell for cell in cells), cells[:40]
    assert not any("â€”" in cell for cell in cells)


def test_an_export_that_starts_with_a_byte_order_mark_still_imports(
    client, seed_accounts
):
    # Excel adds the mark when it saves a CSV as "CSV UTF-8"; the first
    # header must still be found, not read as "﻿Date".
    resp = client.post("/api/migration/qbo_export/import", files=_bundle(bom_on=BUNDLE))
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_journals"] == 19
