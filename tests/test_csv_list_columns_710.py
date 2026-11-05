"""#710: the list imports say their columns, ship templates and take an
income account.

A plain item list (Item, Description, Rate, Unit, Income account) answered
"Row 2: Missing name" on every row and nothing said which headings the
importer reads. Headings now match without regard to case, spaces,
underscores or hyphens, with the usual other names; a file with no name
column gets one error naming its headings; an item row may name its income
account by number, name or QuickBooks path; the CSV page lists the columns
and links a template for each list, served from /static/downloads/.
"""

import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "app" / "static" / "downloads"


def _upload(client, entity, text):
    r = client.post(
        f"/api/csv/import/{entity}",
        files={"file": (f"{entity}.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _items(client):
    return {i["name"]: i for i in client.get("/api/items").json()}


def _account_id(client, number):
    return next(
        a["id"]
        for a in client.get("/api/accounts").json()
        if a["account_number"] == number
    )


# ── headings ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "header",
    [
        "Item Name,Sales Price,Unit Cost",
        "item_name,price,unit_cost",
        "ITEM,PRICE,COST",
        "  Name ,Rate,Unit-Cost",
    ],
)
def test_item_headings_match_whatever_their_case_spacing_or_alias(
    client, seed_accounts, header
):
    out = _upload(client, "items", f"{header}\nDesign Hour,85.00,30.00\n")
    assert out == {"created": 1, "skipped": 0, "errors": []}
    item = _items(client)["Design Hour"]
    assert item["rate"] == "85.00" and item["cost"] == "30.00"


def test_customer_and_vendor_headings_take_the_list_s_own_name(client):
    out = _upload(
        client,
        "customers",
        "Customer,Street,Zip Code,E-mail\nCedar Dental,212 Main St,60999,a@example.com\n",
    )
    assert out == {"created": 1, "skipped": 0, "errors": []}
    cust = client.get("/api/customers").json()[0]
    assert (cust["name"], cust["bill_address1"], cust["bill_zip"]) == (
        "Cedar Dental",
        "212 Main St",
        "60999",
    )
    out = _upload(
        client, "vendors", "Vendor Name,Street\nBrookside Nursery,5150 Rt 47\n"
    )
    assert out == {"created": 1, "skipped": 0, "errors": []}
    vendor = client.get("/api/vendors").json()[0]
    assert (vendor["name"], vendor["address1"]) == ("Brookside Nursery", "5150 Rt 47")


def test_the_column_s_own_name_wins_over_an_alias(client, seed_accounts):
    out = _upload(client, "items", "Item,Name,Rate\nWidget family,Widget,5\n")
    assert out["created"] == 1
    assert list(_items(client)) == ["Widget"]


def test_a_file_with_no_name_column_says_so_once(client, seed_accounts):
    out = _upload(client, "items", "Product,Rate\nWidget,5\nGadget,6\n")
    assert out["created"] == 0
    assert out["errors"] == [
        'No Name column: the file\'s headings are "Product", "Rate". Head the '
        "name column one of Name, Item Name, Item."
    ]
    out = _upload(client, "vendors", "Supplier,Phone\nAcme,555\n")
    assert len(out["errors"]) == 1 and "Vendor Name, Vendor" in out["errors"][0]


def test_a_blank_name_and_a_bad_price_name_the_column(client, seed_accounts):
    out = _upload(client, "items", "Item Name,Price\n,5\nWidget,abc\n")
    assert out["errors"] == [
        'Row 2: Missing name: the "Item Name" column is blank.',
        'Row 3: Price "abc" is not a number.',
    ]


def test_varies_is_a_price_set_on_each_sale(client, seed_accounts):
    out = _upload(client, "items", "Item,Rate\nWeekly Mowing,varies\n")
    assert out == {"created": 1, "skipped": 0, "errors": []}
    assert _items(client)["Weekly Mowing"]["rate"] == "0.00"


# ── the income account ──────────────────────────────────────────────────


def test_an_item_lands_on_the_income_account_its_row_names(client, seed_accounts):
    lawn = client.post(
        "/api/accounts",
        json={
            "name": "Lawn Maintenance",
            "account_number": "4510",
            "account_type": "income",
        },
    )
    assert lawn.status_code == 201, lawn.text
    # a chart imported from QuickBooks keeps the path as the name
    snow = client.post(
        "/api/accounts",
        json={
            "name": "Landscaping Services:Snow Removal",
            "account_number": "4520",
            "account_type": "income",
        },
    )
    assert snow.status_code == 201, snow.text
    out = _upload(
        client,
        "items",
        "Item,Rate,Income account\n"
        "Design Hour,85,4100\n"
        "Labor Hour,65,labor income\n"
        "Weekly Mowing,varies,Lawn Maintenance\n"
        "Plowing,varies,Landscaping Services:Snow Removal\n"
        "Odd Job,10,\n",
    )
    assert out == {"created": 5, "skipped": 0, "errors": []}
    items = _items(client)
    assert items["Design Hour"]["income_account_id"] == _account_id(client, "4100")
    assert items["Labor Hour"]["income_account_id"] == _account_id(client, "4300")
    assert items["Weekly Mowing"]["income_account_id"] == lawn.json()["id"]
    assert items["Plowing"]["income_account_id"] == snow.json()["id"]
    assert items["Odd Job"]["income_account_id"] is None  # the default, as before


def test_a_sub_account_is_found_by_its_parent_s_path(client, seed_accounts):
    parent = client.post(
        "/api/accounts",
        json={
            "name": "Landscaping",
            "account_number": "4600",
            "account_type": "income",
        },
    ).json()
    child = client.post(
        "/api/accounts",
        json={
            "name": "Installation",
            "account_number": "4610",
            "account_type": "income",
            "parent_id": parent["id"],
        },
    )
    assert child.status_code == 201, child.text
    out = _upload(client, "items", "Name,Account\nMulch,Landscaping:Installation\n")
    assert out["errors"] == []
    assert _items(client)["Mulch"]["income_account_id"] == child.json()["id"]


def test_an_income_account_the_chart_lacks_or_that_is_not_income_is_refused(
    client, seed_accounts
):
    out = _upload(
        client,
        "items",
        "Name,Income Account\nMowing,Lawn Care\nRent,6100\nGood,4000\n",
    )
    assert out["created"] == 1
    assert out["errors"][0].startswith(
        'Row 2: Income Account "Lawn Care" is not in the chart of accounts.'
    )
    assert out["errors"][0].endswith("Mowing was not imported.")
    assert out["errors"][1].startswith(
        'Row 3: Income Account "6100" is Auto & Truck Expense, an expense account; an item\'s '
        "income account must be an income account."
    )
    assert list(_items(client)) == ["Good"]


# ── the templates and the page ───────────────────────────────────────────

TEMPLATES = {
    "items": "items-template.csv",
    "customers": "customers-template.csv",
    "vendors": "vendors-template.csv",
}


@pytest.mark.parametrize("entity", sorted(TEMPLATES))
def test_each_template_is_served_and_imports_cleanly(client, seed_accounts, entity):
    path = DOWNLOADS / TEMPLATES[entity]
    text = path.read_text(encoding="utf-8")
    r = client.get(f"/static/downloads/{TEMPLATES[entity]}")
    assert r.status_code == 200 and r.text == text
    rows = text.strip().count("\n")
    out = _upload(client, entity, text)
    assert out == {"created": rows, "skipped": 0, "errors": []}


def test_the_items_template_puts_its_items_on_their_income_accounts(
    client, seed_accounts
):
    _upload(client, "items", (DOWNLOADS / "items-template.csv").read_text())
    items = _items(client)
    assert items["Neon Tube 4ft"]["income_account_id"] == _account_id(client, "4100")
    assert items["Installation Labor"]["income_account_id"] == _account_id(
        client, "4300"
    )
    assert items["Loyalty Discount"]["allows_negative_price"] is True


def test_the_template_headings_are_the_columns_the_importer_reads():
    from app.services.csv_import import (
        CUSTOMER_COLUMNS,
        ITEM_COLUMNS,
        VENDOR_COLUMNS,
    )

    for name, columns in (
        ("items", ITEM_COLUMNS),
        ("customers", CUSTOMER_COLUMNS),
        ("vendors", VENDOR_COLUMNS),
    ):
        header = (DOWNLOADS / TEMPLATES[name]).read_text().splitlines()[0]
        assert header.split(",") == list(columns), name


def test_the_csv_page_lists_the_columns_and_links_each_template():
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for template in (*TEMPLATES.values(), "chart-of-accounts-template.csv"):
        assert f"template: '{template}'" in js
    assert 'href="/static/downloads/${spec.template}" download' in js
    assert "Income Account (number or name" in js
    assert "${T('Customer')} Name" in js
