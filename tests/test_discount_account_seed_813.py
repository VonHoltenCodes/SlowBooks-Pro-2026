"""A discount account from the start (#813).

The seeded chart had no account for the early-payment discounts a customer
takes (#664), so a new company's first discount stopped at "Pick the
account the discount posts to". A new company has 4750 Discounts Given; an
existing one is offered to make it from the discount picker
(payment_terms.js, tests/js/discount_account_813.test.cjs)."""

from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS


def test_the_seeded_chart_has_discounts_given():
    row = next(a for a in CHART_OF_ACCOUNTS if a["account_number"] == "4750")
    assert row["name"] == "Discounts Given" and row["account_type"] == "income"
    numbers = [a["account_number"] for a in CHART_OF_ACCOUNTS]
    assert numbers.count("4750") == 1


def test_a_discount_posts_to_it(client, seed_accounts, seed_customer):
    given = seed_accounts["4750"]
    accounts = client.get("/api/accounts").json()
    assert any(a["id"] == given.id and a["name"] == "Discounts Given" for a in accounts)
