"""Print the checks Pay Bills wrote in one go (#603, from discussion #594).

Each check printed on its own from its payment. A check Pay Bills writes with
no number waits to be printed: GET /api/checks/unprinted?bank_account_id=
lists those for a bank account with the number the next would take, and POST
/api/checks/print-batch prints the ones sent, in order, as one PDF with a
check to a page on the existing 3-per-page stock, giving each the number it
printed with. A numbered check is printed and leaves the list.

Most tests stub the PDF renderer so they run everywhere; the page count is
checked through the real one (it needs WeasyPrint and skips without it).
"""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.routes import checks as checks_route

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def printed(monkeypatch):
    """The checks handed to the renderer, one list per batch."""
    seen = []

    def fake(checks, company):
        seen.append([dict(c) for c in checks])
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_checks_pdf", fake)
    return seen


@pytest.fixture
def vendor(client, seed_accounts):
    r = client.post("/api/vendors", json={"name": "Harbor Supply"})
    assert r.status_code in (200, 201), r.text
    return r.json()


_n = iter(range(1, 1000))


def _pay(client, seed_accounts, vendor, amount=100.0, date="2026-09-10", **extra):
    """A bill for `amount`, paid by check with no number unless told."""
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": f"B-{next(_n)}",
            "date": min(date, "2026-09-01"),
            "lines": [
                {
                    "description": "Supplies",
                    "amount": amount,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    body = {
        "vendor_id": vendor["id"],
        "date": date,
        "amount": amount,
        "method": "check",
        "allocations": [{"bill_id": bill.json()["id"], "amount": amount}],
        **extra,
    }
    r = client.post("/api/bill-payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _bank(seed_accounts):
    return seed_accounts["1000"].id


def _list(client, account_id):
    r = client.get(f"/api/checks/unprinted?bank_account_id={account_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _batch(client, account_id, pairs):
    return client.post(
        "/api/checks/print-batch",
        json={
            "bank_account_id": account_id,
            "checks": [
                {"bill_payment_id": pid, "check_number": num} for pid, num in pairs
            ],
        },
    )


def _ids(listing):
    return [c["bill_payment_id"] for c in listing["checks"]]


def test_the_list_shows_only_unprinted_checks_for_that_account(
    client, seed_accounts, vendor
):
    waiting = _pay(client, seed_accounts, vendor, 100.0, notes="Acct 9")
    _pay(client, seed_accounts, vendor, 50.0, check_number="1040")  # by hand
    _pay(client, seed_accounts, vendor, 60.0, method="ach")
    savings = _pay(
        client,
        seed_accounts,
        vendor,
        70.0,
        pay_from_account_id=seed_accounts["1010"].id,
    )
    got = _list(client, _bank(seed_accounts))
    assert _ids(got) == [waiting["id"]]
    row = got["checks"][0]
    assert row["payee"] == "Harbor Supply"
    assert (row["date"], row["amount"], row["memo"]) == (
        "2026-09-10",
        "100.00",
        "Acct 9",
    )
    assert _ids(_list(client, seed_accounts["1010"].id)) == [savings["id"]]


def test_the_list_offers_the_next_number_after_the_highest_used(
    client, seed_accounts, vendor
):
    assert _list(client, _bank(seed_accounts))["next_check_number"] == "1001"
    _pay(client, seed_accounts, vendor, 50.0, check_number="1040")
    _pay(client, seed_accounts, vendor, 50.0, check_number="ACH")  # not a check
    assert _list(client, _bank(seed_accounts))["next_check_number"] == "1041"


def test_a_card_or_a_missing_account_is_refused(client, seed_accounts):
    r = client.get(f"/api/checks/unprinted?bank_account_id={seed_accounts['2100'].id}")
    assert r.status_code == 400
    assert client.get("/api/checks/unprinted?bank_account_id=9999").status_code == 404


def test_a_batch_numbers_each_check_in_the_order_sent(
    client, seed_accounts, vendor, printed
):
    a, b, c = (_pay(client, seed_accounts, vendor, amt) for amt in (10.0, 20.0, 30.0))
    r = _batch(client, _bank(seed_accounts), [(c["id"], "2001"), (a["id"], "2002")])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "Checks_2001-2002.pdf" in r.headers["content-disposition"]
    # rendered in the order sent, each with its number and amount
    assert [(x["check_number"], float(x["amount"])) for x in printed[0]] == [
        ("2001", 30.0),
        ("2002", 10.0),
    ]
    for pid, number in ((c["id"], "2001"), (a["id"], "2002")):
        assert client.get(f"/api/bill-payments/{pid}").json()["check_number"] == number
    # the printed ones leave the list; the one left out stays
    assert _ids(_list(client, _bank(seed_accounts))) == [b["id"]]
    assert _list(client, _bank(seed_accounts))["next_check_number"] == "2003"


def _warn(client, on=True):
    r = client.put(
        "/api/settings", json={"warn_duplicate_check_number": "true" if on else "false"}
    )
    assert r.status_code == 200, r.text


def test_a_number_already_on_the_account_is_refused_and_nothing_is_numbered(
    client, seed_accounts, vendor, printed
):
    """The same 409 a single check gets (#481): the code and the uses the
    keep-or-edit question is built from, under the warning setting, and
    allow_duplicate_check_number=true keeps the number."""
    _warn(client)
    _pay(client, seed_accounts, vendor, 50.0, check_number="1040")
    a, b = (_pay(client, seed_accounts, vendor, amt) for amt in (10.0, 20.0))
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1041"), (b["id"], "1040")])
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate_check_number"
    assert detail["number"] == "1040"
    assert "1040" in detail["message"]
    assert [(u["payee"], u["amount"]) for u in detail["uses"]] == [
        ("Harbor Supply", "50.00")
    ]
    assert printed == []
    assert set(_ids(_list(client, _bank(seed_accounts)))) == {a["id"], b["id"]}
    # keep it: the person answered the question
    r = client.post(
        "/api/checks/print-batch?allow_duplicate_check_number=true",
        json={
            "bank_account_id": _bank(seed_accounts),
            "checks": [
                {"bill_payment_id": a["id"], "check_number": "1041"},
                {"bill_payment_id": b["id"], "check_number": "1040"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bill-payments/{b['id']}").json()["check_number"] == "1040"


def test_with_the_warning_off_a_used_number_prints_as_a_single_check_would(
    client, seed_accounts, vendor, printed
):
    _warn(client, on=False)
    _pay(client, seed_accounts, vendor, 50.0, check_number="1040")
    a = _pay(client, seed_accounts, vendor, 10.0)
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1040")])
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bill-payments/{a['id']}").json()["check_number"] == "1040"


def test_a_number_repeated_in_the_batch_is_refused(
    client, seed_accounts, vendor, printed
):
    a, b = (_pay(client, seed_accounts, vendor, amt) for amt in (10.0, 20.0))
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1050"), (b["id"], "1050")])
    assert r.status_code == 400, r.text
    assert printed == []
    # " 1050" is 1050: the number is compared as it is stored, stripped
    r = _batch(client, _bank(seed_accounts), [(a["id"], " 1050"), (b["id"], "1050 ")])
    assert r.status_code == 400, r.text
    assert printed == []


def test_a_number_with_a_leading_zero_is_its_own_number_in_the_batch_and_on_the_account(
    client, seed_accounts, vendor, printed
):
    """ "01051" and "1051" are two numbers to the duplicate lookup (uses_of
    matches the stored text); the batch compares them the same way, so the
    two are neither refused together nor refused against each other."""
    _warn(client)
    _pay(client, seed_accounts, vendor, 50.0, check_number="1051")
    a, b = (_pay(client, seed_accounts, vendor, amt) for amt in (10.0, 20.0))
    r = _batch(client, _bank(seed_accounts), [(a["id"], "01051"), (b["id"], "1052")])
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bill-payments/{a['id']}").json()["check_number"] == "01051"
    c, d = (_pay(client, seed_accounts, vendor, amt) for amt in (30.0, 40.0))
    r = _batch(client, _bank(seed_accounts), [(c["id"], "1051"), (d["id"], "01051")])
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["number"] == "1051"
    assert printed[-1][0]["check_number"] == "01051"


def test_a_number_that_is_not_digits_is_refused(client, seed_accounts, vendor, printed):
    a = _pay(client, seed_accounts, vendor, 10.0)
    r = _batch(client, _bank(seed_accounts), [(a["id"], "12A")])
    assert r.status_code == 400, r.text


def test_a_printed_check_cannot_be_printed_again_in_a_batch(
    client, seed_accounts, vendor, printed
):
    a = _pay(client, seed_accounts, vendor, 10.0)
    assert _batch(client, _bank(seed_accounts), [(a["id"], "1050")]).status_code == 200
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1051")])
    assert r.status_code == 400, r.text
    assert client.get(f"/api/bill-payments/{a['id']}").json()["check_number"] == "1050"


def test_a_void_check_is_left_off_the_list_and_refused(
    client, seed_accounts, vendor, printed
):
    a, b = (_pay(client, seed_accounts, vendor, amt) for amt in (10.0, 20.0))
    assert client.post(f"/api/bill-payments/{a['id']}/void").status_code == 200
    assert _ids(_list(client, _bank(seed_accounts))) == [b["id"]]
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1050")])
    assert r.status_code == 400, r.text


def test_a_check_paid_from_another_account_is_refused(
    client, seed_accounts, vendor, printed
):
    s = _pay(
        client,
        seed_accounts,
        vendor,
        10.0,
        pay_from_account_id=seed_accounts["1010"].id,
    )
    r = _batch(client, _bank(seed_accounts), [(s["id"], "1050")])
    assert r.status_code == 400, r.text


def test_a_check_in_a_closed_period_is_left_off_the_list_and_refused(
    client, seed_accounts, vendor, printed
):
    """A company that paid by check for years without typing numbers is not
    offered that history to renumber: a payment dated on or before the
    closing date is not listed, and a batch of the open ones prints without
    it standing in the way."""
    old = _pay(client, seed_accounts, vendor, 10.0, date="2026-09-10")
    on_the_day = _pay(client, seed_accounts, vendor, 15.0, date="2026-09-30")
    a, b = (
        _pay(client, seed_accounts, vendor, amt, date="2026-10-05")
        for amt in (20.0, 30.0)
    )
    assert _ids(_list(client, _bank(seed_accounts))) == [
        old["id"],
        on_the_day["id"],
        a["id"],
        b["id"],
    ]
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )
    assert _ids(_list(client, _bank(seed_accounts))) == [a["id"], b["id"]]
    r = _batch(client, _bank(seed_accounts), [(old["id"], "1050")])
    assert r.status_code == 400, r.text
    # the refusal names the closing date (#813)
    assert r.json()["detail"] == (
        f"Bill payment {old['id']} is dated 2026-09-10, on or before the closing "
        "date (2026-09-30), so it is not printed here: numbering it would change "
        "a closed period."
    )
    assert printed == []
    r = _batch(client, _bank(seed_accounts), [(a["id"], "1050"), (b["id"], "1051")])
    assert r.status_code == 200, r.text
    assert [x["check_number"] for x in printed[0]] == ["1050", "1051"]
    assert client.get(f"/api/bill-payments/{old['id']}").json()["check_number"] is None
    assert _ids(_list(client, _bank(seed_accounts))) == []


def test_an_empty_batch_is_refused(client, seed_accounts):
    r = _batch(client, _bank(seed_accounts), [])
    assert r.status_code == 422


def test_the_pdf_has_one_page_of_the_stock_per_check(client, seed_accounts, vendor):
    pypdf = pytest.importorskip("pypdf")
    pays = [_pay(client, seed_accounts, vendor, 10.0 + i) for i in range(4)]
    r = _batch(
        client,
        _bank(seed_accounts),
        [(p["id"], str(3001 + i)) for i, p in enumerate(pays)],
    )
    assert r.status_code == 200, r.text[:300]
    assert r.content[:5] == b"%PDF-"
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    assert len(reader.pages) == 4
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        # two stubs and the check: the number prints three times on its page
        assert text.count(str(3001 + i)) >= 3


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_print_checks_screen_numbers_the_checked_rows_and_posts_them():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "print_checks_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # sequential from the starting number, an unchecked row skipped
    assert got["numbers"] == ["1051", "", "1052"]
    assert got["bad_start"] == ["", ""]
    assert got["filled"] == ["1051", "", "1052"]
    html = got["html"]
    assert 'id="batch-start"' in html and 'value="1051"' in html
    assert 'class="batch-number"' in html and "Harbor Supply" in html
    assert "Acct 9" in html and "$100.00" in html
    assert "BillsPage.printSelectedChecks()" in html
    assert "printSelectedChecks" not in got["empty"]
    assert "No checks are waiting" in got["empty"]
    # Print posts the checked rows, in order, with the numbers in the boxes
    posted = next(c for c in got["calls"] if c[0] == "fetch")
    assert posted[1] == "/api/checks/print-batch"
    assert posted[2] == {
        "bank_account_id": 7,
        "checks": [
            {"bill_payment_id": 11, "check_number": "1051"},
            {"bill_payment_id": 13, "check_number": "1052"},
        ],
    }
    # then the list reloads without the printed checks
    assert ["get", "/checks/unprinted?bank_account_id=7"] in got["calls"]
    # opened on its own, every waiting check is listed and none is ticked;
    # after Pay Bills, only the checks it just wrote are (the older one is
    # listed, unticked), and Select all ticks and numbers every row
    assert got["ticked_alone"] == [False, False]
    assert got["ticked_after_pay"] == [True, False]
    assert got["select_all_after_pay"] == ""
    assert got["select_all_every"] == " checked"
    assert got["select_all_box"] is False
    assert 'id="batch-select-all"' in html
    assert got["all_ticked"] == [[True, "1051"], [True, "1052"], [True, "1053"]]
    # a number the account already has (#481): the same keep-or-edit question
    # as a single check, then the batch sent again with the number kept
    assert got["dup_calls"][:3] == [
        ["fetch", "/api/checks/print-batch"],
        ["ask", "This check number is already used"],
        ["fetch", "/api/checks/print-batch?allow_duplicate_check_number=true"],
    ]
    assert ["toast", "3 checks printed"] in got["dup_calls"]
    # "go back and edit": nothing printed, Print works again
    assert got["edit_calls"] == [
        ["fetch", "/api/checks/print-batch"],
        ["ask", "This check number is already used"],
    ]
    assert got["edit_button_enabled"] is True


def test_pay_bills_opens_the_list_with_the_checks_it_just_wrote_ticked():
    """The Pay Bills screen hands Print Checks the ids of the payments it
    made (each POST /api/bill-payments response), and nothing else is
    ticked: years of unnumbered history are not renumbered in one click."""
    src = (ROOT / "app" / "static" / "js" / "bills.js").read_text(encoding="utf-8")
    pay = src[src.index("async savePay(") : src.index("async voidBillPayment(")]
    assert "written.push(done.id)" in pay
    assert "BillsPage.showPrintChecks(fromId, written)" in pay
    assert "showPrintChecks(fromId)" not in pay


def test_a_quickbooks_num_is_not_the_next_checks(
    client, db_session, seed_accounts, vendor
):
    """A QuickBooks Desktop ledger import keeps QuickBooks' Num as the
    reference (#663), and one long Num put the next check in the millions.
    The next check follows the checks written here."""
    from datetime import date
    from decimal import Decimal

    from app.services.accounting import create_journal_entry

    def imported(num):
        create_journal_entry(
            db_session,
            date(2026, 8, 3),
            "Harbor Supply",
            [
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": Decimal("40"),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": _bank(seed_accounts),
                    "debit": Decimal("0"),
                    "credit": Decimal("40"),
                },
            ],
            source_type="qb_ledger_import",
            reference=num,
        )
        db_session.commit()

    imported("20240108123456")
    assert _list(client, _bank(seed_accounts))["next_check_number"] == "1001"
    _pay(client, seed_accounts, vendor, 50.0, check_number="1040")
    imported("5001")
    assert _list(client, _bank(seed_accounts))["next_check_number"] == "1041"
