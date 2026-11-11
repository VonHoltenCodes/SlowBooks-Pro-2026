"""The construction estimate form priced by a markup on each line (#774), in
a real browser against the real routes.

Markup per line shows no Package, Kind or Markups block; Price is cost ×
(1 + markup) and the save carries the markup. A new row starts at the
company's default markup. An estimate that uses bid tools opens in the
commercial layout whatever the setting says, whose totals panel hides its
zero rows. A costed estimate priced at cost says so in either layout."""

from decimal import Decimal
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def _construction(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text


_BROWSER = {}


@pytest.fixture(scope="module", autouse=True)
def _chromium():
    """One Chromium for the module's tests; each test opens its own page."""
    with sync_api.sync_playwright() as p:
        try:
            _BROWSER["browser"] = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        try:
            yield
        finally:
            _BROWSER.pop("browser").close()


def _estimate_form(client, test, est_id=None):
    """Open the estimate form (a new one, or `est_id`) and run test(page)."""
    page = _BROWSER["browser"].new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())

    def serve(route):
        url = urlsplit(route.request.url)
        if url.hostname != "testserver":
            return route.abort()
        response = client.request(
            route.request.method,
            url.path + ("?" + url.query if url.query else ""),
            content=route.request.post_data_buffer,
            headers={
                "Content-Type": route.request.headers.get(
                    "content-type", "application/json"
                )
            },
        )
        route.fulfill(
            status=response.status_code,
            body=response.content,
            content_type=response.headers.get(
                "content-type", "application/octet-stream"
            ),
        )

    page.route("**/*", serve)
    try:
        page.goto("http://testserver/#/estimates")
        page.locator("#splash-dismiss").click()
        page.evaluate("async (id) => { await EstimatesPage.showForm(id); }", est_id)
        page.locator("#est-form").wait_for()
        test(page)
        assert errors == []
    finally:
        page.close()


def _first_row(page):
    return page.locator("#est-lines tr").first


def test_markup_per_line_prices_each_line_and_saves_its_markup(
    client, seed_accounts, seed_customer
):
    _construction(client)
    # the Cost Code column shows once the company has a code
    r = client.post(
        "/api/cost-codes", json={"code": "32 31 00", "name": "Fences and Gates"}
    )
    assert r.status_code == 201, r.text

    def test(page):
        assert page.locator(".line-package").count() == 0
        assert page.locator(".line-kind").count() == 0
        assert page.locator('#est-form [name="gc_pct"]').count() == 0
        assert page.locator('#est-form [name="markup_allowances"]').count() == 0
        assert page.locator('#est-form [name="show_markup_rows"]').count() == 0
        assert page.get_by_role("heading", name="Markups").count() == 0
        heads = [
            " ".join(t.split())
            for t in page.locator(".line-items-table thead th").all_text_contents()
        ]
        assert heads[:11] == [
            "Item",
            "Description",
            "Cost Code",
            "Cost Type",
            "Qty",
            "Unit",
            "Cost",
            "Markup %",
            "Price",
            "Tax",
            "Amount",
        ]
        page.locator("#est-customer-select").select_option(str(seed_customer.id))
        row = _first_row(page)
        row.locator(".line-desc").fill("Fence")
        row.locator(".line-unit-cost").fill("16")
        row.locator(".line-markup").fill("25")
        row.locator(".line-qty").fill("300")
        assert row.locator(".line-rate").input_value() == "20"
        assert row.locator(".line-rate").evaluate("el => el.readOnly")
        assert row.locator(".line-amount").inner_text() == "$6,000.00"
        # no cost: the markup is off and the price is typed
        row.locator(".line-unit-cost").fill("")
        assert row.locator(".line-markup").is_disabled()
        assert not row.locator(".line-rate").evaluate("el => el.readOnly")
        row.locator(".line-unit-cost").fill("16")
        assert row.locator(".line-rate").input_value() == "20"
        page.get_by_role("button", name="Create Estimate").click()
        page.locator("#est-form").wait_for(state="hidden")

    _estimate_form(client, test)
    (est,) = client.get("/api/estimates").json()
    line = client.get(f"/api/estimates/{est['id']}").json()["lines"][0]
    assert line["markup_pct"] == "25"
    assert Decimal(line["rate"]) == Decimal("20")
    assert line["line_kind"] == "base" and line["trade_package_id"] is None


def test_a_new_row_starts_at_the_default_markup(client, seed_customer):
    _construction(client, estimate_default_markup_pct="30")

    def test(page):
        assert _first_row(page).locator(".line-markup").input_value() == "30"
        page.get_by_role("button", name="+ Add Line").click()
        assert page.locator("#est-lines tr .line-markup").nth(1).input_value() == "30"

    _estimate_form(client, test)


def test_an_estimate_with_a_trade_package_opens_as_a_commercial_bid(
    client, seed_customer
):
    _construction(client, estimate_style="line_markup")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Fencing", "section_ids": []}
    ).json()
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-10",
            "lines": [
                {
                    "description": "Fence",
                    "quantity": 1,
                    "unit_cost": 100,
                    "trade_package_id": pkg["id"],
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    def test(page):
        assert page.locator(".line-package").count() == 1
        assert page.locator(".line-kind").count() == 1
        assert page.locator('#est-form [name="gc_pct"]').count() == 1
        assert page.get_by_role("heading", name="Markups").count() == 1

    _estimate_form(client, test, r.json()["id"])


def test_the_commercial_totals_panel_hides_its_zero_rows(client, seed_customer):
    _construction(client, estimate_style="commercial_bid")
    zero_rows = (
        "est-bid-direct",
        "est-bid-allow",
        "est-bid-gc",
        "est-bid-ins",
        "est-bid-fee",
        "est-bid-bond",
        "est-bid-tax",
        "est-bid-markup-allow",
        "est-bid-cost",
        "est-bid-total",
    )

    def test(page):
        row = _first_row(page)
        row.locator(".line-unit-cost").fill("100")
        for row_id in zero_rows:
            assert not page.locator(f"#{row_id}").is_visible(), row_id
        assert page.locator("#est-bid-subtotal").is_visible()
        assert page.locator("#est-bid-grand").is_visible()
        page.locator('#est-form [name="gc_pct"]').fill("10")
        assert page.locator("#est-bid-gc").is_visible()
        assert page.locator("#est-bid-gc").inner_text() == "$10.00"
        assert page.locator("#est-bid-total").is_visible()  # a markup row shows
        assert not page.locator("#est-bid-fee").is_visible()
        # an allowance brings its own row and the allowance-markup flag
        page.get_by_role("button", name="+ Add Line").click()
        second = page.locator("#est-lines tr").nth(1)
        second.locator(".line-kind").select_option("allowance")
        second.locator(".line-rate").fill("50")
        assert page.locator("#est-bid-allow").is_visible()
        assert page.locator("#est-bid-markup-allow").is_visible()
        # the cost of work (the markup base) now differs from the subtotal
        assert page.locator("#est-bid-cost").is_visible()

    _estimate_form(client, test)


@pytest.mark.parametrize("style", ["line_markup", "commercial_bid"])
def test_a_bid_at_cost_says_so_until_a_markup_is_typed(client, seed_customer, style):
    _construction(client, estimate_style=style)
    warning = "Priced at cost: there is no markup on this estimate."

    def test(page):
        note = page.locator("#est-at-cost")
        assert not note.is_visible()  # nothing costed yet
        row = _first_row(page)
        row.locator(".line-unit-cost").fill("16")
        if style == "line_markup":
            row.locator(".line-markup").fill("0")
        assert note.is_visible() and note.inner_text() == warning
        if style == "line_markup":
            row.locator(".line-markup").fill("25")
            profit = page.locator("#est-bid-profit").inner_text()
            assert profit == "Cost $16.00 · Profit $4.00 · Margin 20.00%"
        else:
            page.locator('#est-form [name="fee_pct"]').fill("5")
        assert not note.is_visible()

    _estimate_form(client, test)


def test_a_commercial_line_keeps_a_markup_made_through_the_api(
    client, seed_accounts, seed_customer
):
    _construction(client)
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-10",
            "fee_pct": 5,
            "lines": [
                {
                    "description": "Fence",
                    "quantity": 1,
                    "unit_cost": 100,
                    "markup_pct": 20,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    est_id = r.json()["id"]

    def test(page):
        assert page.locator('#est-form [name="fee_pct"]').count() == 1
        rate = _first_row(page).locator(".line-rate")
        assert rate.input_value() == "120"
        assert rate.evaluate("el => el.readOnly")
        page.get_by_role("button", name="Update Estimate").click()
        page.locator("#est-form").wait_for(state="hidden")

    _estimate_form(client, test, est_id)
    line = client.get(f"/api/estimates/{est_id}").json()["lines"][0]
    assert line["markup_pct"] == "20"
    assert Decimal(line["rate"]) == Decimal("120")


def test_a_four_place_markup_is_a_valid_entry(client, seed_customer):
    _construction(client, estimate_default_markup_pct="33.3333")
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-10",
            "lines": [{"quantity": 1, "unit_cost": 100, "markup_pct": "33.3333"}],
        },
    )
    assert r.status_code == 201, r.text

    def test(page):
        markup = _first_row(page).locator(".line-markup")
        assert markup.input_value() == "33.3333"
        assert markup.evaluate("el => el.checkValidity()")
        assert page.locator("#est-form").evaluate("el => el.checkValidity()")

    _estimate_form(client, test, r.json()["id"])


def test_a_business_save_keeps_the_markup_for_construction(client, seed_customer):
    _construction(client)
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-10",
            "lines": [{"quantity": 1, "unit_cost": 100, "markup_pct": 25}],
        },
    )
    assert r.status_code == 201, r.text
    est_id = r.json()["id"]
    r = client.put("/api/settings", json={"company_type": "business"})
    assert r.status_code == 200, r.text

    def test(page):
        page.get_by_role("button", name="Update Estimate").click()
        page.locator("#est-form").wait_for(state="hidden")

    _estimate_form(client, test, est_id)
    line = client.get(f"/api/estimates/{est_id}").json()["lines"][0]
    assert line["markup_pct"] == "25"
    assert Decimal(line["rate"]) == Decimal("125")
