"""Payment terms, a short month's clamp (#664, review of #665).

A date-driven term whose discount day comes before its due day ("2% if paid
by the 28th, net due the 30th") clamped both days to Feb 28 in February, so
the discount day was not before the due day and February documents got no
discount. The order is the configured days' order, decided before the
clamp. A discount day equal to the due day could never be taken, so it is
refused when the term is saved. Same house style as
tests/test_payment_terms_pass5_terms.py.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.payment_terms import DATE_DRIVEN, Rule

D = Decimal


def _rule(due_day, discount_day):
    return Rule(
        name="t",
        kind=DATE_DRIVEN,
        due_day=due_day,
        discount_day=discount_day,
        discount_percent=D("2"),
    )


@pytest.mark.parametrize(
    "due_day, discount_day, on, expect",
    [
        # by the 28th, net the 30th: February's due day is the 28th, and the
        # discount runs to that same day
        (30, 28, date(2027, 2, 5), date(2027, 2, 28)),
        (30, 28, date(2028, 2, 5), date(2028, 2, 28)),  # leap: due Feb 29
        # by the 29th, net the 31st
        (31, 29, date(2027, 2, 5), date(2027, 2, 28)),
        (31, 29, date(2028, 2, 5), date(2028, 2, 29)),  # leap: both the 29th
        (31, 29, date(2027, 4, 5), date(2027, 4, 29)),  # April: due the 30th
        # a full month is unchanged
        (30, 28, date(2027, 3, 5), date(2027, 3, 28)),
        # dated after the discount day: still none
        (31, 20, date(2027, 2, 21), None),
    ],
)
def test_a_discount_before_the_due_day_survives_a_short_month(
    due_day, discount_day, on, expect
):
    assert _rule(due_day, discount_day).discount_date(on) == expect


def test_the_february_discount_is_on_the_dates_route(client):
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "2% 28th Net 30th",
            "kind": "date_driven",
            "due_day": 30,
            "discount_percent": "2",
            "discount_day": 28,
        },
    )
    assert r.status_code == 201, r.text
    d = client.get(
        "/api/payment-terms/dates",
        params={"date": "2027-02-05", "terms": "2% 28th Net 30th"},
    ).json()
    assert d["due_date"] == "2027-02-28"
    assert d["discount_date"] == "2027-02-28"


EQUAL = (
    "The day of the month for the discount must be a different day from the "
    "day it's due; a discount on the due day itself is never early."
)


def test_a_discount_day_equal_to_the_due_day_is_refused_on_create(client):
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "2% 15th Net 15th",
            "kind": "date_driven",
            "due_day": 15,
            "discount_percent": "2",
            "discount_day": 15,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == EQUAL


def test_a_discount_day_equal_to_the_due_day_is_refused_on_update(client):
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "2% 10th Net 15th",
            "kind": "date_driven",
            "due_day": 15,
            "discount_percent": "2",
            "discount_day": 10,
        },
    )
    assert r.status_code == 201, r.text
    r = client.put(f"/api/payment-terms/{r.json()['id']}", json={"discount_day": 15})
    assert r.status_code == 422
    assert r.json()["detail"] == EQUAL
