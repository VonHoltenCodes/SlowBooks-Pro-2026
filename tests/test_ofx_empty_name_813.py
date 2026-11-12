"""An OFX line with an empty <NAME></NAME> previews and imports (#813).

The whole file was refused ("That file isn't a bank statement this can
read…") for one transaction's empty name, where the same file without the
tag read fine with no payee. An empty MEMO is the same."""

from app.models.banking import BankTransaction
from tests.test_bank_import_bad_files import GOOD_OFX, _post, feed  # noqa: F401


def test_an_empty_name_previews_and_imports(client, db_session, feed):  # noqa: F811
    content = GOOD_OFX.replace(
        b"<NAME>Coffee Roasters", b"<NAME></NAME>\n<MEMO>Card 1234", 1
    ).replace(b"<NAME>Customer Deposit", b"<NAME>Customer Deposit\n<MEMO></MEMO>", 1)
    r = _post(client, "/api/bank-import/preview", content)
    assert r.status_code == 200, r.text
    r = _post(client, f"/api/bank-import/import/{feed['id']}", content)
    assert r.status_code == 200, r.text
    rows = {bt.import_id: bt for bt in db_session.query(BankTransaction).all()}
    assert set(rows) == {"A1", "A2"}
    assert rows["A1"].payee == "Card 1234"
    assert rows["A2"].payee == "Customer Deposit"
    assert (rows["A2"].description or "") == ""
