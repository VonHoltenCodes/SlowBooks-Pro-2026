"""The construction estimate form priced by a markup on each line (#774), in
a real browser against the real routes.

Markup per line, chosen in Settings, shows no Package, Kind or Markups block; Price is cost ×
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


def _saved(client, est_id, page_test=None):
    """Open an estimate, click Update with nothing changed, return it."""

    def test(page):
        if page_test:
            page_test(page)
        page.get_by_role("button", name="Update Estimate").click()
        page.locator("#est-form").wait_for(state="hidden")

    _estimate_form(client, test, est_id)
    return client.get(f"/api/estimates/{est_id}").json()


def _api_estimate(client, customer_id, line, **extra):
    body = {"customer_id": customer_id, "date": "2026-11-10", "lines": [line]}
    r = client.post("/api/estimates", json={**body, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_markup_per_line_prices_each_line_and_saves_its_markup(client, seed_customer):
    _construction(
        client, estimate_style="line_markup", estimate_default_markup_pct="25"
    )
    client.post("/api/cost-codes", json={"code": "32 31 00", "name": "Fences"})

    def test(page):
        for gone in (".line-package", ".line-kind", '#est-form [name="gc_pct"]'):
            assert page.locator(gone).count() == 0, gone
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
        ] + ["Unit", "Cost", "Markup %", "Price", "Tax", "Amount"]
        page.locator("#est-customer-select").select_option(str(seed_customer.id))
        row = _first_row(page)
        assert row.locator(".line-markup").input_value() == "25"  # the default
        row.locator(".line-desc").fill("Fence")
        row.locator(".line-unit-cost").fill("16")
        row.locator(".line-qty").fill("300")
        assert row.locator(".line-rate").input_value() == "20"
        assert row.locator(".line-amount").inner_text() == "$6,000.00"
        row.locator(".line-unit-cost").fill("")  # no cost: the price is typed
        assert row.locator(".line-markup").is_disabled()
        assert not row.locator(".line-rate").evaluate("el => el.readOnly")
        row.locator(".line-unit-cost").fill("16")
        row.locator(".line-markup").fill("33.3333")  # four places are valid
        assert page.locator("#est-form").evaluate("el => el.checkValidity()")
        row.locator(".line-markup").fill("25")
        page.get_by_role("button", name="Create Estimate").click()
        page.locator("#est-form").wait_for(state="hidden")

    _estimate_form(client, test)
    (est,) = client.get("/api/estimates").json()
    line = client.get(f"/api/estimates/{est['id']}").json()["lines"][0]
    assert (line["markup_pct"], Decimal(line["rate"])) == ("25", Decimal("20"))


def test_the_form_rounds_a_half_as_the_server_does(client, seed_customer):
    _construction(client)

    def test(page):
        cases = [("3.51", "12.5"), ("1.41", "62.5"), ("-200", "25")]
        got = [
            page.evaluate("([c, m]) => EstimatesPage.pricedRate(c, m)", case)
            for case in cases
        ]
        assert got == ["3.9488", "2.2913", "-250"]

    _estimate_form(client, test)


def test_an_estimate_with_bid_tools_opens_as_a_commercial_bid(client, seed_customer):
    _construction(client)
    line = {"description": "Fence", "quantity": 1, "unit_cost": 100}

    def test(page):
        assert page.locator(".line-kind").count() == 1
        for row_id in ("est-bid-direct", "est-bid-allow", "est-bid-ins", "est-bid-tax"):
            assert not page.locator(f"#{row_id}").is_visible(), row_id  # $0 rows
        assert page.locator("#est-bid-fee").is_visible()

    est_id = _api_estimate(client, seed_customer.id, line, fee_pct=5)
    _estimate_form(client, test, est_id)


@pytest.mark.parametrize("style", ["line_markup", "commercial_bid"])
def test_a_bid_at_cost_says_so_until_a_markup_is_typed(client, seed_customer, style):
    _construction(client, estimate_style=style)

    def test(page):
        note = page.locator("#est-at-cost")
        row = _first_row(page)
        row.locator(".line-unit-cost").fill("16")
        if style == "line_markup":
            row.locator(".line-markup").fill("0")
        assert note.is_visible()
        if style == "line_markup":
            row.locator(".line-markup").fill("25")
        else:
            page.locator('#est-form [name="fee_pct"]').fill("5")
        assert not note.is_visible()

    _estimate_form(client, test)


@pytest.mark.parametrize(
    "company,line,extra",
    [
        # a commercial bid keeps a markup made through the API
        (
            "construction",
            {"quantity": 1, "unit_cost": 100, "markup_pct": 20},
            {"fee_pct": 5},
        ),
        # a line with no cost keeps the markup it carries
        ("construction", {"quantity": 2, "rate": 100, "markup_pct": 20}, {}),
        # so does a Business company's save
        ("business", {"quantity": 1, "unit_cost": 100, "markup_pct": 20}, {}),
        # and a quantity of 0 stays 0
        ("construction", {"quantity": 0, "unit_cost": 100, "markup_pct": 20}, {}),
    ],
)
def test_an_unchanged_save_changes_nothing(client, seed_customer, company, line, extra):
    _construction(client)
    est_id = _api_estimate(client, seed_customer.id, line, **extra)
    before = client.get(f"/api/estimates/{est_id}").json()
    client.put("/api/settings", json={"company_type": company})
    after = _saved(client, est_id)
    keys = ("quantity", "rate", "markup_pct")
    assert [after["lines"][0][k] for k in keys] == [before["lines"][0][k] for k in keys]
    assert after["total"] == before["total"]
