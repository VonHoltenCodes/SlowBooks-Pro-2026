"""Migrate Data: a file the source cannot place by name, said in a
sentence (#706). The refusal printed a Python list ("['AR_Aging_Detail.xlsx']")
and never said the files were another source's export."""

import pytest


def _post(client, source, *names):
    files = [("files", (name, b"a,b\n1,2\n", "text/csv")) for name in names]
    return client.post(f"/api/migration/{source}/dry-run", files=files)


def test_one_file_named_in_a_sentence_and_its_source_said(client):
    r = _post(client, "xero", "Chart_of_accounts.csv", "AR_Aging_Detail.xlsx")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "[" not in detail and "'AR_Aging_Detail.xlsx'" not in detail
    assert detail.startswith(
        "Could not tell what AR_Aging_Detail.xlsx is from its name"
    )
    assert "it looks like a QuickBooks Online export export" not in detail
    assert "it looks like a QuickBooks Online export" in detail
    assert "Choose QuickBooks Online export under Coming from" in detail


def test_several_files_named_together(client):
    r = _post(client, "qbo_export", "holiday.csv", "photos.csv")
    detail = r.json()["detail"]
    assert detail.startswith(
        "Could not tell what holiday.csv and photos.csv are from their names."
    )
    # no other source reads them either: what this one reads, in words
    assert "looks like" not in detail and "look like" not in detail
    assert "'chart' or 'accounts'" in detail


@pytest.mark.parametrize(
    "names, other",
    [
        (("Customers.xlsx", "Vendors.xlsx"), "QuickBooks Online export"),
    ],
)
def test_files_another_source_reads(client, names, other):
    detail = _post(client, "xero", *names).json()["detail"]
    assert f"these look like a {other}" in detail
    assert "leave them out" in detail


def test_files_several_sources_read_name_none(client):
    # "transactions" is read by every make_classifier source but not Xero
    detail = _post(client, "xero", "Transactions.csv").json()["detail"]
    assert "looks like" not in detail


# ── a whole export uploaded under the wrong source (W-114) ────────────────

import io  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

QBO = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
QBO_BUNDLE = (
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "Trial_balance.csv",
    "Customers.csv",
    "Vendors.csv",
    "Employees.csv",
)
ANOTHER_SOURCE = (
    "These look like a QuickBooks Online export: choose QuickBooks Online "
    "export under Coming from and run the dry run again."
)


def _zip(members: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


def _qbo_zip() -> bytes:
    return _zip({name: (QBO / name).read_bytes() for name in QBO_BUNDLE})


def _zip_upload(name: str, data: bytes):
    return ("files", (name, io.BytesIO(data), "application/zip"))


def test_a_qbo_zip_alone_under_xero_says_whose_export_it_is(client, seed_accounts):
    r = client.post(
        "/api/migration/xero/dry-run",
        files=[_zip_upload("Cedar Hollow.zip", _qbo_zip())],
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False
    # the result itself says so, first, above the missing-chart line
    assert data["errors"][0] == ANOTHER_SOURCE
    # and the members Xero could not place are named, as loose files are
    left = " ".join(data["warnings"])
    assert "Could not tell what Balance_sheet.csv, Profit_and_loss.csv" in left
    assert "Employees.csv are from their names, so they were left out." in left
    assert "Journal.csv" not in left and "Trial_balance.csv" not in left


def test_a_qbo_zip_and_a_loose_file_under_xero(client, seed_accounts):
    aging = "Sutehk_Realty_Group_A_R_Aging_Detail_Report.csv"
    r = client.post(
        "/api/migration/xero/dry-run",
        files=[
            _zip_upload("Cedar Hollow.zip", _qbo_zip()),
            ("files", (aging, io.BytesIO((QBO / aging).read_bytes()), "text/csv")),
        ],
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    # the zip's unplaced members named with the loose file, in one sentence
    assert detail.startswith("Could not tell what Balance_sheet.csv, ")
    assert f"Employees.csv and {aging} are from their names" in detail
    assert "these look like a QuickBooks Online export" in detail
    assert "Choose QuickBooks Online export under Coming from" in detail


def test_a_genuine_xero_zip_still_dry_runs(client, seed_accounts):
    from tests.test_xero_import import COA_CSV, GL_CSV, TB_CSV

    zipped = _zip(
        {
            "ChartOfAccounts.csv": COA_CSV,
            "GeneralLedger.csv": GL_CSV,
            "TrialBalance.csv": TB_CSV,
        }
    )
    r = client.post(
        "/api/migration/xero/dry-run", files=[_zip_upload("Xero.zip", zipped)]
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data["errors"]
    assert data["accounts"] == 3 and data["journals"] == 2
    assert not any("look like" in e for e in data["errors"] + data["warnings"])
