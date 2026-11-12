"""A QuickBooks Online dry run without the General Ledger names the deleted
card once (#813): it was refused as "in the books but not on the Balance
Sheet or the Profit and Loss … add it to the chart", and then again as
"GL references account … not present in the chart of accounts"."""

from app.services import qbo_export_import as qbo
from tests.test_qbo_numbered_export_767 import BUNDLE, _dry_run

CARD = "2101 Old Store Card (deleted)"


def test_the_deleted_card_is_named_once(client, seed_accounts):
    data = _dry_run(client, [n for n in BUNDLE if n != "General_ledger.csv"])
    assert data["ok"] is False
    naming = [e for e in data["errors"] if CARD in e]
    assert len(naming) == 1, naming
    assert "add it to the chart" in naming[0]


def test_only_an_account_already_refused_is_dropped():
    errors = [
        "Account 'A' is in the books but not on the Balance Sheet or the Profit "
        "and Loss (QuickBooks Online leaves a zero-balance row off both): add it "
        "to the chart with its type, then import again",
        "GL references account 'A' not present in the chart of accounts",
        "GL references account 'B' not present in the chart of accounts",
    ]
    assert qbo._named_once(errors) == [errors[0], errors[2]]
    assert qbo._named_once(errors[1:]) == errors[1:]
