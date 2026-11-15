"""A QuickBooks Online dry run without the Journal says what it reads, the
Export data zip, in its own words (#910). A user who downloaded the General
Ledger and a chart of accounts report from the Reports page was told
"Missing general ledger / journal file (filename containing 'general',
'ledger', or 'journal')" while General_ledger.xlsx sat in the upload."""

from app.services import migration_common, qbo_export_import as qbo
from tests.test_qbo_numbered_export_767 import LIST, _dry_run

ZIP = "Export data zip"


def test_account_list_and_general_ledger_name_the_zip(client, seed_accounts):
    data = _dry_run(client, [LIST, "General_ledger.csv"])
    assert data["ok"] is False
    assert len(data["errors"]) == 1, data["errors"]
    (error,) = data["errors"]
    assert error.startswith("No Journal was uploaded.")
    assert ZIP in error and "gear → Export data" in error
    assert "The General Ledger report was uploaded and is not read" in error
    assert "filename containing" not in error


def test_general_ledger_alone_names_the_journal_and_the_statements(
    client, seed_accounts
):
    data = _dry_run(client, ["General_ledger.csv"])
    assert data["ok"] is False
    assert len(data["errors"]) == 2, data["errors"]
    journal, chart = data["errors"]
    assert journal.startswith("No Journal was uploaded.")
    assert chart.startswith("No Balance Sheet or Profit and Loss was uploaded.")
    assert ZIP in chart and "Reports page is not it" in chart
    assert all("'chart' or 'accounts'" not in e for e in data["errors"])


def test_the_import_refuses_in_the_same_words(client, seed_accounts):
    from tests.test_qbo_numbered_export_767 import _import

    data = _import(client, [LIST, "General_ledger.csv"])
    assert data["ok"] is False
    assert data["imported_journals"] == 0
    assert data["errors"][0].startswith("No Journal was uploaded.")


def test_a_journal_without_a_general_ledger_does_not_mention_one():
    (error,) = qbo._missing_files({"coa": "x"})
    assert "General Ledger report was uploaded" not in error


def test_other_sources_keep_the_shared_sentences():
    assert migration_common.missing_files({}) == [
        "Missing chart of accounts file (filename containing 'chart' or 'accounts')",
        "Missing general ledger / journal file (filename containing "
        "'general', 'ledger', or 'journal')",
    ]
    assert migration_common.missing_files({"coa": "x", "gl": "y"}) == []


def test_xero_dry_run_is_unchanged(client):
    files = [("files", ("Chart_of_accounts.csv", b"a,b\n1,2\n", "text/csv"))]
    r = client.post("/api/migration/xero/dry-run", files=files)
    assert r.status_code == 200, r.text
    assert "Missing general ledger / journal file" in r.json()["errors"][0]
