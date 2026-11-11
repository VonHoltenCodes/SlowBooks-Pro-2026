"""Construction estimates priced by a markup on each line (#774).

A construction line that carries a unit cost is priced from it: at cost when
it has no markup of its own (the commercial bid, #551), else unit cost × (1 +
markup_pct / 100) to the rate's four places. The rule reads the line and
nothing else; the estimate_style setting only picks the layout. Downstream
is unchanged: the budget comes from cost, the contract and the schedule of
values from price.
"""

from decimal import Decimal

import pytest

from app.models.estimates import Estimate
from app.services.contracts import sov_from_estimate
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


def _job(client, customer_id, name="Fence job"):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _by_desc(est):
    return {line["description"]: line for line in est["lines"]}


def _fence(client, customer_id, **extra):
    """Two marked-up lines: 300 LF of fence at 16 + 25%, and 2 gates at 150 +
    40%. Price 6,000 + 420 = 6,420; cost 4,800 + 300 = 5,100."""
    return _estimate(
        client,
        customer_id,
        [
            {
                "description": "Fence",
                "quantity": 300,
                "unit": "LF",
                "unit_cost": 16,
                "markup_pct": 25,
            },
            {"description": "Gate", "quantity": 2, "unit_cost": 150, "markup_pct": 40},
        ],
        **extra,
    )


# ── The pricing rule ───────────────────────────────────────────────────────


def test_a_line_with_a_markup_is_priced_cost_plus_markup(client, seed_customer):
    _construction(client)
    lines = _by_desc(_fence(client, seed_customer.id))
    assert Decimal(lines["Fence"]["rate"]) == Decimal("20")
    assert Decimal(lines["Fence"]["amount"]) == Decimal("6000.00")
    assert Decimal(lines["Fence"]["markup_pct"]) == Decimal("25")
    assert Decimal(lines["Gate"]["rate"]) == Decimal("210")


def test_a_line_without_a_markup_is_priced_at_cost(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Slab", "quantity": 2, "rate": 999, "unit_cost": 50}],
    )
    line = est["lines"][0]
    assert Decimal(line["rate"]) == Decimal("50")
    assert line["markup_pct"] is None


def test_the_rate_is_rounded_to_four_places_and_the_markup_reads_back(
    client, seed_customer
):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Post cap",
                "quantity": 100,
                "unit_cost": "0.45",
                "markup_pct": "33.3",
            }
        ],
    )
    line = est["lines"][0]
    # 0.45 × 1.333 = 0.59985, half-up to four places
    assert Decimal(line["rate"]) == Decimal("0.5999")
    assert Decimal(line["amount"]) == Decimal("59.99")
    assert line["markup_pct"] == "33.3"
    again = client.get(f"/api/estimates/{est['id']}").json()["lines"][0]
    assert again["markup_pct"] == "33.3"


def test_an_alternate_is_marked_up_too(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Upgrade",
                "quantity": 1,
                "unit_cost": 100,
                "markup_pct": 10,
                "line_kind": "alternate",
            }
        ],
    )
    assert Decimal(est["lines"][0]["rate"]) == Decimal("110")


def test_a_line_with_nothing_to_mark_up_keeps_it_unapplied(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            # no unit cost: the typed price stands
            {"description": "Haul off", "quantity": 1, "rate": 250, "markup_pct": 20},
            {
                "description": "Fixtures",
                "quantity": 1,
                "rate": 5000,
                "unit_cost": 4000,
                "markup_pct": 20,
                "line_kind": "allowance",
            },
            {
                "description": "Extra post",
                "quantity": 1,
                "rate": 40,
                "unit_cost": 30,
                "markup_pct": 20,
                "line_kind": "unit_price",
            },
        ],
    )
    lines = _by_desc(est)
    assert Decimal(lines["Haul off"]["rate"]) == Decimal("250")
    assert Decimal(lines["Fixtures"]["rate"]) == Decimal("5000")
    assert Decimal(lines["Extra post"]["rate"]) == Decimal("40")
    # kept, so a line whose kind goes and comes back is priced by it again
    assert all(line["markup_pct"] == "20" for line in est["lines"])


def test_the_style_setting_never_changes_the_math(client, seed_customer):
    _construction(client, estimate_style="commercial_bid")
    lines = _by_desc(_fence(client, seed_customer.id))
    assert Decimal(lines["Fence"]["rate"]) == Decimal("20")
    _construction(client, estimate_style="line_markup")
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Slab", "quantity": 1, "unit_cost": 50}],
    )
    assert Decimal(est["lines"][0]["rate"]) == Decimal("50")


def test_a_put_reprices_from_the_markup(client, seed_customer):
    _construction(client)
    est = _fence(client, seed_customer.id)
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={
            "lines": [
                {
                    "description": "Fence",
                    "quantity": 300,
                    "rate": 1,  # ignored: the cost and markup price the line
                    "unit_cost": 16,
                    "markup_pct": 50,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    line = body["lines"][0]
    assert Decimal(line["rate"]) == Decimal("24")
    assert Decimal(line["markup_pct"]) == Decimal("50")
    assert Decimal(body["subtotal"]) == Decimal("7200.00")
    assert Decimal(body["total"]) == Decimal("7200.00")


def test_a_business_company_keeps_the_markup_but_charges_its_rate(
    client, seed_customer
):
    r = client.put("/api/settings", json={"company_type": "business"})
    assert r.status_code == 200, r.text
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Service",
                "quantity": 2,
                "rate": 75,
                "unit_cost": 40,
                "markup_pct": 25,
            }
        ],
    )
    line = est["lines"][0]
    assert Decimal(line["rate"]) == Decimal("75")
    assert line["markup_pct"] == "25"
    assert est["style"] is None


def _save_unchanged(client, est):
    """PUT an estimate's lines back as GET returned them."""
    keep = ("description", "quantity", "rate", "unit_cost", "markup_pct")
    keep += ("line_kind", "unit", "is_taxable")
    lines = [{k: ln[k] for k in keep} for ln in est["lines"]]
    r = client.put(f"/api/estimates/{est['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("other", ["business", "nonprofit"])
def test_a_switch_of_company_type_and_back_keeps_the_price(
    client, seed_customer, other
):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Flip", "quantity": 1, "unit_cost": 100, "markup_pct": 25}],
    )
    r = client.put("/api/settings", json={"company_type": other})
    assert r.status_code == 200, r.text
    est = _save_unchanged(client, client.get(f"/api/estimates/{est['id']}").json())
    assert est["lines"][0]["markup_pct"] == "25"
    _construction(client)
    est = _save_unchanged(client, client.get(f"/api/estimates/{est['id']}").json())
    assert Decimal(est["total"]) == Decimal("125.00")


def test_a_markup_finer_than_four_places_prices_as_it_is_stored(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Precise",
                "quantity": 1,
                "unit_cost": 1000000,
                "markup_pct": "33.3333333",
            }
        ],
    )
    assert est["lines"][0]["markup_pct"] == "33.3333"
    again = _save_unchanged(client, est)
    assert again["subtotal"] == est["subtotal"] == "1333333.00"


@pytest.mark.parametrize(
    "line",
    [
        {"unit_cost": "1e40"},
        {"unit_cost": "1e40", "markup_pct": 25},
        {"rate": "1e40"},
        # the cost fits, the rate its markup gives does not
        {"unit_cost": "100000000000", "markup_pct": 10000},
    ],
)
def test_a_figure_too_large_to_store_is_a_sentence_at_quantity_zero(
    client, seed_customer, line
):
    _construction(client)
    line = {"quantity": 0, **line}
    r = client.post(
        "/api/estimates",
        json={"customer_id": seed_customer.id, "date": "2026-11-10", "lines": [line]},
    )
    assert r.status_code == 422, r.text
    assert "more than a line can be" in r.text


def test_a_markup_out_of_bounds_is_refused(client, seed_customer):
    _construction(client)
    for pct in (-101, 10001):
        r = client.post(
            "/api/estimates",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-11-10",
                "lines": [{"quantity": 1, "unit_cost": 10, "markup_pct": pct}],
            },
        )
        assert r.status_code == 422, r.text


# ── The totals ─────────────────────────────────────────────────────────────


def test_the_totals_carry_cost_profit_and_margin(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Fence",
                "quantity": 300,
                "unit_cost": 16,
                "markup_pct": 25,
            },
            # a typed price with no cost adds price and no cost
            {"description": "Permit", "quantity": 1, "rate": 150},
        ],
    )
    t = est["totals"]
    assert Decimal(t["cost_total"]) == Decimal("4800.00")
    assert Decimal(t["bid_total"]) == Decimal("6150.00")
    assert Decimal(t["profit"]) == Decimal("1350.00")
    # 1,350 ÷ 6,150 = 21.95%
    assert Decimal(t["margin_pct"]) == Decimal("21.95")
    assert t["uses_bid_tools"] is False


def test_a_zero_bid_has_a_zero_margin(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Nothing", "quantity": 1, "rate": 0}],
    )
    assert Decimal(est["totals"]["margin_pct"]) == 0
    assert Decimal(est["totals"]["profit"]) == 0


def test_an_allowance_costs_its_amount_and_uses_a_bid_tool(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Slab", "quantity": 1, "unit_cost": 1000},
            {
                "description": "Finishes",
                "quantity": 1,
                "rate": 500,
                "line_kind": "allowance",
            },
        ],
        gc_pct=10,
    )
    t = est["totals"]
    assert Decimal(t["cost_total"]) == Decimal("1500.00")
    # bid 1,600 (1,500 + 10% gc on the slab); budget 1,600 (gc is a cost)
    assert Decimal(t["profit"]) == Decimal("0.00")
    assert t["uses_bid_tools"] is True


# ── Which layout an estimate gets ──────────────────────────────────────────


def _est(db_session, est_id):
    return db_session.get(Estimate, est_id)


def test_bid_tools_mean_the_commercial_bid(client, db_session, seed_customer):
    _construction(client)
    marked_up = {"quantity": 1, "unit_cost": 10, "markup_pct": 20}
    cases = [
        _estimate(client, seed_customer.id, [marked_up], fee_pct=5),
        _estimate(
            client,
            seed_customer.id,
            [{**marked_up, "line_kind": "alternate"}],
        ),
        _estimate(
            client,
            seed_customer.id,
            [
                {
                    **marked_up,
                    "trade_package_id": client.post(
                        "/api/trade-packages",
                        json={"name": "Fencing", "section_ids": []},
                    ).json()["id"],
                }
            ],
        ),
    ]
    for est in cases:
        assert est["style"] == "commercial_bid"
        assert (
            presentation_style(_est(db_session, est["id"]), "line_markup")
            == "commercial_bid"
        )


def test_a_line_markup_means_markup_per_line(client, db_session, seed_customer):
    _construction(client, estimate_style="commercial_bid")
    est = _fence(client, seed_customer.id)
    assert est["style"] == "line_markup"
    assert (
        presentation_style(_est(db_session, est["id"]), "commercial_bid")
        == "line_markup"
    )


def test_otherwise_the_company_style_decides(client, db_session, seed_customer):
    _construction(client, estimate_style="commercial_bid")
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Slab", "quantity": 1, "unit_cost": 50}],
    )
    assert est["style"] == "commercial_bid"
    orm = _est(db_session, est["id"])
    assert presentation_style(orm, "commercial_bid") == "commercial_bid"
    assert presentation_style(orm, "line_markup") == "line_markup"
    _construction(client, estimate_style="line_markup")
    assert client.get(f"/api/estimates/{est['id']}").json()["style"] == "line_markup"


# ── Downstream unchanged ───────────────────────────────────────────────────


def test_accepting_budgets_cost_and_the_contract_is_the_price(client, seed_customer):
    _construction(client)
    job = _job(client, seed_customer.id)
    est = _fence(client, seed_customer.id, job_id=job["id"])
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    rows = client.get(f"/api/jobs/{job['id']}/budgets").json()
    assert sum(Decimal(str(row["amount"])) for row in rows) == Decimal("5100.00")
    assert sum(Decimal(str(row["revenue_amount"])) for row in rows) == Decimal(
        "6420.00"
    )
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    contract = r.json()["contract"]
    assert Decimal(str(contract["contract_amount"])) == Decimal("6420.00")
    assert contract["sov_balance"] == 0.0


def test_the_schedule_of_values_is_at_price_with_no_markup_lines(
    client, db_session, seed_customer
):
    _construction(client)
    est = _fence(client, seed_customer.id)
    sov = sov_from_estimate(_est(db_session, est["id"]))
    assert {line["kind"] for line in sov} == {"work"}
    assert {line["description"]: line["scheduled_value"] for line in sov} == {
        "Fence": Decimal("6000.00"),
        "Gate": Decimal("420.00"),
    }


def test_convert_carries_the_marked_up_rate(client, seed_accounts, seed_customer):
    _construction(client)
    est = _fence(client, seed_customer.id)
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code == 200, r.text
    inv = r.json()
    lines = {line["description"]: line for line in inv["lines"]}
    assert set(lines) == {"Fence", "Gate"}  # no markup lines
    assert Decimal(lines["Fence"]["rate"]) == Decimal("20")
    assert Decimal(lines["Gate"]["rate"]) == Decimal("210")
    assert Decimal(inv["total"]) == Decimal("6420.00")


# ── Settings ───────────────────────────────────────────────────────────────


def test_the_style_defaults_to_markup_per_line(client):
    s = client.get("/api/settings").json()
    assert s["estimate_style"] == "line_markup"
    assert s["estimate_default_markup_pct"] == ""


def test_settings_take_either_style_and_refuse_another(client):
    for style in ("commercial_bid", "line_markup"):
        r = client.put("/api/settings", json={"estimate_style": style})
        assert r.status_code == 200, r.text
        assert client.get("/api/settings").json()["estimate_style"] == style
    r = client.put("/api/settings", json={"estimate_style": "simple"})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["estimate_style"] == "line_markup"


def test_the_default_markup_is_a_percent_from_0_to_1000(client):
    r = client.put("/api/settings", json={"estimate_default_markup_pct": "25"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["estimate_default_markup_pct"] == "25"
    r = client.put("/api/settings", json={"estimate_default_markup_pct": "1000"})
    assert r.status_code == 200, r.text
    for bad in ("1000.01", "-1", "lots"):
        r = client.put("/api/settings", json={"estimate_default_markup_pct": bad})
        assert r.status_code == 422, bad
        assert "Default markup must be a number from 0 to 1000" in r.json()["detail"]
    r = client.put("/api/settings", json={"estimate_default_markup_pct": ""})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["estimate_default_markup_pct"] == ""


# ── The owner's PDF (print preview: the same template, no WeasyPrint) ────────


def _preview(client, est_id):
    r = client.get(f"/api/estimates/{est_id}/print-preview")
    assert r.status_code == 200, r.text
    return r.text


def test_the_owner_sees_description_quantity_and_amount(client, seed_customer):
    _construction(client, estimate_terms="Gates swing in.", company_license="CGC1")
    code = client.post(
        "/api/cost-codes", json={"code": "32 31 00", "name": "Fences and Gates"}
    ).json()
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Fence",
                "quantity": 300,
                "unit": "LF",
                "unit_cost": 16,
                "markup_pct": 25,
                "cost_code_id": code["id"],
            }
        ],
        tax_rate=0.07,
    )
    html = _preview(client, est["id"])
    head = html[html.index('<table class="line-items priced-lines">') :]
    head = head[: head.index("</thead>")]
    assert ">Description<" in head and ">Qty<" in head and ">Amount<" in head
    assert ">Rate<" not in head
    assert "300.00 LF" in html
    assert "$6,000.00" in html
    assert "32 31 00" not in html  # no cost code before the description
    assert "$20.00" not in html and "$16.00" not in html  # no rate, no cost
    assert "25%" not in html and "Markup" not in html
    # nor the raw figures, however a template might print them
    for raw in ("16.0000", "16.00 ", "20.0000", ">25<", ">16<", "Profit", "Margin"):
        assert raw not in html, raw
    assert "Subtotal" in html and "Tax (7.00%)" in html and "$420.00" in html
    assert "$6,420.00" in html
    assert "Bid total" not in html
    assert "Gates swing in." in html and "Owner signature" in html


def test_the_commercial_bid_keeps_its_own_pdf(client, seed_customer):
    _construction(client, estimate_style="commercial_bid")
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Slab", "quantity": 1, "unit_cost": 400}],
    )
    html = _preview(client, est["id"])
    assert ">Rate<" in html and "Bid total" in html
    # under markup per line, the same estimate prints as a priced list
    _construction(client, estimate_style="line_markup")
    html = _preview(client, est["id"])
    assert ">Rate<" not in html and "Bid total" not in html


def test_an_owner_estimate_with_no_tax_prints_no_tax_row(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Fence", "quantity": 1, "unit_cost": 16, "markup_pct": 25}],
    )
    html = _preview(client, est["id"])
    assert "Tax (" not in html and "Total" in html


def test_a_markup_per_line_row_prints_whole_on_one_page(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Fence", "quantity": 1, "unit_cost": 16, "markup_pct": 25}],
    )
    html = _preview(client, est["id"])
    assert "table.line-items.priced-lines tr { break-inside: avoid;" in html
    assert '<table class="line-items priced-lines">' in html
    # a commercial bid's PDF is as it was, the rule included
    _construction(client, estimate_style="commercial_bid")
    est = _estimate(
        client, seed_customer.id, [{"description": "At cost", "unit_cost": 16}]
    )
    html = _preview(client, est["id"])
    assert "priced-lines" not in html
    # not even a blank line where the rule would be
    assert "#f7f9fb; }\n    .totals {" in html


def test_a_marked_up_rate_too_large_for_a_line_is_refused(client, seed_customer):
    _construction(client)
    line = {"quantity": 1, "unit_cost": "1000000000000", "markup_pct": 10000}
    r = client.post(
        "/api/estimates",
        json={"customer_id": seed_customer.id, "date": "2026-11-10", "lines": [line]},
    )
    assert r.status_code == 422, r.text
    assert "more than a line can be" in r.text


def test_a_cost_finer_than_four_places_prices_as_it_is_stored(client, seed_customer):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "a",
                "quantity": 3,
                "unit_cost": "10.428571",
                "markup_pct": 3,
            },
            {
                "description": "b",
                "quantity": 6,
                "unit_cost": "10.857142",
                "markup_pct": 6,
            },
        ],
    )
    lines = _by_desc(est)
    assert lines["a"]["unit_cost"].startswith("10.4286")
    # the rate is the stored cost marked up: 10.4286 x 1.03 = 10.741458
    assert Decimal(lines["a"]["rate"]) == Decimal("10.7415")
    assert _save_unchanged(client, est)["total"] == est["total"]


def test_a_line_sent_above_cost_with_no_markup_says_it_is_at_cost(
    client, seed_customer
):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Fence", "quantity": 10, "unit_cost": 16, "markup_pct": 25}],
    )
    old_client_line = {"description": "Fence", "quantity": 10, "unit_cost": 16}
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={"lines": [{**old_client_line, "rate": 20}]},
    )
    assert r.status_code == 200, r.text
    (warning,) = r.json()["warnings"]
    # a markup sent as null is no markup: the same sentence
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={"lines": [{**old_client_line, "rate": 20, "markup_pct": None}]},
    )
    assert len(r.json()["warnings"]) == 1, r.text
    assert warning.startswith("Line 1 came with no markup, so it is priced at its")
    assert "markup_pct" in warning
    # sent with its markup, or at its cost, there is nothing to say
    quiet = (
        {**old_client_line, "rate": 16},
        {**old_client_line, "markup_pct": 25},
        # a commercial client sends 0, or less than cost, for an at-cost line
        {**old_client_line, "rate": 0},
        {**old_client_line, "rate": 12},
        # no rate at all, the natural commercial call
        old_client_line,
        # above cost by less than a cent
        {**old_client_line, "rate": "16.00004"},
        {**old_client_line, "rate": "16.005"},
        {**old_client_line, "rate": "16.009"},
    )
    for line in quiet:
        r = client.put(f"/api/estimates/{est['id']}", json={"lines": [line]})
        assert r.status_code == 200 and r.json()["warnings"] == [], r.text


@pytest.mark.parametrize(
    "company,kind",
    [
        ("construction", "allowance"),
        ("construction", "unit_price"),
        ("business", "base"),
    ],
)
def test_a_kept_markup_that_does_not_price_a_line_is_not_size_checked(
    client, seed_customer, company, kind
):
    r = client.put("/api/settings", json={"company_type": company})
    assert r.status_code == 200, r.text
    line = {"description": "Big", "quantity": 1000000, "rate": 1, "unit_cost": 100000}
    line.update(markup_pct=10000, line_kind=kind)
    est = _estimate(client, seed_customer.id, [line])
    assert est["lines"][0]["markup_pct"] == "10000"
    assert Decimal(est["lines"][0]["rate"]) == Decimal("1")
