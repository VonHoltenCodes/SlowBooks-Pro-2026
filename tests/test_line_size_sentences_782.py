"""A line's quantity, rate and unit cost are each held to their own column,
whatever the other figure is (#782): "1e40" at a rate of 0 comes to 0, so the
line check passed it, and the quantity rounding raised (a 500). Each is a 422
with a sentence now, on every document that takes lines."""

import pytest

from tests.test_bill_edit import _vendor

QUANTITY_SENTENCE = "A quantity can be at most 99,999,999.9999. Check the line."
RATE_SENTENCE = "A rate can be at most 9,999,999,999,999.9999. Check the line."
UNIT_COST_SENTENCE = "A unit cost can be at most 99,999,999.9999. Check the line."
NOT_A_NUMBER = "must be a number"

DOCUMENTS = (
    "estimate",
    "invoice",
    "credit memo",
    "bill",
    "purchase order",
    "vendor credit",
    "recurring invoice",
    "memorized bill",
)
# Lines whose figures are floats parse "1e400" as infinity, which is refused
# as not a number before any line check runs; the Decimal ones reach the check.
FLOAT_LINES = {
    "credit memo",
    "bill",
    "purchase order",
    "vendor credit",
    "recurring invoice",
    "memorized bill",
}


def _message(r) -> str:
    detail = r.json().get("detail")
    if isinstance(detail, list):
        return " ".join(e.get("message", "") or e.get("msg", "") for e in detail)
    return detail if isinstance(detail, str) else str(detail)


@pytest.fixture
def post_line(client, seed_accounts, seed_customer):
    """Post one document of a kind with one line; returns the response."""
    vendor_id = _vendor(client)["id"]
    account_id = seed_accounts["6000"].id
    customer_id = seed_customer.id

    def post(kind, **line):
        line = {"description": "x", **line}
        if kind in ("estimate", "invoice", "credit memo"):
            path = {
                "estimate": "/api/estimates",
                "invoice": "/api/invoices",
                "credit memo": "/api/credit-memos",
            }[kind]
            body = {"customer_id": customer_id, "date": "2026-11-11", "lines": [line]}
        elif kind in ("bill", "vendor credit", "purchase order"):
            path = {
                "bill": "/api/bills",
                "vendor credit": "/api/vendor-credits",
                "purchase order": "/api/purchase-orders",
            }[kind]
            if kind != "purchase order":
                line["account_id"] = account_id
            body = {"vendor_id": vendor_id, "date": "2026-11-11", "lines": [line]}
        elif kind == "recurring invoice":
            path = "/api/recurring"
            body = {
                "customer_id": customer_id,
                "frequency": "monthly",
                "start_date": "2026-11-11",
                "lines": [line],
            }
        else:
            path = "/api/memorized"
            line["account_id"] = account_id
            body = {
                "kind": "bill",
                "name": "Supplies",
                "mode": "auto",
                "frequency": "monthly",
                "start_date": "2026-11-30",
                "vendor_id": vendor_id,
                "lines": [line],
            }
        return client.post(path, json=body)

    return post


@pytest.mark.parametrize("kind", DOCUMENTS)
@pytest.mark.parametrize("quantity", ["1e40", 1e40, "100000000"])
def test_a_quantity_past_its_column_at_a_rate_of_0_is_refused_with_a_sentence(
    post_line, kind, quantity
):
    r = post_line(kind, quantity=quantity, rate=0)
    assert r.status_code == 422, r.text
    assert QUANTITY_SENTENCE in _message(r)


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_a_quantity_of_1e400_is_refused_with_a_sentence_not_a_500(post_line, kind):
    r = post_line(kind, quantity="1e400", rate=0)
    assert r.status_code == 422, r.text
    expected = NOT_A_NUMBER if kind in FLOAT_LINES else QUANTITY_SENTENCE
    assert expected in _message(r)


@pytest.mark.parametrize("kind", DOCUMENTS)
@pytest.mark.parametrize("rate", ["1e40", "10000000000000"])
def test_a_rate_past_its_column_at_a_quantity_of_0_is_refused_with_a_sentence(
    post_line, kind, rate
):
    r = post_line(kind, quantity=0, rate=rate)
    assert r.status_code == 422, r.text
    assert RATE_SENTENCE in _message(r)


@pytest.mark.parametrize("kind", DOCUMENTS)
@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
def test_a_quantity_or_rate_that_is_not_a_number_is_refused_with_a_sentence(
    post_line, kind, value
):
    for line in ({"quantity": value, "rate": 0}, {"quantity": 0, "rate": value}):
        r = post_line(kind, **line)
        assert r.status_code == 422, r.text
        assert NOT_A_NUMBER in _message(r), r.text


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_the_largest_quantity_a_line_can_hold_still_saves(post_line, kind):
    r = post_line(kind, quantity="99999999.9999", rate=1)
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_a_large_rate_that_fits_its_column_still_saves(post_line, kind):
    r = post_line(kind, quantity=1, rate="9999999999999.99")
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("kind", ["estimate", "invoice"])
def test_the_largest_rate_a_line_can_hold_still_saves(post_line, kind):
    # Decimal lines keep all four places; a float line cannot hold this figure
    r = post_line(kind, quantity="0.5", rate="9999999999999.9999")
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_a_line_of_1e9_by_1e9_is_still_refused_as_more_than_a_line_can_be(
    post_line, kind
):
    r = post_line(kind, quantity="1000000000", rate="1000000000")
    assert r.status_code == 422, r.text
    assert "more than a line can be (9,999,999,999,999.99)" in _message(r)


def test_an_estimate_unit_cost_past_its_column_is_refused_whatever_the_quantity(
    post_line,
):
    r = post_line("estimate", quantity=0, rate=0, unit_cost="1e40")
    assert r.status_code == 422, r.text
    assert UNIT_COST_SENTENCE in _message(r)
    r = post_line("estimate", quantity=1, rate=1, unit_cost="100000000")
    assert r.status_code == 422, r.text
    assert UNIT_COST_SENTENCE in _message(r)
    r = post_line("estimate", quantity=1, rate=1, unit_cost="99999999.9999")
    assert r.status_code == 201, r.text


def test_a_deduct_alternate_is_held_to_the_same_quantity_column(post_line):
    r = post_line("estimate", quantity="-1e40", rate=0, line_kind="alternate")
    assert r.status_code == 422, r.text
    assert QUANTITY_SENTENCE in _message(r)
