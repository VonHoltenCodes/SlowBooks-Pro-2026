"""The reports and the giving statement agree to the cent.

One rule for the giving statement, the acknowledgment letter and the
donation receipt's IRS sentence: a gift is money that came in, stated in
home-currency dollars.

- A donation receipt counts at what its payments that are not void paid on
  it, and is no gift once that is nothing (its payment voided: a returned
  check). It was counted at the receipt's total, paid or not, so a donor's
  statement listed $150.00 "received" that the cash basis Income by
  Customer, rightly, did not.
- A foreign-currency gift is stated at its own rate. EUR 150.00 at 1.25 was
  printed as $150.00 beside dollar gifts and summed with them; it is
  $187.50, the figure Income by Customer and the Profit and Loss carry.
- A payment with no reference has an empty Reference. It read "payment 7",
  the row's database id.
- A gift is dated the day its money came in, on the letter, the receipt's
  IRS block and the statement alike; money that came in on more than one
  day is listed by day in the IRS sentence.
- Only payments are money received: a credit applied to a receipt is not,
  and the receipt's window asks the acknowledgment route instead of reading
  the receipt's amount paid.

And two report figures:

- Income by Customer's count column counts invoices and sales receipts
  alike, so on a nonprofit three donation receipts read "Pledges 3" and the
  note spoke of "the pledges ... billed". It is headed "Invoices &
  Receipts", a nonprofit's "Pledges, Invoices & Donations", and the note
  says "the documents counted".
- Cash-basis Sales by Item's average price is the price: 3 at 1,333.33, a
  third of the invoice paid, read 1,333.37 (a rounded amount over a rounded
  quantity).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.services.donor_documents import collect_gifts
from tests.test_cash_basis import _invoice, _payment
from tests.test_donor_documents import _nonprofit, _receipt

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
D = Decimal


def _year(year, basis="cash"):
    return {
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "basis": basis,
    }


def _income_row(client, name, year=2026, basis="cash"):
    """The donor's Income by Customer row, or None when they have none."""
    r = client.get("/api/reports/income-by-customer", params=_year(year, basis))
    assert r.status_code == 200, r.text
    return {i["customer_name"]: i for i in r.json()["items"]}.get(name)


def _statement(db_session, donor, year=2026):
    db_session.expire_all()
    return collect_gifts(db_session, donor, year)


def _preview(client, kind, gift_id):
    r = client.get(f"/api/donors/gifts/{kind}/{gift_id}/acknowledgment/preview")
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, donor, when, amount, invoice_id=None, **over):
    body = {"customer_id": donor, "date": when, "amount": str(amount)}
    if invoice_id is not None:
        body["allocations"] = [{"invoice_id": invoice_id, "amount": str(amount)}]
    body.update(over)
    r = client.post("/api/payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# A donation receipt counts at what was paid on it
# ---------------------------------------------------------------------------


def test_a_receipt_whose_payment_is_voided_is_off_the_statement(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    donor, name = seed_customer.id, seed_customer.full_name
    made = _receipt(client, donor).json()
    receipt = made["invoice"]["id"]

    gifts = _statement(db_session, donor)
    assert gifts["totals"]["amount"] == D("150.00")
    assert _income_row(client, name)["total_sales"] == 150.00
    assert _preview(client, "invoice", receipt) == {
        "eligible": True,
        "amount": 150.0,
        "document_paid": 150.0,
        "reason": None,
    }

    # the check came back: the payment is voided, the receipt is open again
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    gifts = _statement(db_session, donor)
    assert gifts["cash"] == []
    assert gifts["totals"] == {
        "amount": D("0.00"),
        "fair_value": D("0.00"),
        "deductible": D("0.00"),
    }
    # as the cash basis report has it: nothing came in
    assert _income_row(client, name) is None
    # and no letter thanks the donor for it
    assert _preview(client, "invoice", receipt) == {
        "eligible": False,
        "amount": None,
        "reason": "Nothing has been received on this donation receipt",
    }
    pdf = f"/api/donors/gifts/invoice/{receipt}/acknowledgment/pdf"
    assert client.get(pdf).status_code == 400
    # the receipt's own page no longer says a contribution was received
    page = client.get(f"/api/invoices/{receipt}/print-preview").text
    assert "DONATION RECEIPT" in page
    assert "Acknowledgment of contribution" not in page
    assert "received by" not in page

    # part of it is made good: the receipt counts at what was paid, once
    _pay(client, donor, "2026-06-02", "60.00", receipt, reference="CK 88")
    gifts = _statement(db_session, donor)
    assert [(g["kind"], g["number"], g["amount"]) for g in gifts["cash"]] == [
        ("invoice", made["invoice"]["invoice_number"], D("60.00"))
    ]
    assert gifts["totals"]["amount"] == D("60.00")
    assert _income_row(client, name)["total_sales"] == 60.00
    assert _preview(client, "invoice", receipt)["amount"] == 60.0
    page = client.get(f"/api/invoices/{receipt}/print-preview").text
    assert "Contribution of $60.00 received by" in page


def test_a_void_receipt_gets_no_letter(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    made = _receipt(client, seed_customer.id).json()
    receipt = made["invoice"]["id"]
    # voided the way the window does it: the payment, then the receipt
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    assert client.post(f"/api/invoices/{receipt}/void").status_code == 200
    assert _preview(client, "invoice", receipt) == {
        "eligible": False,
        "amount": None,
        "reason": "This donation receipt is void",
    }
    assert _statement(db_session, seed_customer.id)["cash"] == []


def test_a_receipt_counts_in_the_year_its_money_came_in(
    client, db_session, seed_accounts, seed_customer
):
    """A December receipt whose check came back, made good in January: the
    gift is January's, as it is on the cash basis Income by Customer. The
    fair value of what the donor got back is deducted once, with the first
    money."""
    _nonprofit(client)
    donor, name = seed_customer.id, seed_customer.full_name
    made = _receipt(
        client,
        donor,
        date="2026-12-20",
        fair_value_amount="45",
        fair_value_description="gala dinner",
    ).json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2027-01-08", "100.00", receipt)
    _pay(client, donor, "2027-02-08", "50.00", receipt)

    assert _statement(db_session, donor, 2026)["cash"] == []
    assert _income_row(client, name, 2026) is None

    gifts = _statement(db_session, donor, 2027)
    [gift] = gifts["cash"]
    assert gift["date"] == date(2027, 1, 8)
    assert gift["number"] == made["invoice"]["invoice_number"]
    assert gifts["totals"] == {
        "amount": D("150.00"),
        "fair_value": D("45.00"),
        "deductible": D("105.00"),
    }
    assert _income_row(client, name, 2027)["total_sales"] == 150.00


def test_a_receipt_paid_across_two_years_is_split_between_them(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    donor, name = seed_customer.id, seed_customer.full_name
    made = _receipt(client, donor, date="2026-12-20", fair_value_amount="45").json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2026-12-28", "30.00", receipt)
    _pay(client, donor, "2027-01-08", "120.00", receipt)

    # the first money was less than the dinner was worth: none of it is
    # deductible, no more is deducted than was given, and the totals add up
    first = _statement(db_session, donor, 2026)
    assert first["totals"] == {
        "amount": D("30.00"),
        "fair_value": D("30.00"),
        "deductible": D("0.00"),
    }
    # dated the day the money came in, not the day the receipt was written
    assert first["cash"][0]["date"] == date(2026, 12, 28)
    assert _income_row(client, name, 2026)["total_sales"] == 30.00
    # the rest of the dinner comes off the next year's money: between the
    # two statements the fair value is deducted once, in full, and the
    # donor's deductible total is the receipt less the dinner
    second = _statement(db_session, donor, 2027)
    assert second["totals"] == {
        "amount": D("120.00"),
        "fair_value": D("15.00"),
        "deductible": D("105.00"),
    }
    assert _income_row(client, name, 2027)["total_sales"] == 120.00
    assert first["totals"]["deductible"] + second["totals"]["deductible"] == D(
        "150.00"
    ) - D("45.00")


def test_a_dinner_worth_more_than_the_first_years_money_is_deducted_in_full(
    client, db_session, seed_accounts, seed_customer
):
    """Receipt 150.00 with a 100.00 dinner, a cent paid on the last day of
    one year and the rest on the first of the next. The two statements
    together read 149.99 deductible, and the second said no goods or
    services were provided; the figure is 50.00, the one the letter
    prints."""
    from app.services.donor_documents import (
        NO_GOODS,
        gift_irs,
        giving_statement_irs_text,
        load_gift,
    )

    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(
        client,
        donor,
        date="2025-12-20",
        fair_value_amount="100.00",
        fair_value_description="Dinner",
    ).json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2025-12-31", "0.01", receipt)
    _pay(client, donor, "2026-01-01", "149.99", receipt)

    first = _statement(db_session, donor, 2025)
    assert first["totals"] == {
        "amount": D("0.01"),
        "fair_value": D("0.01"),
        "deductible": D("0.00"),
    }
    second = _statement(db_session, donor, 2026)
    assert second["totals"] == {
        "amount": D("149.99"),
        "fair_value": D("99.99"),
        "deductible": D("50.00"),
    }
    row = second["cash"][0]
    assert (row["fair_value_amount"], row["deductible"]) == (D("99.99"), D("50.00"))
    assert row["fair_value_description"] == "Dinner"
    # the statement no longer says nothing was given back
    company = {"company_name": "Riverbend Community Arts"}
    said = giving_statement_irs_text(company, second["totals"])
    assert NO_GOODS not in said
    assert "Deductible column" in said
    # the two years together are what the letter for the receipt prints
    db_session.expire_all()
    letter = gift_irs(company, load_gift(db_session, "invoice", receipt))
    assert letter["deductible_amount"] == 50.0
    assert "fair market value of $100.00 (Dinner)" in letter["text"]
    assert first["totals"]["deductible"] + second["totals"]["deductible"] == D("50.00")
    assert first["totals"]["fair_value"] + second["totals"]["fair_value"] == D("100.00")


def test_a_dinner_is_shared_out_by_the_money_each_year_brought(
    client, db_session, seed_accounts, seed_customer
):
    """60.00 one year and 90.00 the next on a 150.00 receipt with a 100.00
    dinner: 0.00 and 50.00 deductible, not 0.00 and 90.00. A voided payment
    covers none of it."""
    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(client, donor, date="2025-12-20", fair_value_amount="100.00").json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    bounced = _pay(client, donor, "2025-12-22", "150.00", receipt)
    assert client.post(f"/api/payments/{bounced['id']}/void").status_code == 200
    _pay(client, donor, "2025-12-28", "60.00", receipt)
    _pay(client, donor, "2026-01-08", "90.00", receipt)

    assert _statement(db_session, donor, 2025)["totals"] == {
        "amount": D("60.00"),
        "fair_value": D("60.00"),
        "deductible": D("0.00"),
    }
    assert _statement(db_session, donor, 2026)["totals"] == {
        "amount": D("90.00"),
        "fair_value": D("40.00"),
        "deductible": D("50.00"),
    }


def test_an_unpaid_receipt_offers_no_acknowledgment_buttons(
    client, db_session, seed_accounts, seed_customer
):
    """The receipt's window offered Acknowledgment (PDF) on a receipt whose
    payment was voided; the route refuses it, so the button opened a tab of
    raw JSON. The buttons show only when something has been received, and
    the window's deductible portion is worked from what was received, as
    the letter and the statement work it."""
    _nonprofit(client)
    made = _receipt(client, seed_customer.id, fair_value_amount="40.00").json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    # what the window reads, and what the button would open
    shown = client.get(f"/api/invoices/{receipt}").json()
    assert D(str(shown["amount_paid"])) == D("0.00")
    pdf = client.get(f"/api/donors/gifts/invoice/{receipt}/acknowledgment/pdf")
    assert pdf.status_code == 400

    assert _preview(client, "invoice", receipt)["eligible"] is False
    _window_asks_the_acknowledgment_route()


def _window_asks_the_acknowledgment_route():
    """The receipt's window shows the buttons, and works its deductible
    portion, from what the acknowledgment route answers: never from the
    receipt's amount paid, which a credit raises with no money received."""
    view = (JS / "sales_receipts.js").read_text(encoding="utf-8")
    assert (
        "ack = await API.get(`/donors/gifts/invoice/${sr.id}/acknowledgment/preview`)"
        in view
    )
    asked = next(ln for ln in view.splitlines() if "acknowledgment/preview" in ln)
    guard = view[: view.index(asked)].rstrip().splitlines()[-1]
    assert "Terms.isNonprofit() && sr.status !== 'void'" in guard
    buttons = next(ln for ln in view.splitlines() if "Acknowledgment (PDF)" in ln)
    assert "${ack && ack.eligible ? `<button" in buttons
    assert "amount_paid" not in buttons
    deductible = next(ln for ln in view.splitlines() if "Deductible portion" in ln)
    assert "sr.total - sr.fair_value_amount" not in deductible
    assert "sr.amount_paid" not in deductible
    assert "Math.max(received - Number(sr.fair_value_amount), 0)" in deductible
    assert "ack && ack.eligible ? Number(ack.document_paid || 0) : 0" in view


def test_a_credit_applied_to_a_receipt_is_not_money_received(
    client, db_session, seed_accounts, seed_customer
):
    """A receipt whose payment was voided and which then had a credit memo
    applied has an amount paid and no money received. The window showed the
    Acknowledgment (PDF) button on it (amount paid above zero), the route
    refused, and the button opened raw JSON; the window's deductible
    portion was worked from the credit. The window now asks the route."""
    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(client, donor, fair_value_amount="40.00").json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    memo = client.post(
        "/api/credit-memos",
        json={
            "customer_id": donor,
            "date": "2026-05-12",
            "lines": [{"description": "Credit", "quantity": 1, "rate": 50}],
        },
    )
    assert memo.status_code == 201, memo.text
    applied = client.post(
        f"/api/credit-memos/{memo.json()['id']}/apply",
        json={"invoice_id": receipt, "amount": 50},
    )
    assert applied.status_code == 200, applied.text

    shown = client.get(f"/api/invoices/{receipt}").json()
    assert D(str(shown["amount_paid"])) == D("50.00")
    # the route the button opens, and what the window now asks first
    pdf = client.get(f"/api/donors/gifts/invoice/{receipt}/acknowledgment/pdf")
    assert pdf.status_code == 400
    assert _preview(client, "invoice", receipt) == {
        "eligible": False,
        "amount": None,
        "reason": "Nothing has been received on this donation receipt",
    }
    assert _statement(db_session, donor)["cash"] == []
    _window_asks_the_acknowledgment_route()

    # money on top of the credit: the window's figures are the money's
    _pay(client, donor, "2026-06-01", "70.00", receipt)
    shown = client.get(f"/api/invoices/{receipt}").json()
    assert D(str(shown["amount_paid"])) == D("120.00")
    answer = _preview(client, "invoice", receipt)
    assert (answer["eligible"], answer["amount"], answer["document_paid"]) == (
        True,
        70.0,
        70.0,
    )


def test_the_window_gets_a_foreign_receipts_money_in_its_own_currency(
    client, db_session, seed_accounts, seed_customer
):
    """The window prints the receipt's figures in the receipt's currency:
    the route gives it what was paid in that currency beside the dollars
    the letter states."""
    _nonprofit(client)
    euro = _receipt(
        client,
        seed_customer.id,
        currency="EUR",
        exchange_rate="1.25",
        fair_value_amount="40",
    ).json()["invoice"]
    answer = _preview(client, "invoice", euro["id"])
    assert (answer["amount"], answer["document_paid"]) == (187.5, 150.0)


# ---------------------------------------------------------------------------
# The letter, the receipt and the statement date a gift the same day
# ---------------------------------------------------------------------------


def _letter(client, db_session, receipt):
    """(gift, letter body, IRS sentence) as the acknowledgment PDF and the
    email are built from them."""
    from app.models.contacts import Customer
    from app.services.donor_documents import (
        gift_irs,
        load_gift,
        render_acknowledgment,
    )
    from app.services.settings_service import get_all_settings

    db_session.expire_all()
    company = get_all_settings(db_session)
    gift = load_gift(db_session, "invoice", receipt)
    donor = db_session.get(Customer, gift["customer_id"])
    _subject, body = render_acknowledgment(db_session, company, donor, gift)
    return gift, body, gift_irs(company, gift)["text"]


def test_the_letter_dates_a_gift_the_day_its_money_came_in(
    client, db_session, seed_accounts, seed_customer
):
    """A December receipt whose check came back, made good in January. The
    giving statement puts it in January's year; the letter thanked the
    donor for a contribution "on Dec 20", the other tax year."""
    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(client, donor, date="2025-12-20").json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2026-01-05", "150.00", receipt)

    assert _statement(db_session, donor, 2025)["cash"] == []
    [listed] = _statement(db_session, donor, 2026)["cash"]
    assert listed["date"] == date(2026, 1, 5)

    gift, body, sentence = _letter(client, db_session, receipt)
    assert gift["date"] == listed["date"]
    assert "contribution of $150.00 on Jan 05, 2026" in body
    assert "Dec 20, 2025" not in body
    # one day's money: the sentence needs no list of days
    assert "It was received as" not in sentence
    # the receipt's own IRS block is handed the same day
    from app.models.invoices import Invoice
    from app.services.donor_documents import receipt_irs

    irs = receipt_irs({}, db_session.get(Invoice, receipt))
    assert irs["date"] == date(2026, 1, 5)


def test_a_letter_for_money_that_came_in_over_two_years_says_when(
    client, db_session, seed_accounts, seed_customer
):
    """A receipt paid a cent in December and the rest in January is on two
    giving statements. The letter stated the whole amount on the receipt's
    date; it now says how much came in when, the figures the two statements
    carry, on the letter and on the receipt's own IRS sentence."""
    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(
        client,
        donor,
        date="2025-12-20",
        fair_value_amount="40",
        fair_value_description="Dinner",
    ).json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2025-12-31", "0.01", receipt)
    _pay(client, donor, "2026-01-01", "100.00", receipt)
    _pay(client, donor, "2026-01-01", "49.99", receipt)

    first = _statement(db_session, donor, 2025)
    second = _statement(db_session, donor, 2026)
    assert first["totals"]["amount"] == D("0.01")
    assert second["totals"]["amount"] == D("149.99")
    assert first["cash"][0]["date"] == date(2025, 12, 31)
    assert second["cash"][0]["date"] == date(2026, 1, 1)

    gift, body, sentence = _letter(client, db_session, receipt)
    said = "It was received as $0.01 on Dec 31, 2025 and $149.99 on Jan 01, 2026."
    assert gift["date"] == date(2025, 12, 31)
    assert "Dec 20, 2025" not in body
    assert said in sentence and said in body
    assert "limited to $110.00" in sentence
    assert sentence.endswith("Please retain this acknowledgment for your tax records.")
    page = client.get(f"/api/invoices/{receipt}/print-preview").text
    assert said in page


def test_a_receipt_paid_the_day_it_was_written_reads_as_before(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    made = _receipt(client, seed_customer.id).json()
    gift, body, sentence = _letter(client, db_session, made["invoice"]["id"])
    assert gift["date"] == date(2026, 5, 9)
    assert "contribution of $150.00 on May 09, 2026" in body
    assert "It was received as" not in sentence
    assert "Contribution of $150.00 received by" in sentence


def test_a_receipt_an_import_wrote_with_no_payment_record_still_counts(
    client, db_session, seed_accounts, seed_customer
):
    """A receipt a report import wrote as paid, with no payment behind it,
    counts at its own amount paid; one that says nothing was paid does
    not."""
    _nonprofit(client)

    def imported(number, paid):
        inv = Invoice(
            invoice_number=number,
            customer_id=seed_customer.id,
            date=date(2026, 3, 3),
            due_date=date(2026, 3, 3),
            status=InvoiceStatus.PAID if paid else InvoiceStatus.SENT,
            is_sales_receipt=True,
            subtotal=D("80.00"),
            tax_rate=D("0"),
            tax_amount=D("0"),
            total=D("80.00"),
            amount_paid=D("80.00") if paid else D("0"),
            balance_due=D("0") if paid else D("80.00"),
        )
        db_session.add(inv)
        db_session.flush()
        db_session.add(
            InvoiceLine(
                invoice_id=inv.id,
                description="Gift",
                quantity=1,
                rate=D("80.00"),
                amount=D("80.00"),
                line_order=0,
            )
        )
        db_session.commit()
        return inv.id

    paid, unpaid = imported("IMP-1", True), imported("IMP-2", False)
    gifts = _statement(db_session, seed_customer.id)
    assert [(g["id"], g["amount"]) for g in gifts["cash"]] == [(paid, D("80.00"))]
    assert _preview(client, "invoice", unpaid)["eligible"] is False


# ---------------------------------------------------------------------------
# A foreign-currency gift is stated in home-currency dollars
# ---------------------------------------------------------------------------


def test_a_foreign_currency_receipt_is_stated_in_dollars(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    donor, name = seed_customer.id, seed_customer.full_name
    euro = _receipt(
        client,
        donor,
        currency="EUR",
        exchange_rate="1.25",
        fair_value_amount="40",
        fair_value_description="gala dinner",
    )
    assert euro.status_code == 201, euro.text
    euro = euro.json()["invoice"]
    plain = _receipt(
        client,
        donor,
        date="2026-05-10",
        lines=[{"description": "Gift", "quantity": 1, "rate": "100.00"}],
    )
    assert plain.status_code == 201, plain.text

    gifts = _statement(db_session, donor)
    assert [g["amount"] for g in gifts["cash"]] == [D("187.50"), D("100.00")]
    # the dinner was typed in euros too: EUR 40.00 is $50.00
    assert gifts["cash"][0]["fair_value_amount"] == D("50.00")
    assert gifts["cash"][0]["deductible"] == D("137.50")
    assert gifts["totals"] == {
        "amount": D("287.50"),
        "fair_value": D("50.00"),
        "deductible": D("237.50"),
    }
    # the statement and the report agree for the donor, to the cent
    row = _income_row(client, name)
    assert (row["total_sales"], row["total_paid"]) == (287.50, 287.50)
    assert _income_row(client, name, basis="accrual")["total_sales"] == 287.50

    # the letter, and the IRS sentence on the receipt itself, say dollars
    assert _preview(client, "invoice", euro["id"])["amount"] == 187.5
    page = client.get(f"/api/invoices/{euro['id']}/print-preview").text
    assert "In exchange for this contribution of $187.50" in page
    assert "fair market value of $50.00 (gala dinner)" in page
    assert "limited to $137.50" in page
    assert "contribution of $150.00" not in page
    # the receipt's own table stays in its own currency
    assert "EUR 150.00" in page and "EUR 110.00" in page


def test_a_foreign_currency_pledge_payment_is_stated_in_dollars(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    donor, name = seed_customer.id, seed_customer.full_name
    pledge = client.post(
        "/api/invoices",
        json={
            "customer_id": donor,
            "date": "2026-02-01",
            "tax_rate": "0",
            "is_pledge": True,
            "currency": "EUR",
            "exchange_rate": "1.25",
            "lines": [{"description": "Pledge", "quantity": 1, "rate": "1000"}],
        },
    ).json()
    paid = _pay(
        client,
        donor,
        "2026-06-01",
        "400.00",
        pledge["id"],
        currency="EUR",
        exchange_rate="1.25",
    )

    gifts = _statement(db_session, donor)
    assert [g["amount"] for g in gifts["cash"]] == [D("500.00")]
    assert gifts["totals"]["amount"] == D("500.00")
    assert _income_row(client, name)["total_sales"] == 500.00
    assert _preview(client, "payment", paid["id"])["amount"] == 500.0


def test_a_payment_is_stated_at_its_own_rate(
    client, db_session, seed_accounts, seed_customer
):
    """EUR 400.00 received when the euro was 1.30 is a gift of $520.00, what
    reached the bank, whatever rate the pledge was written at."""
    _nonprofit(client)
    donor = seed_customer.id
    pledge = client.post(
        "/api/invoices",
        json={
            "customer_id": donor,
            "date": "2026-02-01",
            "tax_rate": "0",
            "is_pledge": True,
            "currency": "EUR",
            "exchange_rate": "1.25",
            "lines": [{"description": "Pledge", "quantity": 1, "rate": "1000"}],
        },
    ).json()
    _pay(
        client,
        donor,
        "2026-06-01",
        "400.00",
        pledge["id"],
        currency="EUR",
        exchange_rate="1.30",
    )
    assert _statement(db_session, donor)["totals"]["amount"] == D("520.00")


# ---------------------------------------------------------------------------
# A payment with no reference
# ---------------------------------------------------------------------------


def test_a_payment_with_no_reference_shows_none(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    donor = seed_customer.id
    # two payments first, so a row id could not pass for anything else
    _pay(client, donor, "2026-01-05", "10.00", reference="CK 1")
    _pay(client, donor, "2026-01-06", "20.00", check_number="2002")
    bare = _pay(client, donor, "2026-01-07", "400.00")
    assert bare["reference"] is None and bare["check_number"] is None

    gifts = _statement(db_session, donor)
    assert [g["number"] for g in gifts["cash"]] == ["CK 1", "2002", None]
    assert not any("payment" in str(g["number"]) for g in gifts["cash"])

    # the letter names no reference, and its file is named by the gift
    base = f"/api/donors/gifts/payment/{bare['id']}/acknowledgment"
    pdf = client.get(f"{base}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert f"Acknowledgment_{bare['id']}.pdf" in pdf.headers["content-disposition"]
    assert "payment" not in pdf.headers["content-disposition"]

    from app.services.donor_documents import load_gift, render_acknowledgment
    from app.services.settings_service import get_all_settings

    gift = load_gift(db_session, "payment", bare["id"])
    _subject, body = render_acknowledgment(
        db_session, get_all_settings(db_session), seed_customer, gift
    )
    assert "contribution of $400.00 on" in body
    assert "(payment" not in body and "()" not in body

    statement = client.get(f"/api/donors/{donor}/giving-statement/pdf?year=2026")
    assert statement.status_code == 200 and statement.content[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Income by Customer: the count column and the note
# ---------------------------------------------------------------------------


def _csv(client, basis):
    r = client.get("/api/reports/income-by-customer/csv", params=_year(2026, basis))
    assert r.status_code == 200, r.text
    return r.text


def test_contributions_by_donor_names_every_document_it_counts(
    client, seed_accounts, seed_customer
):
    """Three donation receipts and no pledge: the count is not headed
    "Pledges", and the note does not say pledges billed it."""
    _nonprofit(client)
    for day in ("2026-05-09", "2026-05-10", "2026-05-11"):
        assert _receipt(client, seed_customer.id, date=day).status_code == 201
    name = seed_customer.full_name
    for basis in ("accrual", "cash"):
        text = _csv(client, basis)
        assert "Report,Contributions by Donor" in text
        assert '"Pledges, Invoices & Donations",Sales,Sales Tax,Paid,Balance' in text
        assert text.count("Donor,") == 1 and "Donor,Pledges," not in text
        assert f"{name},3,450.00,0.00,450.00,0.00" in text
        note = next(ln for ln in text.splitlines() if ln.startswith("Note,"))
        assert "the documents counted" in note
        assert "ledge" not in note and "nvoice" not in note
        data = client.get(
            "/api/reports/income-by-customer", params=_year(2026, basis)
        ).json()
        assert "the documents counted" in data["row_rule"]
        assert "invoice" not in data["row_rule"]
        pdf = client.get(
            "/api/reports/income-by-customer/pdf", params=_year(2026, basis)
        )
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"


def test_income_by_customer_names_receipts_for_a_business_too(
    client, seed_accounts, seed_customer
):
    """A business's sales receipts were counted under "Invoices" the same
    way."""
    assert _receipt(client, seed_customer.id).status_code == 201
    _invoice(client, seed_customer.id, "2026-05-12", [{"rate": 40}])
    text = _csv(client, "accrual")
    assert "Report,Income by Customer" in text
    assert "Customer,Invoices & Receipts,Sales,Sales Tax,Paid,Balance" in text
    assert f"{seed_customer.full_name},2,190.00,0.00,150.00,40.00" in text
    assert "TOTAL,2,190.00,0.00,150.00,40.00" in text


def test_the_pdf_and_the_screen_head_the_count_the_same(client, seed_accounts):
    from app.services.ledger_exports import (
        INCOME_BY_CUSTOMER_COUNT,
        income_by_customer_sections,
    )
    from app.services.terminology import Terms

    data = client.get(
        "/api/reports/income-by-customer", params=_year(2026, "cash")
    ).json()
    for mode, who, count in (
        ("business", "Customer", "Invoices & Receipts"),
        ("nonprofit", "Donor", "Pledges, Invoices & Donations"),
    ):
        [section] = income_by_customer_sections(data, Terms(mode))
        assert section["columns"] == [
            who,
            count,
            "Sales",
            "Sales Tax",
            "Paid",
            "Balance owed now",
        ]
        assert "the documents counted" in section["note"]
        assert "pledge" not in section["note"] and "invoice" not in section["note"]
    # the screen's heading is the same key, through T(), escaped
    view = (JS / "reports.js").read_text(encoding="utf-8")
    start = view.index("async incomeByCustomer(")
    view = view[start : view.index("\n    },\n", start)]
    assert f"${{escapeHtml(T('{INCOME_BY_CUSTOMER_COUNT}'))}}" in view
    assert "T('Invoices')" not in view
    # and the key is in both dictionaries, the same
    from app.services.terminology import NONPROFIT

    assert NONPROFIT[INCOME_BY_CUSTOMER_COUNT] == "Pledges, Invoices & Donations"
    terms_js = (JS / "terms.js").read_text(encoding="utf-8")
    assert '"Invoices & Receipts": "Pledges, Invoices & Donations",' in terms_js


# ---------------------------------------------------------------------------
# Cash-basis Sales by Item: the average price is the price
# ---------------------------------------------------------------------------


def _sales_by_item(client, basis, month="10", last="31"):
    r = client.get(
        "/api/reports/sales-by-item",
        params={
            "start_date": f"2026-{month}-01",
            "end_date": f"2026-{month}-{last}",
            "basis": basis,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_cash_sales_by_item_average_price_is_the_price_sold_at(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.accounts import Account, AccountType

    held = Account(
        account_number="2450",
        name="Deposits held",
        account_type=AccountType.LIABILITY,
        detail_type="other_current_liability",
        balance=D("0"),
    )
    db_session.add(held)
    db_session.commit()
    widget = client.post(
        "/api/items", json={"name": "Widget", "item_type": "service", "rate": 1333.33}
    ).json()
    other = client.post(
        "/api/items",
        json={
            "name": "Security deposit",
            "item_type": "service",
            "is_taxable": False,
            "income_account_id": held.id,
        },
    )
    assert other.status_code == 201, other.text
    other = other.json()
    inv = _invoice(
        client,
        seed_customer.id,
        "2026-09-10",
        [
            {
                "item_id": widget["id"],
                "quantity": 3,
                "rate": 1333.33,
                "is_taxable": True,
            },
            {"item_id": other["id"], "quantity": 1, "rate": 200},
        ],
        tax_rate=0.0725,
    )
    _payment(client, seed_customer.id, "2026-10-05", 1500, [(inv["id"], 1500)])

    rows = {r["item_name"]: r for r in _sales_by_item(client, "cash")["items"]}
    # the columns are what they were: the paid part, rounded for display
    assert rows["Widget"]["quantity"] == 1.0022
    assert rows["Widget"]["amount"] == 1336.30
    # and the average is the price, not 1,336.30 / 1.0022 = 1,333.37
    assert rows["Widget"]["average_price"] == 1333.33

    text = client.get(
        "/api/reports/sales-by-item/csv",
        params={"start_date": "2026-10-01", "end_date": "2026-10-31", "basis": "cash"},
    ).text
    assert "Widget,service,1.0022,1336.30,1333.33" in text


def test_cash_average_price_holds_when_almost_nothing_is_paid(
    client, seed_accounts, seed_customer
):
    """1.50 paid on one unit at 999.00: the quantity share rounds to 0.0015
    (1.50 / 0.0015 = 1,000.00), and a smaller payment's to nothing."""
    item = client.post(
        "/api/items", json={"name": "Unit", "item_type": "service", "rate": 999}
    ).json()
    inv = _invoice(
        client,
        seed_customer.id,
        "2026-09-10",
        [{"item_id": item["id"], "quantity": 1, "rate": 999}],
    )
    _payment(client, seed_customer.id, "2026-10-05", 1.5, [(inv["id"], 1.5)])
    [row] = _sales_by_item(client, "cash")["items"]
    assert (row["quantity"], row["amount"]) == (0.0015, 1.50)
    assert row["average_price"] == 999.00

    _payment(client, seed_customer.id, "2026-11-05", 0.04, [(inv["id"], 0.04)])
    [row] = _sales_by_item(client, "cash", "11", "30")["items"]
    assert (row["quantity"], row["amount"]) == (0.0, 0.04)
    assert row["average_price"] == 999.00


def test_accrual_average_price_is_unchanged(client, seed_accounts, seed_customer):
    item = client.post(
        "/api/items", json={"name": "Unit", "item_type": "service", "rate": 10}
    ).json()
    _invoice(
        client,
        seed_customer.id,
        "2026-10-10",
        [{"item_id": item["id"], "quantity": 3, "rate": 10}],
    )
    _invoice(
        client,
        seed_customer.id,
        "2026-10-11",
        [{"item_id": item["id"], "quantity": 1, "rate": 14}],
    )
    [row] = _sales_by_item(client, "accrual")["items"]
    assert (row["quantity"], row["amount"], row["average_price"]) == (4.0, 44.0, 11.0)


def test_a_part_paid_receipts_irs_block_is_handed_what_was_received(
    client, db_session, seed_accounts, seed_customer
):
    """What the printed receipt's IRS block is given for a receipt part
    paid on a later day: the amount received and the day it came in, in
    the receipt's own currency, beside the sentence worked from them."""
    from app.models.invoices import Invoice
    from app.services.donor_documents import receipt_irs

    _nonprofit(client)
    donor = seed_customer.id
    made = _receipt(
        client, donor, fair_value_amount="40.00", fair_value_description="Dinner"
    ).json()
    receipt = made["invoice"]["id"]
    assert client.post(f"/api/payments/{made['payment']['id']}/void").status_code == 200
    _pay(client, donor, "2026-06-01", "30.00", receipt)
    db_session.expire_all()
    irs = receipt_irs({}, db_session.get(Invoice, receipt))
    assert (irs["amount_received"], irs["deductible_amount"]) == (30.0, 0.0)
    assert irs["date"] == date(2026, 6, 1)
    assert "In exchange for this contribution of $30.00" in irs["text"]
