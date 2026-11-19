"""End-of-month payment terms (#1091).

"Net 75 EOM" is due 75 days after the last day of the document's month,
whatever day of that month the document is dated. It is a third kind on the
terms list (`end_of_month`), and terms text off the list that reads as
end-of-month ("Net 75 EOM", "75 days EOM", "EOM") gets that rule too, as
"Net N" text has always had its days. Every existing reading is pinned here
alongside, so the new kind changes nothing that was already understood.
Same house style as tests/test_payment_terms_664.py.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.payment_terms import END_OF_MONTH, PaymentTerm
from app.services.payment_terms import (
    DATE_DRIVEN,
    FALLBACK_DAYS,
    Rule,
    describe,
    eom_days,
    month_end,
    read_text,
)
from app.services.pdf_service import _format_date
from tests.test_payment_terms_664 import _bill, _invoice, _put, _term, _vendor

D = Decimal


def _dates(client, on, terms, **extra):
    r = client.get(
        "/api/payment-terms/dates", params={"date": on, "terms": terms, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _eom(days, **extra):
    return Rule(name="t", kind=END_OF_MONTH, net_days=days, **extra)


# ── the rule ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "days, on, due",
    [
        # the issue's example: Nov 30 + 75 days
        (75, date(2026, 11, 19), date(2027, 2, 13)),
        # any day of the month gives the same due date
        (75, date(2026, 11, 1), date(2027, 2, 13)),
        (75, date(2026, 11, 30), date(2027, 2, 13)),
        # a 31st: Jan 31 + 30 days, the same from Jan 1
        (30, date(2027, 1, 31), date(2027, 3, 2)),
        (30, date(2027, 1, 1), date(2027, 3, 2)),
        # February: from Feb 28 in 2027, from Feb 29 in a leap year
        (30, date(2027, 2, 10), date(2027, 3, 30)),
        (30, date(2028, 2, 10), date(2028, 3, 30)),
        (1, date(2028, 2, 3), date(2028, 3, 1)),
        (1, date(2027, 2, 3), date(2027, 3, 1)),
        # 0 days: the last day of the month itself
        (0, date(2026, 12, 1), date(2026, 12, 31)),
        (0, date(2028, 2, 1), date(2028, 2, 29)),
        (0, date(2027, 4, 30), date(2027, 4, 30)),
        # across the year
        (31, date(2026, 12, 15), date(2027, 1, 31)),
    ],
)
def test_due_n_days_after_the_last_day_of_the_month(days, on, due):
    assert _eom(days).due_date(on) == due


def test_month_end_knows_every_month():
    assert month_end(date(2027, 2, 1)) == date(2027, 2, 28)
    assert month_end(date(2028, 2, 29)) == date(2028, 2, 29)
    assert month_end(date(2100, 2, 5)) == date(2100, 2, 28)  # not a leap year
    assert month_end(date(2000, 2, 5)) == date(2000, 2, 29)
    assert month_end(date(2026, 4, 30)) == date(2026, 4, 30)
    assert month_end(date(2026, 12, 1)) == date(2026, 12, 31)


def test_the_discount_counts_from_the_month_end_too():
    """A standard term's discount days run from where its due date does
    (the document's date); an end-of-month term's run from the month's end,
    so "2% 10, Net 30 EOM" is 2% by the 10th of the next month."""
    rule = _eom(30, discount_percent=D("2"), discount_days=10)
    assert rule.discount_date(date(2026, 11, 19)) == date(2026, 12, 10)
    assert rule.discount_date(date(2027, 1, 31)) == date(2027, 2, 10)
    assert _eom(30, discount_percent=D("2"), discount_days=0).discount_date(
        date(2027, 2, 3)
    ) == date(2027, 2, 28)
    assert _eom(30).discount_date(date(2026, 11, 19)) is None


@pytest.mark.parametrize(
    "rule, words",
    [
        (_eom(75), "Due 75 days after the end of the month"),
        (_eom(1), "Due 1 day after the end of the month"),
        (_eom(0), "Due on the last day of the month"),
        (
            _eom(30, discount_percent=D("2"), discount_days=10),
            "Due 30 days after the end of the month; 2% off if paid within "
            "10 days after the end of the month",
        ),
        (
            _eom(30, discount_percent=D("1.5"), discount_days=0),
            "Due 30 days after the end of the month; 1.5% off if paid by the "
            "end of the month",
        ),
        # the other kinds say what they said
        (Rule(name="t", net_days=30), "Due in 30 days"),
        (Rule(name="t", net_days=0), "Due on receipt"),
        (
            Rule(name="t", kind=DATE_DRIVEN, due_day=10, cutoff_days=5),
            "Due on day 10 of the month, the next month's when dated fewer "
            "than 5 days before it",
        ),
    ],
)
def test_the_rule_in_words(rule, words):
    assert describe(rule) == words


# ── the text ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, days",
    [
        ("Net 75 EOM", 75),
        ("net 75 eom", 75),
        ("NET 75 EOM", 75),
        ("75 days EOM", 75),
        ("1 day EOM", 1),
        ("Net 75 days EOM", 75),
        ("75 EOM", 75),
        ("Net75EOM", 75),
        ("  Net   75\tEOM ", 75),
        ("Net 75 EOM", 75),
        ("Ｎｅｔ ７５ ＥＯＭ", 75),  # full width, read after NFKC
        ("Net​ 75 EOM", 75),  # a zero-width space
        ("Net 075 EOM", 75),
        ("EOM", 0),
        ("Net EOM", 0),
        ("Net 0 EOM", 0),
        ("eom", 0),
    ],
)
def test_end_of_month_text_reads_as_end_of_month(text, days):
    assert eom_days(text) == days
    read = read_text(text)
    assert read.kind == END_OF_MONTH and read.net_days == days


@pytest.mark.parametrize(
    "text",
    [
        "EOM 30",
        "2/10 EOM",
        "Net -5 EOM",
        "Net 75 EOMX",
        "Net 75 E.O.M.",
        "Net 30 end of month",
        "Net 999999 EOM",  # past the calendar, as Net N text is
        "15th of following month",
        "",
        None,
    ],
)
def test_other_text_is_not_end_of_month(text):
    assert eom_days(text) is None
    assert read_text(text) is None


@pytest.mark.parametrize(
    "text, due",
    [
        # what main read, unchanged
        ("Net 30", "2026-11-18"),
        ("net 45", "2026-12-03"),
        ("Net 10", "2026-10-29"),
        ("30", "2026-11-18"),
        ("Due on Receipt", "2026-10-19"),
        ("due upon receipt", "2026-10-19"),
        ("COD", "2026-10-19"),
        ("Net 0", "2026-10-19"),
        ("Net 1_0", "2026-10-29"),
        ("", "2026-11-18"),  # blank: the default 30 days
        # end-of-month, new: Oct 31 + N
        ("Net 30 EOM", "2026-11-30"),
        ("Net 75 EOM", "2027-01-14"),
        ("EOM", "2026-10-31"),
    ],
)
def test_text_off_the_list_reads_as_it_did_and_eom_now_reads(client, text, due):
    d = _dates(client, "2026-10-19", text)
    assert d["due_date"] == due, text
    assert d["note"] is None, text


def test_text_nobody_reads_keeps_the_30_days_and_the_note(client):
    for text in ("EOM 30", "2/10 EOM", "Net 75 E.O.M.", "Net 30.0"):
        d = _dates(client, "2026-10-19", text)
        assert (
            d["due_date"]
            == (date(2026, 10, 19) + timedelta(days=FALLBACK_DAYS)).isoformat()
        )
        assert d["note"].startswith(f'"{text}" isn\'t on the terms list'), text
    # main read "net<TAB>45" as 30 days, said so, and still does
    d = _dates(client, "2026-10-19", "net\t45")
    assert d["due_date"] == "2026-11-18"
    assert d["note"].startswith('"net 45" isn\'t on the terms list')


def test_seeded_terms_keep_their_due_dates(client, db_session):
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db_session)
    db_session.commit()
    for name, due in (
        ("Due on Receipt", "2027-01-31"),
        ("Net 15", "2027-02-15"),
        ("Net 30", "2027-03-02"),  # a 31st, 30 days on: March 2
        ("Net 45", "2027-03-17"),
        ("Net 60", "2027-04-01"),
    ):
        assert _dates(client, "2027-01-31", name)["due_date"] == due, name
    # and in a leap February
    assert _dates(client, "2028-02-29", "Net 30")["due_date"] == "2028-03-30"


def test_date_driven_terms_keep_their_cutoff_and_short_months(client):
    _term(client, name="10th prox", kind="date_driven", due_day=10, cutoff_days=10)
    _term(client, name="Due the 31st", kind="date_driven", due_day=31)
    for on, terms, due in (
        ("2026-10-05", "10th prox", "2026-11-10"),  # 5 days before: next month
        ("2026-09-30", "10th prox", "2026-10-10"),  # exactly 10 days: this one
        ("2027-02-03", "Due the 31st", "2027-02-28"),
        ("2028-02-03", "Due the 31st", "2028-02-29"),
        ("2026-04-30", "Due the 31st", "2026-04-30"),
    ):
        assert _dates(client, on, terms)["due_date"] == due, (on, terms)


# ── the list ────────────────────────────────────────────────────────────


def test_an_end_of_month_term_on_the_list(client):
    t = _term(client, name="Net 75 EOM", kind="end_of_month", net_days=75)
    assert t["kind"] == "end_of_month"
    assert t["net_days"] == 75
    assert t["due_day"] is None and t["cutoff_days"] == 0
    assert t["description"] == "Due 75 days after the end of the month"
    listed = {r["name"]: r for r in client.get("/api/payment-terms").json()}
    assert listed["Net 75 EOM"]["description"] == t["description"]
    assert client.get(f"/api/payment-terms/{t['id']}").json()["kind"] == (
        "end_of_month"
    )
    d = _dates(client, "2026-11-19", "Net 75 EOM")
    assert d["due_date"] == "2027-02-13"


def test_a_list_name_need_not_say_eom(client):
    _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    assert _dates(client, "2027-01-31", "Month end + 30")["due_date"] == "2027-03-02"
    assert _dates(client, "2027-01-31", "month end +30")["due_date"] == "2027-03-02"


def test_an_end_of_month_discount_on_the_dates_route(client):
    _term(
        client,
        name="2% 10 Net 30 EOM",
        kind="end_of_month",
        net_days=30,
        discount_percent="2",
        discount_days=10,
    )
    d = _dates(client, "2026-11-19", "2% 10 Net 30 EOM")
    assert d["due_date"] == "2026-12-30"
    assert d["discount_date"] == "2026-12-10"
    assert d["discount_line"] == "2% discount if paid by Dec 10, 2026"


@pytest.mark.parametrize(
    "body, sentence",
    [
        (
            {"name": "X", "kind": "end_of_month"},
            "An end-of-month term needs the number of days after the end of the "
            "month until it's due.",
        ),
        (
            {"name": "X", "kind": "end_of_month", "net_days": -1},
            "Days after the end of the month must be 0 to 3650.",
        ),
        (
            {"name": "X", "kind": "end_of_month", "net_days": 3651},
            "Days after the end of the month must be 0 to 3650.",
        ),
        (
            {"name": "X", "kind": "end_of_month", "net_days": 30, "due_day": 10},
            "An end-of-month term is due a number of days after the end of the "
            "month; a day of the month belongs to a date-driven term.",
        ),
        (
            {"name": "X", "kind": "end_of_month", "net_days": 30, "cutoff_days": 5},
            "Only a date-driven term moves to the next month.",
        ),
        (
            {
                "name": "X",
                "kind": "end_of_month",
                "net_days": 30,
                "discount_percent": "2",
                "discount_day": 10,
            },
            "An end-of-month term is due a number of days after the end of the "
            "month; a day of the month belongs to a date-driven term.",
        ),
        (
            {
                "name": "X",
                "kind": "end_of_month",
                "net_days": 30,
                "discount_percent": "2",
                "discount_days": 31,
            },
            "The discount must be taken within the days after the end of the "
            "month until it's due, 0 to 30.",
        ),
        (
            {
                "name": "X",
                "kind": "end_of_month",
                "net_days": 30,
                "discount_percent": "2",
            },
            "An early-payment discount needs both the percent and when it must "
            "be paid by; leave both blank for none.",
        ),
    ],
)
def test_an_end_of_month_rule_that_cannot_work_is_refused(client, body, sentence):
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == sentence


def test_bounds_are_inclusive(client):
    _term(client, name="EOM 0 days", kind="end_of_month", net_days=0)
    _term(client, name="Ten years EOM", kind="end_of_month", net_days=3650)


def test_a_name_eom_text_reads_keeps_that_reading(client):
    r = client.post(
        "/api/payment-terms",
        json={"name": "Net 75 EOM", "kind": "standard", "net_days": 75},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == (
        '"Net 75 EOM" already means due 75 days after the end of the month, so '
        "a term by that name keeps that rule. Give this term another name."
    )
    r = client.post(
        "/api/payment-terms",
        json={"name": "EOM", "kind": "end_of_month", "net_days": 30},
    )
    assert r.status_code == 409
    assert "already means due on the last day of the month" in r.json()["detail"]
    # and "Net 99" stays a standard 99 days, never end-of-month
    r = client.post(
        "/api/payment-terms",
        json={"name": "Net 99", "kind": "end_of_month", "net_days": 99},
    )
    assert r.status_code == 409
    assert "already means due in 99 days" in r.json()["detail"]


def test_an_entry_saved_before_its_name_read_as_eom_keeps_its_rule(client, db_session):
    """A book that added "Net 30 EOM" as a plain 30 days before #1091 keeps
    that entry and its rule: the list is read first, and making it inactive
    is not refused for a reading its name gained since."""
    db_session.add(PaymentTerm(name="Net 30 EOM", kind="standard", net_days=30))
    db_session.commit()
    assert _dates(client, "2026-10-19", "Net 30 EOM")["due_date"] == "2026-11-18"
    term = db_session.query(PaymentTerm).filter_by(name="Net 30 EOM").one()
    got = _put(client, f"/api/payment-terms/{term.id}", {"is_active": False})
    assert got["is_active"] is False
    # a new rule for it is held to the reading
    r = client.put(f"/api/payment-terms/{term.id}", json={"net_days": 31})
    assert r.status_code == 409


def test_a_term_changes_kind_to_and_from_end_of_month(client):
    t = _term(
        client,
        name="Changeable",
        net_days=30,
        discount_percent="2",
        discount_days=10,
    )
    got = _put(client, f"/api/payment-terms/{t['id']}", {"kind": "end_of_month"})
    assert (got["kind"], got["net_days"], got["discount_days"]) == (
        "end_of_month",
        30,
        10,
    )
    assert got["description"] == (
        "Due 30 days after the end of the month; 2% off if paid within 10 days "
        "after the end of the month"
    )
    got = _put(
        client,
        f"/api/payment-terms/{t['id']}",
        {"kind": "date_driven", "due_day": 15, "discount_day": 5},
    )
    assert (got["net_days"], got["discount_days"], got["due_day"]) == (None, None, 15)
    got = _put(
        client,
        f"/api/payment-terms/{t['id']}",
        {"kind": "end_of_month", "net_days": 45, "discount_days": 5},
    )
    assert (got["due_day"], got["discount_day"], got["cutoff_days"]) == (None, None, 0)
    assert got["net_days"] == 45


def test_a_term_in_use_keeps_its_end_of_month_rule(
    client, seed_accounts, seed_customer
):
    t = _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    _invoice(client, seed_customer.id, "Month end + 30")
    r = client.put(f"/api/payment-terms/{t['id']}", json={"net_days": 45})
    assert r.status_code == 409
    assert "so its rule stays" in r.json()["detail"]


# ── the documents ───────────────────────────────────────────────────────


def test_an_invoice_and_its_due_date(client, seed_accounts, seed_customer):
    _term(client, name="Net 75 EOM", kind="end_of_month", net_days=75)
    inv = _invoice(client, seed_customer.id, "Net 75 EOM", on="2026-11-19")
    assert inv["due_date"] == "2027-02-13"
    assert inv["terms_note"] is None
    # off the list, the text reads the same way
    inv = _invoice(client, seed_customer.id, "Net 30 EOM", on="2027-01-31")
    assert inv["due_date"] == "2027-03-02"
    assert inv["terms_note"] is None


def test_an_invoice_follows_its_terms_and_date_before_it_is_settled(
    client, seed_accounts, seed_customer
):
    _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    inv = _invoice(client, seed_customer.id, "Net 30", on="2027-02-10")
    assert inv["due_date"] == "2027-03-12"
    # terms changed to the end-of-month term: the due date follows (server)
    got = _put(client, f"/api/invoices/{inv['id']}", {"terms": "Month end + 30"})
    assert got["due_date"] == "2027-03-30"
    # a date moved on the form: the form asks the invoice's own rule
    d = _dates(
        client, "2027-01-31", "Month end + 30", invoice_id=inv["id"], document="invoice"
    )
    assert d["due_date"] == "2027-03-02"
    got = _put(
        client,
        f"/api/invoices/{inv['id']}",
        {"date": "2027-01-31", "due_date": d["due_date"]},
    )
    assert got["due_date"] == "2027-03-02"
    # a memo-only save keeps the stored due date
    got = _put(client, f"/api/invoices/{inv['id']}", {"notes": "memo"})
    assert got["due_date"] == "2027-03-02"


def test_a_bill_follows_its_terms_and_date(client, seed_accounts):
    _term(client, name="Net 60 EOM", kind="end_of_month", net_days=60)
    vendor = _vendor(client, terms="Net 60 EOM")
    bill = _bill(client, vendor["id"], "Net 60 EOM", on="2026-11-19")
    assert bill["due_date"] == "2027-01-29"
    # a date moved into February: due from Feb 28
    got = _put(client, f"/api/bills/{bill['id']}", {"date": "2027-02-01"})
    assert got["due_date"] == "2027-04-29"
    got = _put(client, f"/api/bills/{bill['id']}", {"date": "2028-02-01"})
    assert got["due_date"] == "2028-04-29"  # Feb 29 + 60
    got = _put(client, f"/api/bills/{bill['id']}", {"terms": "Net 30"})
    assert got["due_date"] == "2028-03-02"
    # text off the list on a bill
    bill = _bill(client, vendor["id"], "75 days EOM", on="2026-11-03", number="B-2")
    assert bill["due_date"] == "2027-02-13"


def test_a_settled_document_keeps_its_stored_due_date(
    client, db_session, seed_accounts, seed_customer
):
    """An invoice saved with "Net 75 EOM" before the text was read got the
    old 30 days; it keeps that date on every save that leaves its date and
    terms alone."""
    from app.models.invoices import Invoice

    inv = _invoice(client, seed_customer.id, "Net 30", on="2026-11-19")
    row = db_session.get(Invoice, inv["id"])
    row.terms = "Net 75 EOM"
    row.payment_term_id = None
    row.due_date = date(2026, 12, 19)
    db_session.commit()
    got = _put(client, f"/api/invoices/{inv['id']}", {"terms": "Net 75 EOM"})
    assert got["due_date"] == "2026-12-19"
    got = _put(client, f"/api/invoices/{inv['id']}", {"notes": "memo"})
    assert got["due_date"] == "2026-12-19"


def test_the_printed_invoice_and_bill_show_the_terms_and_due_date(
    client, seed_accounts, seed_customer
):
    _term(client, name="Net 75 EOM", kind="end_of_month", net_days=75)
    inv = _invoice(client, seed_customer.id, "Net 75 EOM", on="2026-11-19")
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "Net 75 EOM" in html
    assert _format_date(date(2027, 2, 13)) in html
    vendor = _vendor(client, terms="Net 75 EOM")
    bill = _bill(client, vendor["id"], "Net 75 EOM", on="2026-11-19")
    html = client.get(f"/api/bills/{bill['id']}/print-preview").text
    assert "Net 75 EOM" in html
    assert _format_date(date(2027, 2, 13)) in html


def test_a_converted_estimate_takes_the_customers_end_of_month_terms(
    client, seed_accounts
):
    _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    cust = client.post(
        "/api/customers", json={"name": "Harbor Works", "terms": "Month end + 30"}
    ).json()
    assert cust["terms"] == "Month end + 30"
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": cust["id"],
            "date": "2026-10-05",
            "lines": [{"description": "Deck", "quantity": 1, "rate": "900"}],
        },
    ).json()
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code in (200, 201), r.text
    inv = r.json()
    assert inv["terms"] == "Month end + 30"
    on = date.fromisoformat(inv["date"])
    assert inv["due_date"] == (month_end(on) + timedelta(days=30)).isoformat()


def test_a_purchase_order_bill_takes_the_vendors_end_of_month_terms(
    client, seed_accounts
):
    vendor = _vendor(client, terms="Net 30 EOM")
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-11-19",
            "lines": [{"description": "Lumber", "quantity": 2, "rate": 50}],
        },
    )
    assert po.status_code == 201, po.text
    po = po.json()
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={
            "date": "2027-01-31",
            "lines": [
                {
                    "line_id": po["lines"][0]["id"],
                    "account_id": seed_accounts["5100"].id,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/bills/{r.json()['bill_id']}").json()
    assert bill["terms"] == "Net 30 EOM"
    assert bill["due_date"] == "2027-03-02"


def test_a_recurring_invoice_on_end_of_month_terms(
    db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice
    from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
    from app.services.recurring_service import generate_due_invoices

    rec = RecurringInvoice(
        customer_id=seed_customer.id,
        frequency="monthly",
        start_date=date(2027, 2, 3),
        next_due=date(2027, 2, 3),
        is_active=True,
        terms="Net 15 EOM",
        tax_rate=D("0"),
    )
    db_session.add(rec)
    db_session.flush()
    db_session.add(
        RecurringInvoiceLine(
            recurring_invoice_id=rec.id, quantity=1, rate=D("50"), description="Fee"
        )
    )
    db_session.commit()
    ids = generate_due_invoices(db_session, as_of=date(2027, 2, 3))
    assert db_session.get(Invoice, ids[0]).due_date == date(2027, 3, 15)


def test_a_memorized_bill_on_an_end_of_month_term(client, db_session, seed_accounts):
    from tests.test_payment_terms_664 import _account_id

    _term(client, name="Month end + 10", kind="end_of_month", net_days=10)
    vendor = _vendor(client, terms="Month end + 10")
    mem = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Rent",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-10-20",
            "vendor_id": vendor["id"],
            "terms": "Month end + 10",
            "lines": [
                {
                    "account_id": _account_id(db_session, "6500"),
                    "description": "Rent",
                    "amount": 400,
                }
            ],
        },
    )
    assert mem.status_code == 201, mem.text
    r = client.post(f"/api/memorized/{mem.json()['id']}/enter")
    assert r.status_code == 200, r.text
    entered = client.get(f"/api/bills/{r.json()['document_id']}").json()
    assert entered["due_date"] == "2026-11-10"


def test_a_pay_application_invoice_on_the_customers_eom_terms(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_pay_app_billing_flow import _approved, _invoice as _app_invoice
    from tests.test_pay_applications import _two_line_job

    seed_customer.terms = "Net 75 EOM"
    db_session.commit()
    job, sov = _two_line_job(client, seed_customer.id)
    inv = _app_invoice(db_session, _approved(client, job, sov, [30000, 0]))
    assert inv.terms == "Net 75 EOM"
    assert inv.date == date(2026, 7, 31)
    assert inv.due_date == date(2026, 10, 14)


def test_the_default_terms_setting_takes_an_end_of_month_term(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_pay_app_billing_flow import _approved, _invoice as _app_invoice
    from tests.test_pay_applications import _two_line_job

    _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    seed_customer.terms = None
    db_session.commit()
    r = client.put("/api/settings", json={"default_terms": "Month end + 30"})
    assert r.status_code == 200, r.text
    job, sov = _two_line_job(client, seed_customer.id)
    inv = _app_invoice(db_session, _approved(client, job, sov, [30000, 0]))
    assert inv.terms == "Month end + 30"
    assert inv.due_date == date(2026, 8, 30)


def test_a_customer_and_vendor_carry_an_end_of_month_term(client, seed_accounts):
    t = _term(client, name="Month end + 30", kind="end_of_month", net_days=30)
    client.post("/api/customers", json={"name": "Harbor Works", "terms": t["name"]})
    _vendor(client, terms=t["name"])
    got = client.get(f"/api/payment-terms/{t['id']}").json()
    assert got["elsewhere"] == 2


def test_aging_reads_the_end_of_month_due_date(client, seed_accounts, seed_customer):
    inv = _invoice(client, seed_customer.id, "Net 30 EOM", on="2026-09-10")
    assert inv["due_date"] == "2026-10-30"
    ar = client.get("/api/reports/ar-aging?as_of_date=2026-10-29").json()
    row = next(r for r in ar["items"] if r["customer_id"] == seed_customer.id)
    assert row["current"] == 100.0  # not yet due: Net 30 alone would be overdue
    ar = client.get("/api/reports/ar-aging?as_of_date=2026-11-05").json()
    row = next(r for r in ar["items"] if r["customer_id"] == seed_customer.id)
    assert row["current"] == 0.0 and row["total"] == 100.0


def test_a_csv_import_keeps_end_of_month_text(client):
    r = client.post(
        "/api/csv/import/vendors",
        files={"file": ("v.csv", b"Name,Terms\nBay Lumber,Net 75 EOM\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []
    assert client.get("/api/vendors").json()[0]["terms"] == "Net 75 EOM"
    r = client.post(
        "/api/csv/import/vendors",
        files={"file": ("v.csv", b"Name,Terms\nDock Supply,EOM 30\n", "text/csv")},
    )
    assert "Net 30 EOM" in r.json()["errors"][0]


# ── the page ────────────────────────────────────────────────────────────


def test_the_terms_form_offers_the_third_kind():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text()
    assert '<option value="end_of_month"' in js
    assert 'data-kind="end_of_month"' in js
    assert "paymentTermExample" in js
    # the example asks the server's parser, never computing a date itself
    assert "PaymentTerms.dates(today, `Net ${days} EOM`)" in js
