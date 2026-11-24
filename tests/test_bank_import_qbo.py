"""A QuickBooks Web Connect download (.qbo) is OFX inside, and the server
reads it through the OFX path, but the register's Import Bank File window
did not offer one in its file picker. The picker now accepts .qbo, the
dialog sends any file that is not a .csv to the OFX endpoints, and those
endpoints read the file whatever its name or the case of its extension."""

import re
from pathlib import Path

import pytest

from app.models.banking import BankTransaction

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)

# An invented Web Connect file: two lines from a fictional checking account.
QBO = (
    "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\n"
    "ENCODING:USASCII\nCHARSET:1252\nCOMPRESSION:NONE\n"
    "OLDFILEUID:NONE\nNEWFILEUID:NONE\n\n<OFX>\n<SIGNONMSGSRSV1>\n<SONRS>\n"
    "<STATUS>\n<CODE>0\n<SEVERITY>INFO\n</STATUS>\n<DTSERVER>20261031120000\n"
    "<LANGUAGE>ENG\n<INTU.BID>12345\n</SONRS>\n</SIGNONMSGSRSV1>\n"
    "<BANKMSGSRSV1>\n<STMTTRNRS>\n<TRNUID>1\n<STATUS>\n<CODE>0\n"
    "<SEVERITY>INFO\n</STATUS>\n<STMTRS>\n<CURDEF>USD\n<BANKACCTFROM>\n"
    "<BANKID>1\n<ACCTID>42\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>\n"
    "<BANKTRANLIST>\n<DTSTART>20261001\n<DTEND>20261031\n"
    "<STMTTRN>\n<TRNTYPE>CREDIT\n<DTPOSTED>20261003120000\n<TRNAMT>875.00\n"
    "<FITID>QBO0001\n<NAME>DEPOSIT\n</STMTTRN>\n"
    "<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>20261007120000\n<TRNAMT>-42.18\n"
    "<FITID>QBO0002\n<NAME>LAKESIDE PAPER GOODS\n</STMTTRN>\n"
    "</BANKTRANLIST>\n<LEDGERBAL>\n<BALAMT>832.82\n<DTASOF>20261031\n"
    "</LEDGERBAL>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n"
)


def test_the_file_picker_offers_qbo():
    accept = re.search(r'accept="([^"]*)"[^>]*id="ofx-file"', JS)
    assert accept, "import dialog file input not found"
    assert accept.group(1).split(",") == [".ofx", ".qfx", ".qbo", ".csv"]


def test_only_a_csv_leaves_the_ofx_path():
    """The dialog picks the endpoint by name: .csv (any case) goes to the
    CSV endpoints, everything else, a .qbo included, to the OFX ones."""
    assert "return /\\.csv$/i.test(file.name || '');" in JS
    assert "isCsv ? '/api/bank-import/preview-csv' : '/api/bank-import/preview'" in JS


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("name", ["statement.qbo", "STATEMENT.QBO"])
def test_a_qbo_file_previews_and_imports_through_the_ofx_path(
    client, db_session, feed, name
):
    upload = {"file": (name, QBO.encode(), "application/vnd.intu.qbo")}
    r = client.post("/api/bank-import/preview", files=upload)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["count"] == 2
    assert [t["amount"] for t in preview["transactions"]] == [875.0, -42.18]

    r = client.post(f"/api/bank-import/import/{feed['id']}", files=upload)
    assert r.status_code == 200, r.text
    lines = db_session.query(BankTransaction).order_by(BankTransaction.date).all()
    assert [str(t.amount) for t in lines] == ["875.00", "-42.18"]
