"""Every file download works in the desktop app (explore 2.17.3
integration, I21).

The desktop app's web view (WebView2 with downloads on; WKWebView too)
takes a ``Content-Disposition: attachment`` answer to a page's own fetch()
for a download and never hands it back — "Failed to fetch". desktop_shim.js
fetches every document with the X-Slowbooks-Desktop header, and the server
answers ``inline`` for it: app/main.py rewrites any attachment answer, so
the IIF export and the nonprofit, pledge and Schedule C CSVs — which build
their own responses — are covered as _csv_response(request=...) covers the
CSV page. The tests below hold every download endpoint to that.

What was broken was on the page side:

* The IIF export fetched its file itself, without the header, so in the
  desktop app the web view took the attachment and the export failed. It
  now asks for inline and saves through the desktop bridge (Documents/
  SlowBooks Pro/Reports, "Saved to … Show in folder"); a browser downloads.
* The shim knew a file to save only by an attachment disposition — which
  the server no longer sends it — or a CSV type. An attachment such as a
  scanned receipt (image/jpeg) or a spreadsheet, and an .iif, fell through
  to the page branch and opened as garbage text. Anything that is not a
  page or a PDF is now saved.
"""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = {"X-Slowbooks-Desktop": "1"}
PERIOD = "start_date=2026-01-01&end_date=2026-12-31"

DOWNLOADS = [
    "/api/iif/export/all",
    *(
        f"/api/iif/export/{s}"
        for s in (
            "accounts",
            "classes",
            "bills",
            "deposits",
            "sales-receipts",
            "customers",
            "vendors",
            "items",
            "invoices",
            "payments",
            "estimates",
        )
    ),
    *(
        f"/api/csv/export/{s}"
        for s in (
            "customers",
            "classes",
            "jobs",
            "bills",
            "deposits",
            "sales-receipts",
            "vendors",
            "items",
            "invoices",
            "accounts",
        )
    ),
    "/api/reports/statement-of-financial-position/csv",
    f"/api/reports/statement-of-activities/csv?{PERIOD}",
    f"/api/reports/fund-balances/csv?{PERIOD}",
    f"/api/reports/functional-expenses/csv?{PERIOD}",
    f"/api/reports/pledges/csv?{PERIOD}",
    f"/api/tax/schedule-c/csv?{PERIOD}",
    f"/api/reports/trial-balance/csv?{PERIOD}",
    f"/api/reports/general-ledger/csv?{PERIOD}",
    f"/api/reports/profit-loss/csv?{PERIOD}",
    "/api/reports/balance-sheet/csv?as_of_date=2026-12-31",
    "/api/analytics/export.csv",
]


def _disposition(r):
    assert r.status_code == 200, r.text[:300]
    kind, _, name = r.headers["content-disposition"].partition(";")
    return kind.strip(), name.strip()


@pytest.mark.parametrize("url", DOWNLOADS)
def test_a_download_is_an_attachment_in_a_browser_and_inline_to_the_desktop(
    client, seed_accounts, url
):
    kind, name = _disposition(client.get(url))
    assert kind == "attachment"
    assert name.startswith("filename")
    kind, desktop_name = _disposition(client.get(url, headers=DESKTOP))
    assert kind == "inline"
    assert desktop_name == name


def test_an_analytics_pdf_is_inline_to_the_desktop(client, seed_accounts):
    assert _disposition(client.get("/api/analytics/export.pdf"))[0] == "attachment"
    r = client.get("/api/analytics/export.pdf", headers=DESKTOP)
    assert _disposition(r)[0] == "inline"


def test_an_attachment_is_inline_to_the_desktop(client, seed_accounts, db_session):
    from app.models.contacts import Vendor

    v = Vendor(name="Sign Supply", is_active=True)
    db_session.add(v)
    db_session.commit()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "bill_number": "SS-1",
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Vinyl",
                    "quantity": 1,
                    "rate": 10,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    ).json()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client.post(
        f"/api/attachments/bill/{bill['id']}",
        files={"file": ("receipt.png", io.BytesIO(png), "image/png")},
    )
    assert r.status_code in (200, 201), r.text
    url = f"/api/attachments/download/{r.json()['id']}"
    assert _disposition(client.get(url))[0] == "attachment"
    kind, name = _disposition(client.get(url, headers=DESKTOP))
    assert kind == "inline" and "receipt.png" in name


@pytest.fixture(scope="module")
def probe():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "desktop_downloads_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_shell_saves_every_file_that_is_not_a_page_or_a_pdf(probe):
    shim = probe["shim"]
    assert shim["headers_sent"] is True
    assert shim["/api/attachments/download/1"] == [
        ["save_document_file", "receipt.jpg", "JPEGDATA"]
    ]
    assert shim["/api/attachments/download/2"] == [
        ["save_document_file", "mileage log.xlsx", "PK.."]
    ]
    assert shim["/api/iif/export/all"] == [
        ["save_document_file", "slowbooks_export.iif", "!ACCNT\tNAME"]
    ]
    assert shim["/api/csv/export/customers"] == [
        ["save_document_file", "customers.csv", "Name\nAcme"]
    ]
    # a PDF still opens in the viewer, a page in a window
    assert shim["/api/invoices/1/pdf"] == [["open_document_pdf", "Invoice_1001.pdf"]]
    assert shim["/api/invoices/1/print-preview"] == [
        ["open_document_html", "<html>Invoice</html>"]
    ]


def test_a_file_the_page_already_holds_is_left_to_the_web_view(probe):
    # saveBlob's own fallback link: fetching it back went round in a circle
    assert probe["shim"]["blob_link"] == {"prevented": False, "fetched": 0}


def test_pages_can_save_through_the_shell(probe):
    shim = probe["shim"]
    assert shim["saveFile_desktop"] is True
    assert shim["saveFile_bridge"] == [["save_document_file", "a.iif", "!HDR"]]
    assert shim["saveFile_browser"] is False  # a browser downloads it itself


def test_the_iif_export_works_in_the_desktop_app(probe):
    assert probe["iif_desktop"] == {
        "header": "1",
        "saved": ["slowbooks_export.iif"],
        "clicked": [],
        "toasts": [],
    }
    assert probe["iif_browser"] == {
        "header": "1",
        "saved": [],
        "clicked": ["slowbooks_export.iif"],
        "toasts": ["Exported slowbooks_export.iif"],
    }
