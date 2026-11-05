"""A month imported as CSV and then as OFX (or the other way round) is the
same statement: every line of the second file is a duplicate, skipped and
counted, and nothing lands in To review (#705). A line whose posting an
earlier statement line already holds is reported as in the books, and the
review list can exclude every line it shows at once."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction
from app.services.ofx_import import import_transactions

# A fictional month: a deposit, a check, a loan payment, a long card
# description (an OFX <NAME> is cut at 32 characters) and two identical
# same-day charges, both real.
LINES = [
    ("2026-10-01", "1250.00", "DEPOSIT"),
    ("2026-10-02", "-310.00", "CHECK 2201"),
    ("2026-10-05", "-412.37", "MAPLE CREDIT UNION LOAN PMT ...7741"),
    ("2026-10-08", "-58.19", "POS PURCHASE NORTHSIDE HARDWARE AND SUPPLY 0042"),
    ("2026-10-09", "-6.50", "CORNER COFFEE"),
    ("2026-10-09", "-6.50", "CORNER COFFEE"),
]


def _csv(lines=LINES):
    rows = ["Date,Description,Amount,Balance"]
    for d, a, text in lines:
        y, m, dd = d.split("-")
        rows.append(f"{m}/{dd}/{y},{text},{a},0.00")
    return "\n".join(rows) + "\n"


def _ofx(lines=LINES):
    body = []
    for i, (d, a, text) in enumerate(lines):
        body.append(
            "<STMTTRN>\n<TRNTYPE>OTHER\n"
            f"<DTPOSTED>{d.replace('-', '')}120000\n<TRNAMT>{a}\n"
            f"<FITID>{d.replace('-', '')}{i:04d}\n<NAME>{text[:32]}\n</STMTTRN>"
        )
    return (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\n"
        "ENCODING:USASCII\nCHARSET:1252\nCOMPRESSION:NONE\n"
        "OLDFILEUID:NONE\nNEWFILEUID:NONE\n\n<OFX>\n<BANKMSGSRSV1>\n"
        "<STMTTRNRS>\n<TRNUID>1\n<STATUS>\n<CODE>0\n<SEVERITY>INFO\n</STATUS>\n"
        "<STMTRS>\n<CURDEF>USD\n<BANKACCTFROM>\n<BANKID>1\n<ACCTID>42\n"
        "<ACCTTYPE>CHECKING\n</BANKACCTFROM>\n<BANKTRANLIST>\n"
        "<DTSTART>20261001\n<DTEND>20261031\n" + "\n".join(body) + "\n"
        "</BANKTRANLIST>\n<LEDGERBAL>\n<BALAMT>0.00\n<DTASOF>20261031\n"
        "</LEDGERBAL>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n"
    )


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def booked(client, seed_accounts):
    """Every line of the month already in the books, as register entries."""
    for d, a, text in LINES:
        amount = Decimal(a)
        category = "4000" if amount > 0 else "6000"
        r = client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": d,
                "amount": str(amount),
                "category_account_id": seed_accounts[category].id,
                "payee": text[:40],
            },
        )
        assert r.status_code in (200, 201), r.text


def _csv_import(client, feed):
    r = client.post(
        f"/api/bank-import/import-csv/{feed['id']}",
        files={"file": ("statement.csv", _csv().encode(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ofx_import(client, feed):
    r = client.post(
        f"/api/bank-import/import/{feed['id']}",
        files={"file": ("statement.ofx", _ofx().encode(), "application/x-ofx")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _to_review(client, feed):
    return client.get(
        f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
    ).json()


@pytest.mark.parametrize("order", ["csv-then-ofx", "ofx-then-csv"])
def test_the_same_month_in_the_other_format_is_all_duplicates(
    client, db_session, feed, booked, order
):
    first, second = (
        (_csv_import, _ofx_import)
        if order == "csv-then-ofx"
        else (_ofx_import, _csv_import)
    )
    one = first(client, feed)
    assert one["imported"] == len(LINES) and one["skipped"] == 0
    # the two identical coffees are ambiguous to auto-match (two postings
    # at the same distance) and wait in To review; the rest match
    assert one["matched"] == len(LINES) - 2
    waiting = {t["id"] for t in _to_review(client, feed)}
    two = second(client, feed)
    assert two["imported"] == 0
    assert two["skipped"] == len(LINES)
    assert two["matched"] == 0 and two["already_in_books"] == 0
    # nothing new waits: only the first file's two ambiguous lines
    assert {t["id"] for t in _to_review(client, feed)} == waiting
    assert db_session.query(BankTransaction).count() == len(LINES)


@pytest.mark.parametrize("order", ["csv-then-ofx", "ofx-then-csv"])
def test_with_nothing_booked_the_second_format_still_adds_nothing_to_review(
    client, db_session, feed, order
):
    first, second = (
        (_csv_import, _ofx_import)
        if order == "csv-then-ofx"
        else (_ofx_import, _csv_import)
    )
    first(client, feed)
    assert len(_to_review(client, feed)) == len(LINES)
    two = second(client, feed)
    assert two["imported"] == 0 and two["skipped"] == len(LINES)
    assert len(_to_review(client, feed)) == len(LINES)


def test_each_format_still_dedupes_its_own_reimport(client, feed):
    _ofx_import(client, feed)
    again = _ofx_import(client, feed)
    assert again["imported"] == 0 and again["skipped"] == len(LINES)
    _csv_import(client, feed)
    csv_again = _csv_import(client, feed)
    assert csv_again["imported"] == 0 and csv_again["skipped"] == len(LINES)


def test_an_extra_identical_charge_in_the_second_file_still_imports(
    client, db_session, feed
):
    """Each line on the account answers for one incoming line: a third
    $6.50 coffee the OFX has and the CSV did not is a new line."""
    _csv_import(client, feed)
    extra = LINES + [("2026-10-09", "-6.50", "CORNER COFFEE")]
    r = client.post(
        f"/api/bank-import/import/{feed['id']}",
        files={"file": ("s.ofx", _ofx(extra).encode(), "application/x-ofx")},
    )
    out = r.json()
    assert out["imported"] == 1 and out["skipped"] == len(LINES)


def test_a_different_description_is_not_a_duplicate(client, db_session, feed):
    _csv_import(client, feed)
    other = [("2026-10-09", "-6.50", "BAKERY ON MAIN")]
    out = import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "X1",
                "date": date(2026, 10, 9),
                "amount": Decimal(a),
                "payee": t,
                "memo": "",
            }
            for _, a, t in other
        ],
    )
    assert out["imported"] == 1 and out["skipped"] == 0


def test_a_line_whose_posting_an_earlier_line_holds_is_in_the_books(
    client, db_session, feed, booked
):
    """The OFX names the payee differently, so it is no duplicate by its
    description, but the posting it would match is already matched by the
    CSV's line of that date and amount: reported as in the books and set
    aside, not left as "not in the books"."""
    _csv_import(client, feed)
    out = import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "BANKREF-1",
                "date": date(2026, 10, 2),
                "amount": Decimal("-310.00"),
                "payee": "CHK #2201 CLEARED",
                "memo": "",
            }
        ],
    )
    assert out["imported"] == 1 and out["matched"] == 0
    assert out["already_in_books"] == 1
    bt = db_session.query(BankTransaction).filter_by(import_id="BANKREF-1").one()
    assert bt.match_status == "excluded"
    assert bt.id not in {t["id"] for t in _to_review(client, feed)}


def test_a_free_posting_is_matched_not_set_aside(
    client, db_session, feed, booked, seed_accounts
):
    _csv_import(client, feed)
    # a second $310 posting the same day, not yet matched by anything
    client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-10-02",
            "amount": "-310.00",
            "category_account_id": seed_accounts["6000"].id,
            "payee": "Landlord",
        },
    )
    out = import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "BANKREF-2",
                "date": date(2026, 10, 2),
                "amount": Decimal("-310.00"),
                "payee": "RENT",
                "memo": "",
            }
        ],
    )
    assert out["matched"] == 1 and out["already_in_books"] == 0


def test_find_matches_sets_aside_lines_already_in_the_books(
    client, db_session, feed, booked
):
    """Lines a release before #705 let in twice: Find matches clears them."""
    _csv_import(client, feed)
    for i, (d, a, text) in enumerate(LINES):
        db_session.add(
            BankTransaction(
                bank_account_id=feed["id"],
                date=date.fromisoformat(d),
                amount=Decimal(a),
                payee=f"OTHER NAME {i}",
                description="",
                import_id=f"OLD{i}",
                import_source="ofx",
                match_status="unmatched",
            )
        )
    db_session.commit()
    assert len(_to_review(client, feed)) == len(LINES) + 2
    r = client.post(f"/api/banking/accounts/{feed['id']}/feed/auto-match")
    assert r.status_code == 200, r.text
    # the two same-day coffees are ambiguous to the books (two postings,
    # two lines each side) and stay for the bookkeeper; the rest are set
    # aside as already in the books
    assert r.json() == {"matched": 0, "already_in_books": len(LINES) - 2}
    left = _to_review(client, feed)
    assert {t["payee"] for t in left} == {
        "CORNER COFFEE",
        "OTHER NAME 4",
        "OTHER NAME 5",
    }


def test_exclude_all_shown_excludes_the_lines_sent(
    client, db_session, feed, booked, seed_accounts
):
    _ofx_import(client, feed)
    before = {t["id"] for t in _to_review(client, feed)}
    other = client.post(
        "/api/banking/accounts",
        json={"name": "Second feed", "account_id": seed_accounts["1010"].id},
    ).json()
    rows = []
    for i, ba in enumerate([feed["id"], feed["id"], feed["id"], other["id"]]):
        bt = BankTransaction(
            bank_account_id=ba,
            date=date(2026, 11, 1 + i),
            amount=Decimal("-1.00") * (i + 1),
            payee=f"LINE {i}",
            import_id=f"N{i}",
            match_status="unmatched",
        )
        db_session.add(bt)
        rows.append(bt)
    db_session.commit()
    matched = db_session.query(BankTransaction).filter_by(match_status="auto").first()
    shown = [rows[0].id, rows[1].id, rows[3].id, matched.id]
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/exclude-all",
        json={"line_ids": shown},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"excluded": 2}
    for bt in rows + [matched]:
        db_session.refresh(bt)
    assert [bt.match_status for bt in rows] == [
        "excluded",
        "excluded",
        "unmatched",  # not sent
        "unmatched",  # another feed's
    ]
    assert matched.match_status == "auto"
    assert {t["id"] for t in _to_review(client, feed)} == before | {rows[2].id}


def test_exclude_all_shown_needs_a_feed(client):
    r = client.post("/api/banking/accounts/999999/feed/exclude-all", json={})
    assert r.status_code == 404
