"""A new sales document made through the API starts at the company's tax
rate, as the forms start one (#1129).

The forms start a new invoice, sales receipt, estimate, credit memo or
recurring invoice at Settings' Default Tax Rate; the API started the same
document at 0% when the request left tax_rate out, so an assistant working
through the API made a taxable customer's invoice untaxed. Now an omitted
rate (or null) is the company's default; a rate sent, 0 included, is kept;
a non-taxable customer keeps the rate and is charged no tax, as on the
form; an update that leaves the rate out changes nothing. The vendored
Switchboard client's own fill-in (tax_rate_source) lands on the same rate.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.transactions import TransactionLine
from app.services.books_check import books_check
from app.switchboard.client import ApiError
from app.switchboard.tools import sales_tax

TODAY = date.today().isoformat()
LINE = {"description": "Shelf bracket", "quantity": 2, "rate": 50}  # $100.00

# route → (the body without a rate, the ledger sign of its sales tax: an
# invoice or a sale owes it, a credit memo gives it back; None posts nothing)
ROUTES = {
    "/api/invoices": ({"date": TODAY}, 1),
    "/api/sales-receipts": ({"date": TODAY}, 1),
    "/api/estimates": ({"date": TODAY}, None),
    "/api/credit-memos": ({"date": TODAY}, -1),
    "/api/recurring": ({"frequency": "monthly", "start_date": TODAY}, None),
}


def _default_rate(client, pct):
    r = client.put("/api/settings", json={"default_tax_rate": pct})
    assert r.status_code == 200, r.text


def _customer(client, name, taxable):
    r = client.post("/api/customers", json={"name": name, "is_taxable": taxable})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _create(client, route, customer_id, **rate):
    base, _ = ROUTES[route]
    body = {**base, "customer_id": customer_id, "lines": [dict(LINE)], **rate}
    r = client.post(route, json=body)
    assert r.status_code in (200, 201), r.text
    out = r.json()
    # a sales receipt answers the invoice beside its payment
    return out["invoice"] if route == "/api/sales-receipts" else out


def _sales_tax_posted(db_session) -> Decimal:
    """What sits on Sales Tax Payable (2200): credits less debits."""
    acct = db_session.query(Account).filter(Account.account_number == "2200").one()
    rows = db_session.query(TransactionLine).filter_by(account_id=acct.id).all()
    return sum(
        (Decimal(str(r.credit or 0)) - Decimal(str(r.debit or 0)) for r in rows),
        Decimal("0"),
    )


def _books_clean(db_session):
    rows = books_check(db_session)["rows"]
    assert [r["name"] for r in rows if r["status"] == "difference"] == []


@pytest.mark.parametrize("default_pct", ["0", "8.25"])
@pytest.mark.parametrize("taxable", [True, False], ids=["taxable", "exempt"])
@pytest.mark.parametrize(
    "sent", [None, "0", "0.05"], ids=["omitted", "explicit-0", "explicit-rate"]
)
@pytest.mark.parametrize("route", list(ROUTES))
def test_a_new_sales_document_starts_at_the_company_rate_unless_one_is_sent(
    client, db_session, seed_accounts, route, sent, taxable, default_pct
):
    _default_rate(client, default_pct)
    cid = _customer(client, "Harbor Hardware", taxable)
    rate = {} if sent is None else {"tax_rate": float(sent)}
    doc = _create(client, route, cid, **rate)

    want_rate = Decimal(sent) if sent is not None else Decimal(default_pct) / 100
    assert Decimal(str(doc["tax_rate"])) == want_rate
    if route == "/api/recurring":
        return  # a schedule's rate goes onto each invoice it makes
    want_tax = (
        Decimal("0.00") if not taxable else (100 * want_rate).quantize(Decimal("0.01"))
    )
    assert Decimal(str(doc["tax_amount"])) == want_tax
    assert Decimal(str(doc["total"])) == Decimal("100.00") + want_tax

    sign = ROUTES[route][1]
    if sign is not None:
        # the tax is on Sales Tax Payable, and the books agree with themselves
        assert _sales_tax_posted(db_session) == sign * want_tax
        _books_clean(db_session)


def test_an_explicit_null_is_the_company_rate_too(client, seed_accounts):
    _default_rate(client, "6.5")
    cid = _customer(client, "Harbor Hardware", True)
    doc = _create(client, "/api/invoices", cid, tax_rate=None)
    assert Decimal(doc["tax_rate"]) == Decimal("0.065")
    assert Decimal(doc["tax_amount"]) == Decimal("6.50")


def test_a_credit_memo_for_an_invoice_still_takes_that_invoices_rate(
    client, seed_accounts
):
    _default_rate(client, "8.25")
    cid = _customer(client, "Harbor Hardware", True)
    inv = _create(client, "/api/invoices", cid, tax_rate=0.05)
    cm = _create(client, "/api/credit-memos", cid, original_invoice_id=inv["id"])
    assert Decimal(cm["tax_rate"]) == Decimal("0.05")


@pytest.mark.parametrize("route", ["/api/invoices", "/api/estimates"])
def test_an_update_that_leaves_the_rate_out_keeps_it(client, seed_accounts, route):
    _default_rate(client, "8.25")
    cid = _customer(client, "Harbor Hardware", True)
    doc = _create(client, route, cid, tax_rate=0)
    _default_rate(client, "9.5")
    r = client.put(f"{route}/{doc['id']}", json={"notes": "Called ahead"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["tax_rate"]) == Decimal("0")
    assert Decimal(r.json()["tax_amount"]) == Decimal("0.00")


def test_the_spec_says_what_a_left_out_rate_means(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in (
        "InvoiceCreate",
        "SalesReceiptCreate",
        "EstimateCreate",
        "CreditMemoCreate",
        "RecurringCreate",
    ):
        text = schemas[name]["properties"]["tax_rate"]["description"]
        assert "Left out" in text and "Default Tax Rate" in text, (name, text)
        assert "send 0 for no tax" in text, (name, text)


class _Api:
    """The Switchboard client's reads, answered by the app under test."""

    def __init__(self, client):
        self.client = client

    def get(self, path):
        r = self.client.get(path)
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text)
        return r.json()


@pytest.mark.parametrize("default_pct", ["0", "8.25"])
@pytest.mark.parametrize("taxable", [True, False], ids=["taxable", "exempt"])
@pytest.mark.parametrize("sent", [None, "0"], ids=["omitted", "explicit-0"])
@pytest.mark.parametrize(
    "route",
    ["/api/invoices", "/api/sales-receipts", "/api/estimates", "/api/credit-memos"],
)
def test_switchboard_and_the_api_land_on_the_same_rate(
    client, seed_accounts, route, sent, taxable, default_pct
):
    """What the assistant's client sends after its own fill-in and what the
    API makes of the bare request store the same rate and the same tax."""
    _default_rate(client, default_pct)
    cid = _customer(client, "Harbor Hardware", taxable)
    base, _ = ROUTES[route]
    body = {**base, "customer_id": cid, "lines": [dict(LINE)]}
    if sent is not None:
        body["tax_rate"] = float(sent)
    plan = sales_tax(_Api(client), route, body)
    via_switchboard = dict(body)
    if plan is not None and plan["rate"] is not None:
        via_switchboard["tax_rate"] = float(plan["rate"])

    direct = _create(
        client, route, cid, **{k: body[k] for k in body if k == "tax_rate"}
    )
    sent_on = _create(
        client,
        route,
        cid,
        **{k: via_switchboard[k] for k in via_switchboard if k == "tax_rate"},
    )
    assert Decimal(direct["tax_rate"]) == Decimal(sent_on["tax_rate"])
    assert Decimal(direct["tax_amount"]) == Decimal(sent_on["tax_amount"])
