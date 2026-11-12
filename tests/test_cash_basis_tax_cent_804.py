"""Cash basis: a part payment's income and sales-tax split ends to the cent
(#804, docs/design/cash-basis.md).

Each settlement of an invoice (a payment, an early-payment discount) takes
the rounded share of the running total settled less what the earlier ones
recognized, line by line. The one that settles the invoice carries the
rounding, so a fully paid invoice reads the same income and tax on both
bases, and a part paid never recognizes more tax than its rounded share of
what has been paid. Before, every payment rounded on its own: an invoice of
33.33 + 2.42 tax paid in thirds read 33.32 / 2.43 on the cash basis.
"""

from decimal import Decimal

from app.models.transactions import TransactionLine
from app.services import cash_basis
from tests.test_cash_basis import (
    _account_id,
    _each_entry_balances_on_every_basis,
    _invoice,
    _item,
    _payment,
)
from tests.test_cash_basis_edits import _edit_lines

D = Decimal


def _net(db_session, client, number, basis, invoice_id=None):
    """An account's credits less debits on one reading, every date."""
    db_session.expire_all()
    q = db_session.query(TransactionLine).filter(
        TransactionLine.account_id == _account_id(client, number)
    )
    if invoice_id is not None:
        q = q.filter(TransactionLine.recognizes_invoice_id == invoice_id)
    return sum(
        (
            D(str(ln.credit)) - D(str(ln.debit))
            for ln in q.execution_options(**cash_basis.reading(basis))
        ),
        D("0"),
    )


def _both_bases_agree(db_session, client, *numbers):
    for number in numbers:
        accrual = _net(db_session, client, number, "accrual")
        cash = _net(db_session, client, number, "cash")
        assert cash == accrual, (number, cash, accrual)


def _sales_tax(client, basis):
    r = client.get(
        "/api/reports/sales-tax?start_date=2026-01-01&end_date=2026-12-31"
        f"&basis={basis}"
    )
    assert r.status_code == 200, r.text
    return r.json()


def _thirds_invoice(client, cust, lines=None):
    """33.33 of service and 2.42 of tax (7.25%): 35.75, the issue's case."""
    inv = _invoice(
        client, cust, "2026-03-01", lines or [{"rate": "33.33"}], tax_rate=0.0725
    )
    assert D(inv["total"]) == D("35.75")
    return inv


def _pay_in_thirds(client, cust, inv):
    for day, amount in (("03-10", "11.92"), ("03-20", "11.92"), ("04-02", "11.91")):
        _payment(client, cust, f"2026-{day}", amount, [(inv["id"], amount)])


def test_an_invoice_paid_in_thirds_reads_its_income_and_tax_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    _pay_in_thirds(client, cust, inv)

    assert _net(db_session, client, "4000", "cash") == D("33.33")
    assert _net(db_session, client, "2200", "cash") == D("2.42")
    _both_bases_agree(db_session, client, "4000", "2200", "1100")
    _each_entry_balances_on_every_basis(db_session)


def test_each_third_takes_the_running_total_less_what_was_taken_before(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    paid = D("0")
    taken = []
    for day, amount in (("03-10", "11.92"), ("03-20", "11.92"), ("04-02", "11.91")):
        _payment(client, cust, f"2026-{day}", amount, [(inv["id"], amount)])
        paid += D(amount)
        tax = _net(db_session, client, "2200", "cash")
        # never more tax than its rounded share of what has been paid
        assert tax == cash_basis._q(D("2.42") * paid / D("35.75")), (paid, tax)
        taken.append(tax)
    assert taken == [D("0.81"), D("1.61"), D("2.42")]


def test_the_cash_sales_tax_report_agrees_with_sales_tax_payable_for_a_paid_invoice(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    _pay_in_thirds(client, cust, inv)

    cash = _sales_tax(client, "cash")
    accrual = _sales_tax(client, "accrual")
    assert [item["tax_amount"] for item in cash["items"]] == [0.81, 0.80, 0.81]
    assert cash["total_tax"] == accrual["total_tax"] == 2.42
    assert cash["total_sales"] == accrual["total_sales"] == 33.33
    assert cash["ledger"]["tax_posted"] == 2.42
    assert cash["ledger"]["difference"] == 0
    assert accrual["ledger"]["difference"] == 0


def test_a_part_dated_after_the_report_still_counts_toward_the_running_total(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    _pay_in_thirds(client, cust, inv)
    r = client.get(
        "/api/reports/sales-tax?start_date=2026-04-01&end_date=2026-04-30&basis=cash"
    )
    assert r.status_code == 200, r.text
    april = r.json()
    # the last third: 2.42 less the 1.61 March took, as 2200 reads
    assert april["total_tax"] == 0.81
    assert april["ledger"]["tax_posted"] == 0.81


def test_an_invoice_paid_a_cent_at_a_time_recognizes_its_tax_once(
    client, db_session, seed_accounts, seed_customer
):
    """0.10 of service and 0.01 of tax paid in eleven cents. Each cent
    rounded on its own was all income: the tax was never recognized. Now
    the tax cent lands on the sixth, when the running total's share of it
    rounds to a cent, and never again."""
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-03-01", [{"rate": "0.10"}], tax_rate=0.0725)
    assert D(inv["total"]) == D("0.11")
    for n in range(1, 12):
        _payment(client, cust, "2026-03-10", "0.01", [(inv["id"], "0.01")])
        tax = _net(db_session, client, "2200", "cash")
        assert tax == (D("0.01") if n >= 6 else D("0")), (n, tax)

    assert _net(db_session, client, "4000", "cash") == D("0.10")
    _both_bases_agree(db_session, client, "4000", "2200", "1100")
    assert _sales_tax(client, "cash")["total_tax"] == 0.01
    _each_entry_balances_on_every_basis(db_session)


def test_a_payment_of_99_99_then_one_of_a_cent_settle_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-03-01", [{"rate": "92.59"}], tax_rate=0.08)
    assert D(inv["total"]) == D("100.00")
    _payment(client, cust, "2026-03-10", "99.99", [(inv["id"], "99.99")])
    assert _net(db_session, client, "2200", "cash") <= D("7.41")
    _payment(client, cust, "2026-03-11", "0.01", [(inv["id"], "0.01")])

    assert _net(db_session, client, "4000", "cash") == D("92.59")
    assert _net(db_session, client, "2200", "cash") == D("7.41")
    _both_bases_agree(db_session, client, "4000", "2200", "1100")
    cash = _sales_tax(client, "cash")
    assert cash["total_tax"] == cash["ledger"]["tax_posted"] == 7.41
    _each_entry_balances_on_every_basis(db_session)


def test_a_discount_that_settles_the_invoice_leaves_both_bases_alike(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_payment_terms_664 import TWO_TEN, _term

    cust = seed_customer.id
    _term(client, **TWO_TEN)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "tax_rate": 0.0725,
            "lines": [{"description": "Service", "quantity": 1, "rate": "33.33"}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    _payment(client, cust, "2026-10-08", "11.92", [(inv["id"], "11.92")])
    # the rest, less a 0.71 discount (2% of 35.75 is 0.715)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust,
            "date": "2026-10-12",
            "amount": "23.12",
            "allocations": [
                {"invoice_id": inv["id"], "amount": "23.12", "discount": "0.71"}
            ],
            "discount_account_id": _account_id(client, "4900"),
        },
    )
    assert r.status_code == 201, r.text
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"

    assert _net(db_session, client, "4000", "cash") == D("33.33")
    assert _net(db_session, client, "2200", "cash") == D("2.42")
    _both_bases_agree(db_session, client, "4000", "2200", "4900", "1100")
    cash = _sales_tax(client, "cash")
    assert cash["total_tax"] == cash["ledger"]["tax_posted"] == 2.42
    _each_entry_balances_on_every_basis(db_session)


def test_an_edit_of_a_paid_invoice_rewrites_its_thirds_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    _pay_in_thirds(client, cust, inv)
    labor = _item(client, "Labor", "4100")
    # the same 33.33, now over two accounts
    _edit_lines(
        client,
        inv["id"],
        [{"rate": "20.00"}, {"rate": "13.33", "item_id": labor["id"]}],
    )

    assert _net(db_session, client, "4000", "cash") == D("20.00")
    assert _net(db_session, client, "4100", "cash") == D("13.33")
    assert _net(db_session, client, "2200", "cash") == D("2.42")
    _both_bases_agree(db_session, client, "4000", "4100", "2200", "1100")
    assert _sales_tax(client, "cash")["total_tax"] == 2.42
    _each_entry_balances_on_every_basis(db_session)


def test_the_start_up_backfill_settles_a_paid_invoice_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_cash_basis import _strip_cash_lines

    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    _pay_in_thirds(client, cust, inv)
    _strip_cash_lines(db_session)
    done = cash_basis.backfill(db_session)
    assert done["payments"] == 3 and done["failed"] == 0

    assert _net(db_session, client, "4000", "cash") == D("33.33")
    assert _net(db_session, client, "2200", "cash") == D("2.42")
    _both_bases_agree(db_session, client, "4000", "2200", "1100")
    _each_entry_balances_on_every_basis(db_session)


def test_a_voided_third_no_longer_counts_and_its_replacement_settles_the_rest(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _thirds_invoice(client, cust)
    first = _payment(client, cust, "2026-03-10", "11.92", [(inv["id"], "11.92")])
    _payment(client, cust, "2026-03-20", "11.92", [(inv["id"], "11.92")])
    r = client.post(f"/api/payments/{first['id']}/void")
    assert r.status_code == 200, r.text
    _payment(client, cust, "2026-04-02", "23.83", [(inv["id"], "23.83")])

    assert _net(db_session, client, "4000", "cash") == D("33.33")
    assert _net(db_session, client, "2200", "cash") == D("2.42")
    _both_bases_agree(db_session, client, "4000", "2200", "1100")
    assert _sales_tax(client, "cash")["total_tax"] == 2.42
    _each_entry_balances_on_every_basis(db_session)


def test_a_bill_paid_in_thirds_reads_its_expense_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_cash_basis import _bill, _bill_payment, _vendor

    vendor = _vendor(client)
    bill = _bill(
        client,
        vendor["id"],
        "2026-03-01",
        [
            {"rate": "10.00", "account_id": _account_id(client, "6000")},
            {"rate": "0.01", "account_id": _account_id(client, "5000")},
        ],
    )
    for day, amount in (("03-10", "3.34"), ("03-20", "3.34"), ("04-02", "3.33")):
        _bill_payment(
            client, vendor["id"], f"2026-{day}", amount, [(bill["id"], amount)]
        )

    _both_bases_agree(db_session, client, "6000", "5000", "2000")
    _each_entry_balances_on_every_basis(db_session)
