"""Construction estimates priced by a markup on each line (#774).

A construction base or alternate line with a unit cost is priced from it: at
cost when it has no markup (the commercial bid, #551), else unit cost × (1 +
markup_pct / 100) to four places. Any other line keeps its markup unapplied.
The estimate_style setting only picks the layout. Budget, contract, SOV and
convert read cost and rate as they always have, so their own tests cover them.
"""

from decimal import Decimal

import pytest

from app.models.estimates import Estimate
from app.services.estimating import presentation_style


def _construction(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-11-10", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _post(client, customer_id, line):
    body = {"customer_id": customer_id, "date": "2026-11-10", "lines": [line]}
    return client.post("/api/estimates", json=body)


def _save_unchanged(client, est):
    """PUT an estimate's lines back as GET returned them."""
    keep = ("description", "quantity", "rate", "unit_cost", "markup_pct")
    keep += ("line_kind", "unit", "is_taxable")
    lines = [{k: ln[k] for k in keep} for ln in est["lines"]]
    r = client.put(f"/api/estimates/{est['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    return r.json()


FENCE = {"description": "Fence", "quantity": 300, "unit_cost": 16, "markup_pct": 25}


@pytest.mark.parametrize(
    "line,rate,markup",
    [
        (FENCE, "20", "25"),
        ({**FENCE, "markup_pct": None}, "16", None),
        ({**FENCE, "line_kind": "alternate"}, "20", "25"),
        # kept but unapplied: an allowance, a unit price, a line with no cost
        ({**FENCE, "rate": 30, "line_kind": "allowance"}, "30", "25"),
        ({**FENCE, "rate": 30, "line_kind": "unit_price"}, "30", "25"),
        ({"quantity": 1, "rate": 250, "markup_pct": 20}, "250", "20"),
        # stored to four places before it prices
        ({"quantity": 1, "unit_cost": "10.428571", "markup_pct": 3}, "10.7415", "3"),
        (
            {"quantity": 1, "unit_cost": 1000000, "markup_pct": "33.3333333"},
            "1333333",
            "33.3333",
        ),
    ],
)
def test_the_pricing_rule(client, seed_customer, line, rate, markup):
    _construction(client)
    (got,) = _estimate(client, seed_customer.id, [line])["lines"]
    assert Decimal(got["rate"]) == Decimal(rate)
    if markup is not None:
        assert got["markup_pct"] == markup


def test_an_unchanged_save_keeps_the_price_through_a_type_switch(client, seed_customer):
    _construction(client)
    est = _estimate(client, seed_customer.id, [{**FENCE, "markup_pct": "33.3333333"}])
    for company in ("business", "nonprofit", "construction"):
        client.put("/api/settings", json={"company_type": company})
        est = _save_unchanged(client, client.get(f"/api/estimates/{est['id']}").json())
        assert est["lines"][0]["markup_pct"] == "33.3333"
    assert est["subtotal"] == "6399.99"


def test_a_put_reprices_from_the_markup(client, seed_customer):
    _construction(client)
    est = _estimate(client, seed_customer.id, [FENCE])
    r = client.put(
        f"/api/estimates/{est['id']}", json={"lines": [{**FENCE, "markup_pct": 50}]}
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["total"]) == Decimal("7200.00")


@pytest.mark.parametrize(
    "line",
    [
        {"quantity": 1, "unit_cost": 10, "markup_pct": -101},
        {"quantity": 1, "unit_cost": 10, "markup_pct": 10001},
        {"quantity": 0, "unit_cost": "1e40"},
        {"quantity": 0, "unit_cost": "1e40", "markup_pct": 25},
        {"quantity": 0, "rate": "1e40"},
        {"quantity": 0, "unit_cost": "100000000000", "markup_pct": 10000},
    ],
)
def test_out_of_range_figures_are_refused_with_a_sentence(client, seed_customer, line):
    _construction(client)
    assert _post(client, seed_customer.id, line).status_code == 422


@pytest.mark.parametrize(
    "company,kind", [("construction", "allowance"), ("business", "base")]
)
def test_an_unapplied_markup_is_not_size_checked(client, seed_customer, company, kind):
    client.put("/api/settings", json={"company_type": company})
    line = {"quantity": 1000000, "rate": 1, "unit_cost": 100000, "markup_pct": 10000}
    assert (
        _post(client, seed_customer.id, {**line, "line_kind": kind}).status_code == 201
    )


@pytest.mark.parametrize(
    "rate,warns",
    [
        (20, True),
        (16, False),
        (0, False),
        (12, False),
        ("16.009", False),
        (None, False),
    ],
)
def test_a_line_sent_above_cost_with_no_markup_says_so(
    client, seed_customer, rate, warns
):
    _construction(client)
    line = {"quantity": 10, "unit_cost": 16}
    if rate is not None:
        line["rate"] = rate
    r = _post(client, seed_customer.id, line)
    assert r.status_code == 201, r.text
    assert bool(r.json()["warnings"]) is warns
    if warns:
        assert "markup_pct" in r.json()["warnings"][0]


def test_the_totals_carry_cost_profit_and_margin(client, seed_customer):
    _construction(client)
    permit = {"description": "Permit", "quantity": 1, "rate": 150}
    t = _estimate(client, seed_customer.id, [FENCE, permit])["totals"]
    assert (t["cost_total"], t["bid_total"]) == ("4800.00", "6150.00")
    assert (t["profit"], t["margin_pct"]) == ("1350.00", "21.95")
    assert t["uses_bid_tools"] is False


def test_the_layout_follows_the_estimate_then_the_setting(
    client, db_session, seed_customer
):
    _construction(client)  # the default is the commercial bid
    at_cost = {"quantity": 1, "unit_cost": 50}
    assert _estimate(client, seed_customer.id, [FENCE])["style"] == "line_markup"
    assert _estimate(client, seed_customer.id, [at_cost])["style"] == "commercial_bid"
    tools = _estimate(client, seed_customer.id, [FENCE], fee_pct=5)
    assert tools["style"] == "commercial_bid"
    orm = db_session.get(Estimate, tools["id"])
    assert presentation_style(orm, "line_markup") == "commercial_bid"
    _construction(client, estimate_style="line_markup")
    assert _estimate(client, seed_customer.id, [at_cost])["style"] == "line_markup"


def test_the_settings(client):
    s = client.get("/api/settings").json()
    assert (s["estimate_style"], s["estimate_default_markup_pct"]) == (
        "commercial_bid",
        "",
    )
    for key, value, ok in [
        ("estimate_style", "line_markup", True),
        ("estimate_style", "simple", False),
        ("estimate_default_markup_pct", "33.3333", True),
        ("estimate_default_markup_pct", "33.33333", False),
        ("estimate_default_markup_pct", "1000.01", False),
    ]:
        r = client.put("/api/settings", json={key: value})
        assert (r.status_code == 200) is ok, (key, value, r.text)


def _preview(client, est_id):
    r = client.get(f"/api/estimates/{est_id}/print-preview")
    assert r.status_code == 200, r.text
    return r.text


def test_the_owner_sees_description_quantity_and_amount(client, seed_customer):
    _construction(client)
    code = client.post("/api/cost-codes", json={"code": "32 31 00", "name": "Fences"})
    line = {**FENCE, "unit": "LF", "cost_code_id": code.json()["id"]}
    html = _preview(client, _estimate(client, seed_customer.id, [line])["id"])
    assert '<table class="line-items priced-lines">' in html
    assert "300.00 LF" in html and "$6,000.00" in html
    for hidden in ("32 31 00", "$20.00", "16.0000", "Markup", "Profit", "Tax ("):
        assert hidden not in html, hidden
    # a commercial bid's print preview is main's, byte for byte round the rule
    est = _estimate(client, seed_customer.id, [{"quantity": 1, "unit_cost": 16}])
    html = _preview(client, est["id"])
    assert "priced-lines" not in html and "#f7f9fb; }\n    .totals {" in html
