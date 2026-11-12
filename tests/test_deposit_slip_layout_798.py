"""Deposits on the bank's pre-printed deposit slips (#798).

Settings -> Payroll and checks -> Check Printing -> Deposit slips chooses
the deposit slip stock: plain paper (SlowBooks' own slip, #260, the default)
or the bank's pre-printed slips. On pre-printed slips Print on a deposit lays
only the figures -- the date, the account, the cash, each check's number and
amount, the checks subtotal and the total -- each where the company's layout
puts it, in inches from the slip's top-left, plus a left/right and up/down
nudge, and prints no labels, no MICR line and no routing or account digits.
The layout is one setting, deposit_slip_layout, JSON of what differs from
the defaults; Print a test slip lays sample figures onto it, saved or not.

Every test here reads the rendered HTML (render_pdf replaced), so nothing new
renders with WeasyPrint and nothing skips on Windows.
"""

import json
import re

import pytest

from app.services import pdf_service
from app.services.settings_service import DEPOSIT_SLIP_DEFAULTS

_FIELD = re.compile(
    r'<div class="slip-field slip-(\w+)( slip-amount)?" data-field="\w+" '
    r'style="left: ([-\d.]+)in; top: ([-\d.]+)in; font-size: ([\d.]+)pt;">([^<]*)</div>'
)


def _pages(html):
    """Each page's figures: [(field, text, left, top, size)]."""
    out = []
    for page in html.split('<div class="slip-page">')[1:]:
        out.append(
            [
                (f, text, float(left), float(top), float(size))
                for f, _amount, left, top, size, text in _FIELD.findall(page)
            ]
        )
    return out


def _has(page, field, text, left, top, size):
    """Whether the page prints ``text`` as ``field`` at that place and size
    (inches and points, to a ten-thousandth)."""
    want = (left, top, size)
    return any(
        f[0] == field
        and f[1] == text
        and all(abs(a - b) < 1e-4 for a, b in zip(f[2:], want))
        for f in page
    )


@pytest.fixture
def as_html(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


@pytest.fixture
def acme(client):
    return client.post("/api/customers", json={"name": "Acme Diner"}).json()["id"]


def _receive(client, cid, amount, check=None, method="Check"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": method,
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text


def _deposit(client, seed_accounts):
    pending = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-12",
            "reference": "SLIP-7",
            "line_ids": [p["transaction_line_id"] for p in pending],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["transaction_id"]


@pytest.fixture
def deposit(client, seed_accounts, acme):
    """Two checks and some cash, deposited to Checking."""
    _receive(client, acme, "332.30", check="4420")
    _receive(client, acme, "100.00", check="4421")
    _receive(client, acme, "25.00", method="Cash")
    return _deposit(client, seed_accounts)


def _put(client, **settings):
    r = client.put("/api/settings", json=settings)
    assert r.status_code == 200, r.text


def _saved(client, key="deposit_slip_layout"):
    return client.get("/api/settings").json()[key]


# ----- the settings ----------------------------------------------------


def test_plain_paper_and_the_default_layout_out_of_the_box(client):
    s = client.get("/api/settings").json()
    assert s["deposit_slip_stock"] == "plain"
    assert s["deposit_slip_layout"] == ""
    assert s["deposit_slip_nudge_x_in"] == "0"
    assert s["deposit_slip_nudge_y_in"] == "0"


@pytest.mark.parametrize("stock", ["plain", "preprinted"])
def test_the_stock_saves_either_choice(client, stock):
    _put(client, deposit_slip_stock=stock)
    assert _saved(client, "deposit_slip_stock") == stock


def test_another_stock_is_refused(client):
    r = client.put("/api/settings", json={"deposit_slip_stock": "glossy"})
    assert r.status_code == 422
    assert "deposit_slip_stock must be one of: plain, preprinted" in r.json()["detail"]
    assert _saved(client, "deposit_slip_stock") == "plain"


def test_a_layout_saves_as_what_differs_from_the_defaults(client):
    sent = {
        "date": {"x": "1.25", "y": 0.9, "size": "", "show": True},
        "total": {"x": 7.5, "size": 12},
        "account": {"show": False},
        "cash": {},
        "check_lines": {"count": "6", "spacing": 0.25},
    }
    _put(client, deposit_slip_layout=json.dumps(sent))
    assert json.loads(_saved(client)) == {
        "account": {"show": False},
        "check_lines": {"count": 6},
        "date": {"x": 1.25},
        "total": {"size": 12, "x": 7.5},
    }
    # the object itself, as the API may send it
    _put(client, deposit_slip_layout={"check_lines": {"spacing": 0.3}})
    assert _saved(client) == '{"check_lines":{"spacing":0.3}}'


@pytest.mark.parametrize(
    "empty", ["", "{}", '{"date": {"x": 1, "y": "0.90", "size": 10, "show": true}}']
)
def test_a_layout_of_the_defaults_saves_as_empty(client, empty):
    _put(client, deposit_slip_layout='{"date":{"x":2}}')
    _put(client, deposit_slip_layout=empty)
    assert _saved(client) == ""


@pytest.mark.parametrize(
    "layout, says",
    [
        ({"date": {"x": 11.01}}, "Date from the left must be a number of inches"),
        ({"total": {"y": -0.1}}, "Total from the top must be a number of inches"),
        ({"cash": {"x": "abc"}}, "Cash from the left must be a number"),
        (
            {"check_amount": {"y": 1.005}},
            "Check amount (first line) from the top can have up to two decimal",
        ),
        ({"account": {"size": 19}}, "Account text size must be a number of points"),
        ({"date": {"size": 10.25}}, "Date text size can have one decimal place"),
        ({"date": {"show": "yes"}}, "Date show must be true or false."),
        ({"date": {"bold": True}}, "bold is not part of a field's layout"),
        ({"memo": {"x": 1}}, "memo is not a field; the fields are date, account"),
        ({"check_lines": {"count": 0}}, "Check lines: the number of lines must be"),
        ({"check_lines": {"count": 2.5}}, "Check lines: the number of lines must be"),
        ({"check_lines": {"spacing": 2}}, "Check lines: the spacing must be"),
        ({"check_lines": {"rows": 2}}, "rows is not part of the check lines"),
        ({"date": 3}, "Date: the layout must be an object."),
        ("[1, 2]", "Deposit slip layout must be an object of fields"),
        ("not json", "Deposit slip layout must be an object of fields"),
    ],
)
def test_a_layout_out_of_bounds_is_refused_and_nothing_is_saved(client, layout, says):
    _put(client, deposit_slip_layout='{"date":{"x":2}}')
    r = client.put(
        "/api/settings",
        json={
            "deposit_slip_layout": (
                layout if isinstance(layout, str) else json.dumps(layout)
            )
        },
    )
    assert r.status_code == 422, r.text
    assert says in r.json()["detail"]
    assert _saved(client) == '{"date":{"x":2}}'


def test_every_problem_of_a_layout_is_named_at_once(client):
    r = client.put(
        "/api/settings",
        json={"deposit_slip_layout": {"date": {"x": 12}, "total": {"size": 2}}},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Date from the left" in detail and "Total text size" in detail


@pytest.mark.parametrize("key", ["deposit_slip_nudge_x_in", "deposit_slip_nudge_y_in"])
def test_the_nudges_save_within_half_an_inch_and_refuse_more(client, key):
    _put(client, **{key: "-0.25"})
    assert _saved(client, key) == "-0.25"
    r = client.put("/api/settings", json={key: "0.6"})
    assert r.status_code == 422
    assert "must be a number of inches from -0.5 to 0.5" in r.json()["detail"]
    assert _saved(client, key) == "-0.25"


# ----- a deposit on pre-printed slips ------------------------------------


def test_the_plain_paper_slip_is_unchanged(client, deposit, as_html):
    before = client.get(f"/api/deposits/{deposit}/pdf").text
    # a layout and nudges set but plain paper chosen change nothing
    _put(
        client,
        deposit_slip_layout='{"date":{"x":3}}',
        deposit_slip_nudge_x_in="0.2",
    )
    after = client.get(f"/api/deposits/{deposit}/pdf").text
    assert after == before
    assert "Total deposit" in after and "slip-field" not in after


def test_on_preprinted_slips_the_figures_land_at_the_defaults(client, deposit, as_html):
    _put(client, deposit_slip_stock="preprinted")
    html = client.get(f"/api/deposits/{deposit}/pdf").text
    (page,) = _pages(html)
    d = DEPOSIT_SLIP_DEFAULTS
    spacing = d["check_lines"]["spacing"]
    width = 2.5  # an amount's box ends at its x
    assert _has(page, "date", "09/12/2026", d["date"]["x"], d["date"]["y"], 10)
    assert _has(page, "account", "Checking", d["account"]["x"], d["account"]["y"], 10)
    assert _has(page, "cash", "25.00", d["cash"]["x"] - width, d["cash"]["y"], 10)
    n, a = d["check_number"], d["check_amount"]
    assert _has(page, "check_number", "4420", n["x"], n["y"], 10)
    assert _has(page, "check_amount", "332.30", a["x"] - width, a["y"], 10)
    assert _has(page, "check_number", "4421", n["x"], n["y"] + spacing, 10)
    assert _has(page, "check_amount", "100.00", a["x"] - width, a["y"] + spacing, 10)
    s, t = d["checks_subtotal"], d["total"]
    assert _has(page, "checks_subtotal", "432.30", s["x"] - width, s["y"], 10)
    assert _has(page, "total", "457.30", t["x"] - width, t["y"], 10)
    assert len(page) == 9  # date, account, cash, two check lines, the totals


def test_preprinted_slips_print_no_labels_no_micr_and_no_bank_digits(
    client, deposit, as_html
):
    # the bank details a check on blank stock would print
    acct = client.get("/api/accounts").json()
    checking = next(a for a in acct if a["name"] == "Checking")
    client.put(
        f"/api/settings/check-bank-details/{checking['id']}",
        json={
            "bank_name": "First Prairie Bank",
            "routing_number": "071000013",
            "account_number": "4455667788",
        },
    )
    _put(client, deposit_slip_stock="preprinted", check_blank_stock="true")
    html = client.get(f"/api/deposits/{deposit}/pdf").text
    body = html.split("<body>", 1)[1]
    text = re.sub(r"<[^>]+>", " ", body)
    for label in (
        "DEPOSIT",
        "Deposited to",
        "Total deposit",
        "Cash",
        "check",
        "Received from",
        "Acme Diner",
        "SLIP-7",
        "Slowbooks",
        "$",
    ):
        assert label not in text, label
    assert "071000013" not in html and "4455667788" not in html
    assert "micr" not in html.lower() and "<svg" not in html and "<img" not in html


def test_the_layout_and_the_nudges_move_the_figures(client, deposit, as_html):
    _put(
        client,
        deposit_slip_stock="preprinted",
        deposit_slip_layout=json.dumps(
            {
                "date": {"x": 0.5, "y": 2, "size": 12},
                "total": {"x": 6, "y": 3},
                "check_number": {"x": 3.25, "y": 1},
                "check_lines": {"spacing": 0.5},
                "account": {"show": False},
            }
        ),
        deposit_slip_nudge_x_in="0.1",
        deposit_slip_nudge_y_in="-0.05",
    )
    (page,) = _pages(client.get(f"/api/deposits/{deposit}/pdf").text)
    assert _has(page, "date", "09/12/2026", 0.6, 1.95, 12)
    assert _has(page, "total", "457.30", 6.1 - 2.5, 2.95, 10)
    assert _has(page, "check_number", "4420", 3.35, 0.95, 10)
    assert _has(page, "check_number", "4421", 3.35, 1.45, 10)
    assert not [f for f in page if f[0] == "account"]


def test_checks_past_the_slips_lines_continue_on_another_page(
    client, seed_accounts, acme, as_html
):
    for n in range(5):
        _receive(client, acme, f"{10 + n}.00", check=f"50{n}")
    deposit = _deposit(client, seed_accounts)
    _put(
        client,
        deposit_slip_stock="preprinted",
        deposit_slip_layout='{"check_lines":{"count":2}}',
    )
    pages = _pages(client.get(f"/api/deposits/{deposit}/pdf").text)
    assert len(pages) == 3
    numbers = [[f[1] for f in p if f[0] == "check_number"] for p in pages]
    assert numbers == [["500", "501"], ["502", "503"], ["504"]]
    # the subtotal of every check and the total, on the first page only
    assert [f[1] for f in pages[0] if f[0] == "checks_subtotal"] == ["60.00"]
    assert [f[1] for f in pages[0] if f[0] == "total"] == ["60.00"]
    for p in pages[1:]:
        assert {f[0] for f in p} == {"date", "account", "check_number", "check_amount"}
    # with no cash on the deposit the cash box stays empty
    assert not [f for f in pages[0] if f[0] == "cash"]


def test_a_void_deposit_on_preprinted_slips_prints_void_and_no_figures(
    client, deposit, as_html
):
    _put(client, deposit_slip_stock="preprinted")
    assert client.post(f"/api/deposits/{deposit}/void").status_code == 200
    (page,) = _pages(client.get(f"/api/deposits/{deposit}/pdf").text)
    assert {f[0]: f[1] for f in page} == {
        "date": "09/12/2026",
        "account": "Checking",
        "total": "VOID",
    }


def test_the_print_page_is_the_same_slip(client, deposit):
    _put(client, deposit_slip_stock="preprinted")
    page = client.get(f"/api/deposits/{deposit}/print-preview").text
    assert 'data-field="total"' in page and "window.print()" in page
    assert "Total deposit" not in page


def test_a_damaged_stored_layout_prints_the_fields_that_check_out():
    from app.services.settings_service import deposit_slip_layout

    layout = deposit_slip_layout(
        '{"date": {"x": 99}, "total": {"x": 6}, "memo": {"x": 1}}'
    )
    assert layout["date"] == DEPOSIT_SLIP_DEFAULTS["date"]
    assert layout["total"]["x"] == 6
    assert "memo" not in layout
    assert deposit_slip_layout("not json") == DEPOSIT_SLIP_DEFAULTS


# ----- the test slip ---------------------------------------------------


def test_the_test_slip_lays_sample_figures_onto_the_stock(client, as_html):
    # the stock is plain: the test slip lines up the pre-printed one anyway
    r = client.get("/api/deposits/test-slip")
    assert r.status_code == 200, r.text
    assert "Deposit_slip_test_print.pdf" in r.headers["content-disposition"]
    (page,) = _pages(r.text)
    figures = {(f[0], f[1]) for f in page}
    assert ("account", "Sample Checking") in figures
    assert ("check_number", "1001") in figures and ("check_number", "1002") in figures
    assert ("check_amount", "1,234.56") in figures
    assert ("cash", "75.00") in figures
    assert ("checks_subtotal", "1,484.56") in figures
    assert ("total", "1,559.56") in figures
    assert "Total deposit" not in r.text
    assert _saved(client, "deposit_slip_stock") == "plain"


def test_the_test_slip_uses_the_unsaved_layout_and_nudges(client, as_html):
    _put(client, deposit_slip_layout='{"total":{"x":5}}')
    r = client.get(
        "/api/deposits/test-slip",
        params={
            "deposit_slip_layout": '{"total":{"x":6,"y":4}}',
            "deposit_slip_nudge_x_in": "0.5",
            "deposit_slip_nudge_y_in": "0.25",
        },
    )
    assert r.status_code == 200, r.text
    (page,) = _pages(r.text)
    assert _has(page, "total", "1,559.56", 6.5 - 2.5, 4.25, 10)
    # nothing is stored; left out, the saved layout prints
    assert _saved(client) == '{"total":{"x":5}}'
    (page,) = _pages(client.get("/api/deposits/test-slip").text)
    total = next(f for f in page if f[0] == "total")
    assert total[2] == 5 - 2.5


@pytest.mark.parametrize(
    "params, says",
    [
        ({"deposit_slip_layout": '{"date":{"x":20}}'}, "Date from the left"),
        ({"deposit_slip_nudge_x_in": "1"}, "Deposit slip left/right nudge must be"),
        ({"deposit_slip_nudge_y_in": "x"}, "Deposit slip up/down nudge must be"),
    ],
)
def test_the_test_slip_refuses_a_bad_unsaved_layout(client, params, says):
    r = client.get("/api/deposits/test-slip", params=params)
    assert r.status_code == 422
    assert says in r.json()["detail"]


# ----- the settings page -------------------------------------------------


def test_the_settings_page_has_the_deposit_slip_block():
    src = open("app/static/js/settings.js", encoding="utf-8").read()
    assert "${SettingsPage._depositSlipHtml(s)}" in src
    assert "window.open('/api/deposits/test-slip'" in src
    for name in (
        "deposit_slip_layout",
        "deposit_slip_nudge_x_in",
        "deposit_slip_nudge_y_in",
    ):
        assert f"'{name}'" in src
    # the page's defaults are the server's
    for key, label, x, y, _kind in re.findall(
        r"\['(\w+)', '([^']+)', ([\d.]+), ([\d.]+), '(text|amount)'\]", src
    ):
        assert DEPOSIT_SLIP_DEFAULTS[key]["x"] == float(x), key
        assert DEPOSIT_SLIP_DEFAULTS[key]["y"] == float(y), key
    assert "DEPOSIT_SLIP_LINES: { count: 8, spacing: 0.25 }" in src
