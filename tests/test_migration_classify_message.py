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
