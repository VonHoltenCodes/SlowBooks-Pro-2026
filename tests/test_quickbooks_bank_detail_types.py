"""The QuickBooks importers bring Bank and Credit Card accounts in as bank
and card accounts (#718).

A Bank Feeds "Imports into" list offers the chart's accounts whose detail
type is Bank (and Credit Card where the feed takes them). A chart that came
from QuickBooks is where most of those charts come from, so both importers
must carry what QuickBooks said: the Desktop lists IIF names it in ACCNTTYPE
(BANK, CCARD), and the Online export only in the Balance Sheet's headings
("Bank Accounts", "Credit Cards"). What happens today, stated here: both
carry it, sub-accounts included; an account whose name is already in the
chart keeps the chart's detail type and the import says so."""

import io

from app.models.accounts import Account

from tests.test_qbo_export_migration import _csv_files


def _iif(client, text):
    r = client.post(
        "/api/iif/import",
        files={"file": ("lists.iif", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _named(db_session, name):
    db_session.expire_all()
    return db_session.query(Account).filter(Account.name == name).one()


def _offered(client):
    """The accounts Bank Feeds offers under "Imports into": the overview's."""
    return {
        a["name"]: a["bank_kind"] for a in client.get("/api/banking/overview").json()
    }


def test_a_desktop_iif_bank_and_card_arrive_with_their_detail_types(
    client, db_session, seed_accounts
):
    result = _iif(
        client,
        "!ACCNT\tNAME\tACCNTTYPE\n"
        "ACCNT\tFirst Midwest Checking\tBANK\n"
        "ACCNT\tFirst Midwest Checking:Payroll\tBANK\n"
        "ACCNT\tShop Amex\tCCARD\n"
        "ACCNT\tShop Amex:Driver Card\tCCARD\n"
        "ACCNT\tPetty Cash Box\tOCASSET\n",
    )
    assert result["errors"] == [], result
    for name, detail in (
        ("First Midwest Checking", "bank"),
        ("Payroll", "bank"),
        ("Shop Amex", "credit_card"),
        ("Driver Card", "credit_card"),
    ):
        acct = _named(db_session, name)
        assert (acct.detail_type, acct.bank_kind) == (detail, detail), name
    assert _named(db_session, "Payroll").parent_id == (
        _named(db_session, "First Midwest Checking").id
    )
    petty = _named(db_session, "Petty Cash Box")
    assert (petty.detail_type, petty.bank_kind) == ("other_current_asset", None)
    offered = _offered(client)
    assert offered["First Midwest Checking"] == "bank"
    assert offered["Payroll"] == "bank"
    assert offered["Shop Amex"] == "credit_card"
    assert "Petty Cash Box" not in offered


def test_a_desktop_iif_bank_named_like_a_plain_chart_account_keeps_the_chart(
    client, db_session, seed_accounts
):
    """The chart's detail type is the user's: an import never changes it. A
    hand-made "Operating" asset the file calls BANK stays a plain asset, and
    the import says where the two differ."""
    db_session.add(Account(name="Operating", account_type="asset"))
    db_session.commit()
    result = _iif(client, "!ACCNT\tNAME\tACCNTTYPE\nACCNT\tOperating\tBANK\n")
    acct = _named(db_session, "Operating")
    assert (acct.detail_type, acct.bank_kind) == ("other_current_asset", None)
    assert any(
        "'Operating' is Bank in the file and Other Current Asset in the chart" in w
        for w in result["warnings"]
    ), result["warnings"]


def test_an_online_export_bank_and_card_arrive_with_their_detail_types(
    client, db_session, seed_accounts
):
    """The Balance Sheet's "Bank Accounts" and "Credit Cards" headings are the
    export's only word on it; the accounts under them land as Bank and
    Credit Card, and the ones under "Other Current Assets" do not."""
    resp = client.post("/api/migration/qbo_export/import", files=_csv_files())
    assert resp.json()["ok"] is True, resp.json()["errors"]
    for name, detail in (
        ("Operating Account", "bank"),
        ("Escrow", "bank"),
        ("Company Visa", "credit_card"),
    ):
        acct = _named(db_session, name)
        assert (acct.detail_type, acct.bank_kind) == (detail, detail), name
    escrow_held = _named(db_session, "Escrow Held")
    assert escrow_held.bank_kind is None
    assert escrow_held.detail_type == "other_current_liability"
    offered = _offered(client)
    assert offered["Operating Account"] == "bank"
    assert offered["Escrow"] == "bank"
    assert offered["Company Visa"] == "credit_card"
    assert "Escrow Held" not in offered
