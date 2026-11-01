"""Payment terms, pass 5 (#665 skeptic review): the fixes this pass adds.

Each fix gets a test that fails without it. The house style of the earlier
passes is kept (tests/test_payment_terms_664.py): plain sentences, the same
`client`/`db_session`/`seed_accounts`/`seed_customer` fixtures.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.services.payment_terms import DATE_DRIVEN, STANDARD, Rule

D = Decimal

TWO_TEN = {
    "name": "2% 10 Net 30",
    "net_days": 30,
    "discount_percent": "2",
    "discount_days": 10,
}


def _term(client, **body):
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, customer_id, terms, on="2026-10-05", amount="100.00", **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": on,
            "terms": terms,
            "lines": [{"description": "Service", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client, name="Gulf Supply", terms="Net 30"):
    expense = next(
        a["id"]
        for a in client.get("/api/accounts").json()
        if a["account_number"] == "6000"
    )
    r = client.post(
        "/api/vendors",
        json={"name": name, "terms": terms, "default_expense_account_id": expense},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, terms, on="2026-10-05", amount="500.00", number="B-1"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": on,
            "terms": terms,
            "lines": [{"description": "Lumber", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _account_id(db, number):
    return db.query(Account).filter(Account.account_number == number).one().id


# ── finding 1: a date-driven discount a day past the due day ──────────────


@pytest.mark.parametrize(
    "on, expect",
    [
        ("2027-02-01", None),  # due Feb 28; Jan 31 is past, so no discount
        ("2028-02-10", None),  # due Feb 29 (leap); Jan 31 is past
        ("2027-01-31", date(2027, 1, 31)),  # dated on the day itself: due Feb 28
        ("2027-04-10", None),  # due Apr 30; Mar 31 is past, so no discount
        ("2027-03-31", date(2027, 3, 31)),  # dated on the day: due Apr 30
        ("2027-05-31", date(2027, 5, 31)),  # due Jun 30, discount May 31
    ],
)
def test_a_date_driven_discount_past_the_due_day_is_the_month_before(on, expect):
    """#665: a short month's clamp landed day 31 on the due day itself, so
    the discount ran through the due date instead of the month before."""
    rule = Rule(
        name="t",
        kind=DATE_DRIVEN,
        due_day=30,
        discount_day=31,
        discount_percent=D("2"),
    )
    y, m, d = (int(x) for x in on.split("-"))
    assert rule.discount_date(date(y, m, d)) == expect


def test_the_month_before_branch_has_a_test():
    """Delete the discount_day > due_day branch and this fails: it would
    read day 31 of the due month (clamped to the due day) and offer no
    discount instead of the 31st of the month before."""
    rule = Rule(
        name="t",
        kind=DATE_DRIVEN,
        due_day=30,
        discount_day=31,
        discount_percent=D("2"),
    )
    assert rule.discount_date(date(2027, 5, 31)) == date(2027, 5, 31)


def test_a_same_month_discount_already_passed_is_none():
    """Delete the `on <= last` guard and this fails: a same-month discount
    whose day fell before the document's own date is no discount."""
    rule = Rule(
        name="t",
        kind=DATE_DRIVEN,
        due_day=25,
        discount_day=10,
        discount_percent=D("2"),
    )
    # dated the 11th: the 10th has passed, the due day is the 25th
    assert rule.discount_date(date(2026, 11, 11)) is None
    # dated the 9th: the 10th is still ahead
    assert rule.discount_date(date(2026, 11, 9)) == date(2026, 11, 10)


# ── finding 2: find_term is deterministic, and the race is a 409 ──────────


def test_find_term_ties_to_the_lowest_id_on_a_tie(client, db_session):
    """#665 B4/B5: lower(trim(name)) in SQL and .first() with no ORDER BY
    could tie a document to either of two rows; now the lowest id wins, the
    same every time, with no SQL case folding."""
    from app.models.payment_terms import PaymentTerm
    from app.services.payment_terms import find_term

    a = PaymentTerm(name="Prox Net", kind=STANDARD, net_days=12)
    b = PaymentTerm(name="prox  net", kind=STANDARD, net_days=12)
    db_session.add_all([a, b])
    db_session.commit()
    for text in ("Prox Net", "PROX NET", "prox net"):
        assert find_term(db_session, text).id == min(a.id, b.id)


def test_a_case_variant_create_is_refused_quoting_the_entry(client):
    """#665: "race NET 20" beside "Race Net 20" both got 201; now the second
    is a 409 quoting the entry already there."""
    _term(client, name="Race Net 20", net_days=20)
    r = client.post("/api/payment-terms", json={"name": "race NET 20", "net_days": 20})
    assert r.status_code == 409
    assert r.json()["detail"] == '"Race Net 20" is already on the terms list.'


def test_a_racing_create_meets_the_unique_index_as_a_409(client, monkeypatch):
    """#665: eight parallel creates of one name gave a 500 IntegrityError;
    now the loser rolls back and answers the 409, quoting the winner. The
    race is simulated by letting the read-check pass, so the write meets the
    index."""
    from app.routes import payment_terms as routes

    _term(client, name="Flush Net", net_days=20)
    monkeypatch.setattr(routes, "_existing_named", lambda *a, **k: None)
    r = client.post("/api/payment-terms", json={"name": "Flush Net", "net_days": 20})
    assert r.status_code == 409
    assert r.json()["detail"] == '"Flush Net" is already on the terms list.'


# ── finding 3: look-alike names are one name ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Nеt 30",  # Cyrillic e (U+0435)
        "Net30",  # no space
        "Nеt 30",  # Cyrillic e and a no-break space
    ],
)
def test_a_look_alike_name_is_the_same_name(client, db_session, name):
    """#665 round 4: a Cyrillic e, a missing space, odd whitespace — a name
    that only looks different is the same name."""
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db_session)
    db_session.commit()
    r = client.post("/api/payment-terms", json={"name": name, "net_days": 45})
    assert r.status_code == 409, name
    assert r.json()["detail"] == '"Net 30" is already on the terms list.'


def test_the_letter_o_is_not_folded_to_the_digit_zero(client, db_session):
    """#665: folding O to 0 would make "1OO" and "100" one name, so "Net 3O"
    (letter O) is a name of its own beside "Net 30"."""
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db_session)
    db_session.commit()
    r = client.post("/api/payment-terms", json={"name": "Net 3O", "net_days": 7})
    assert r.status_code == 201, r.text


# ── finding 4: /dates says a due date before the document date ────────────


def test_dates_says_a_due_date_before_the_document_date(client):
    """#665: "Net -5" (a customer's own text) answered note null, then the
    save refused; now the note says so up front, as the save phrases it."""
    r = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-28", "terms": "Net -5"}
    )
    assert r.status_code == 200, r.text
    note = r.json()["note"]
    assert note == (
        "The due date (Oct 23, 2026) is before the invoice date (Oct 28, 2026). "
        "Pick a due date on or after the invoice date."
    )


# ── finding 5: the single GET refuses unknown query parameters ────────────


def test_the_single_get_refuses_an_unknown_query_parameter(client):
    """#665: GET /api/payment-terms/{id}?foo=1 answered 200; the list and
    /dates refuse one by name, and now so does the single GET."""
    term = _term(client, name="Lonely", net_days=5)
    r = client.get(f"/api/payment-terms/{term['id']}", params={"foo": "1"})
    assert r.status_code == 422
    assert r.json()["detail"] == "'foo' is not a parameter this accepts."


# ── finding 6: ids of 2**63 or more are refused, not a 500 ────────────────


def test_a_huge_id_is_refused_not_a_500(client, seed_accounts, seed_customer):
    """#665: 2**63 raised OverflowError (a 500) on three routes; now each is
    a 422 for the id it is given."""
    big = 2**63
    assert client.get(f"/api/payment-terms/{big}").status_code == 422
    r = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-01", "terms": "Net 30", "invoice_id": big},
    )
    assert r.status_code == 422
    r = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-01", "terms": "Net 30", "bill_id": big},
    )
    assert r.status_code == 422


# ── finding 7: an explicit pick ties a legacy document ────────────────────


def test_an_explicit_pick_ties_a_legacy_invoice_and_recomputes(
    client, seed_accounts, seed_customer
):
    """#665: a document whose stored text equals a new term's name could not
    be tied by sending the same text; an explicit payment_term_id now does,
    and the due date follows the rule as any pick does."""
    inv = _invoice(client, seed_customer.id, "Net 10th", on="2026-10-05")
    assert inv["due_date"] == "2026-11-04"  # the old 30 days, untied
    term = _term(
        client, name="Net 10th", kind="date_driven", due_day=10, cutoff_days=10
    )

    # the same text with no pick leaves it untied
    same = client.put(f"/api/invoices/{inv['id']}", json={"terms": "Net 10th"})
    assert same.status_code == 200, same.text
    assert same.json()["terms_note"] is not None
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 0

    # the same text with the pick ties it and recomputes the due date
    tied = client.put(
        f"/api/invoices/{inv['id']}",
        json={"terms": "Net 10th", "payment_term_id": term["id"]},
    )
    assert tied.status_code == 200, tied.text
    assert tied.json()["terms_note"] is None
    assert tied.json()["due_date"] == "2026-11-10"
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 1

    # a pick whose name is not the terms text is refused, naming both
    others = client.get("/api/payment-terms").json()
    net30 = next(t for t in others if t["name"] == "Net 30")
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"terms": "Net 10th", "payment_term_id": net30["id"]},
    )
    assert r.status_code == 409
    assert '"Net 10th"' in r.json()["detail"] and "Net 30" in r.json()["detail"]


# ── finding 8: an invoice PUT recomputes the due date from new terms ──────


def test_an_invoice_put_recomputes_the_due_date_when_terms_change(
    client, seed_accounts, seed_customer
):
    """#665: an invoice PUT that changed the terms but sent no due_date kept
    the old due date (bills recomputed, invoices did not)."""
    inv = _invoice(client, seed_customer.id, "Net 30")
    assert inv["due_date"] == "2026-11-04"
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "Net 15"})
    assert r.status_code == 200, r.text
    assert r.json()["due_date"] == "2026-10-20"  # 15 days from Oct 5


# ── finding 9: the CSV import matches the list with NFKC ──────────────────


def test_a_fullwidth_terms_cell_imports_as_the_list_name(client):
    """#665: "Ｎｅｔ ３０" (fullwidth) was refused by the import while the list
    and /dates read it as Net 30; now the import matches with the same key."""
    r = client.post(
        "/api/csv/import/customers",
        files={
            "file": (
                "c.csv",
                "Name,Terms\nWide Co,Ｎｅｔ ３０\n".encode("utf-8"),
                "text/csv",
            )
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []
    cust = client.get("/api/customers").json()[0]
    assert cust["terms"] == "Net 30"


def test_the_csv_import_refuses_an_inactive_terms_name(client):
    """#665 guard: the import offers the ACTIVE list; a term made inactive
    is no longer an option, so its name is refused (unless it reads as
    Net N)."""
    term = _term(client, name="Weekly Rest", net_days=7)
    client.put(f"/api/payment-terms/{term['id']}", json={"is_active": False})
    r = client.post(
        "/api/csv/import/customers",
        files={"file": ("c.csv", b"Name,Terms\nRest Co,Weekly Rest\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert "Weekly Rest" in r.json()["errors"][0]


# ── finding 10: the in-use wording ────────────────────────────────────────


def test_the_in_use_wording_is_singular_plural_and_drops_a_zero(
    client, seed_accounts, seed_customer
):
    """#665: "is on 0 invoices and bills ... and 6 customers ...". Drop the
    zero clause, say "1 invoice or bill" / "2 invoices and bills", and quote
    the existing entry on a duplicate."""
    # a term carried only elsewhere: the zero docs clause is dropped
    term = _term(client, name="Monthly Due", net_days=30)
    client.post("/api/customers", json={"name": "Alpha", "terms": "Monthly Due"})
    client.post("/api/customers", json={"name": "Beta", "terms": "Monthly Due"})
    r = client.put(f"/api/payment-terms/{term['id']}", json={"name": "Monthly"})
    assert r.status_code == 409
    assert r.json()["detail"].startswith(
        "Monthly Due is carried by 2 customers, vendors, schedules or settings, "
        "so its name stays."
    )
    assert "invoices and bills" not in r.json()["detail"]

    # two documents: the plural form
    two = _term(client, name="Due Soon", net_days=5)
    _invoice(client, seed_customer.id, "Due Soon")
    _invoice(client, seed_customer.id, "Due Soon")
    r = client.put(f"/api/payment-terms/{two['id']}", json={"net_days": 6})
    assert r.status_code == 409
    assert "is on 2 invoices and bills saved under it" in r.json()["detail"]


# ── finding 12: the note uses the company's noun ──────────────────────────


def test_the_note_uses_the_companys_noun_for_a_nonprofit(
    client, seed_accounts, seed_customer
):
    """#665: a pledge in a nonprofit read "from the invoice date"; now the
    note uses the company's own word."""
    assert (
        client.put("/api/settings", json={"company_type": "nonprofit"}).status_code
        == 200
    )
    inv = _invoice(client, seed_customer.id, "mystery 99 days")
    assert "from the pledge date" in inv["terms_note"]
    assert "invoice date" not in inv["terms_note"]
    # /dates says it too
    d = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-28", "terms": "Net -5", "document": "invoice"},
    ).json()
    assert "before the pledge date" in d["note"]


# ── finding 13: guards that had no test ───────────────────────────────────


def test_the_name_guard_applies_on_a_rename(client):
    """An unused term renamed to a name the old parser reads as other days
    is refused, as a POST of it is."""
    term = _term(client, name="Loose", net_days=45)
    r = client.put(f"/api/payment-terms/{term['id']}", json={"name": "Net 33"})
    assert r.status_code == 409
    assert r.json()["detail"].startswith('"Net 33" already means due in 33 days')


def test_include_inactive_shows_an_inactive_term(client):
    term = _term(client, name="Retired", net_days=9)
    client.put(f"/api/payment-terms/{term['id']}", json={"is_active": False})
    names = [t["name"] for t in client.get("/api/payment-terms").json()]
    assert "Retired" not in names
    with_inactive = [
        t["name"]
        for t in client.get(
            "/api/payment-terms", params={"include_inactive": "true"}
        ).json()
    ]
    assert "Retired" in with_inactive


def test_a_kind_change_to_standard_resets_the_cutoff(client):
    term = _term(client, name="Prox Cut", kind="date_driven", due_day=10, cutoff_days=5)
    r = client.put(
        f"/api/payment-terms/{term['id']}", json={"kind": "standard", "net_days": 20}
    )
    assert r.status_code == 200, r.text
    assert r.json()["cutoff_days"] == 0 and r.json()["net_days"] == 20


@pytest.mark.parametrize(
    "body, sentence",
    [
        (
            {"name": "X", "kind": "date_driven", "due_day": 10, "net_days": 5},
            "number of days belongs to a standard term",
        ),
        (
            {
                "name": "X",
                "kind": "date_driven",
                "due_day": 10,
                "discount_percent": "2",
                "discount_days": 5,
            },
            "number of days belongs to a standard term",
        ),
        (
            {
                "name": "X",
                "kind": "date_driven",
                "due_day": 10,
                "discount_percent": "2",
                "discount_day": 0,
            },
            "day of the month for the discount must be 1 to 31",
        ),
        (
            {
                "name": "X",
                "kind": "date_driven",
                "due_day": 10,
                "discount_percent": "2",
                "discount_day": 32,
            },
            "day of the month for the discount must be 1 to 31",
        ),
        (
            {"name": "X", "net_days": 30, "discount_percent": "0", "discount_days": 5},
            "more than 0 and under 100",
        ),
    ],
)
def test_a_bad_rule_is_refused_in_a_sentence(client, body, sentence):
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 422, r.text
    # the sentence, whitespace in the source aside
    assert " ".join(sentence.split()) in " ".join(r.json()["detail"].split())


def test_dates_refuses_invoice_id_and_bill_id_together(client):
    r = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-01", "terms": "Net 30", "invoice_id": 1, "bill_id": 1},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "Send invoice_id or bill_id, not both."


# ── finding 14: discount_taken on the response and the printed document ───


def _receive(client, customer_id, invoice_id, amount, discount, account_id):
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-10-12",
            "amount": amount,
            "allocations": [
                {"invoice_id": invoice_id, "amount": amount, "discount": discount}
            ],
            "discount_account_id": account_id,
        },
    )


def test_a_paid_discounted_invoice_shows_what_was_taken(
    client, db_session, seed_accounts, seed_customer
):
    """#665: once the discount is taken the offer is spent — the response
    carries discount_taken and clears the offer, and the printed invoice
    says what was taken, not what could be."""
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    acct = _account_id(db_session, "4900")
    r = _receive(client, seed_customer.id, inv["id"], "98.00", "2.00", acct)
    assert r.status_code == 201, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert got["status"] == "paid"
    assert got["discount_taken"]["amount"] == "2.00"
    assert got["discount_taken"]["number"].startswith("CM-")
    assert got["discount_line"] is None and got["discount_date"] is None
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "Early-payment discount taken" in html
    assert "if paid by" not in html


def test_an_unpaid_discounted_invoice_still_offers(
    client, db_session, seed_accounts, seed_customer
):
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert got["discount_taken"] is None
    assert got["discount_line"] == "2% discount if paid by Oct 15, 2026"


def test_a_paid_discounted_bill_shows_what_was_taken(client, db_session, seed_accounts):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    bill = _bill(client, vendor["id"], "2% 10 Net 30")
    acct = _account_id(db_session, "4900")
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 490.0,
            "allocations": [
                {"bill_id": bill["id"], "amount": 490.0, "discount": "10.00"}
            ],
            "discount_account_id": acct,
        },
    )
    assert r.status_code == 201, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert got["status"] == "paid"
    assert got["discount_taken"]["amount"] == "10.00"
    assert got["discount_taken"]["number"].startswith("VC-")
    assert got["discount_line"] is None
    html = client.get(f"/api/bills/{bill['id']}/print-preview").text
    assert "Early-payment discount taken" in html
    assert "if paid by" not in html
