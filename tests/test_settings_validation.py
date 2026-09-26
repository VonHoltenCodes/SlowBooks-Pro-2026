"""Settings refuse values that cannot work (explore 2.17.3: skytech M4,
macbase1 F4).

A default tax rate of 150% or -5%, and a next invoice number of "abc" or 0,
all answered "Settings saved". Every new invoice then defaulted to that rate
and was refused with "tax_rate: Value error, tax_rate cannot be negative" /
"tax_rate is a fraction ... 1.5 looks like a percent — divide by 100",
although the person had typed into a field labelled "Tax Rate (%)" and
nothing pointed back at Settings.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(
    encoding="utf-8"
)

# A module constant, never a literal beside a username key.
SETUP_PW = "long-enough-pw"


def _settings(client):
    return client.get("/api/settings").json()


@pytest.mark.parametrize("bad", ["150", "-5", "100.01", "abc", "NaN", "Infinity"])
def test_a_default_tax_rate_outside_0_to_100_is_refused(client, bad):
    before = _settings(client)["default_tax_rate"]
    r = client.put("/api/settings", json={"default_tax_rate": bad})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Default tax rate must be a number from 0 to 100" in detail
    assert "8.25 means 8.25%" in detail
    assert _settings(client)["default_tax_rate"] == before


@pytest.mark.parametrize(
    "typed, stored", [("8.25", "8.25"), ("0", "0"), ("100", "100"), ("", "0")]
)
def test_an_ordinary_default_tax_rate_saves(client, typed, stored):
    r = client.put("/api/settings", json={"default_tax_rate": typed})
    assert r.status_code == 200, r.text
    assert _settings(client)["default_tax_rate"] == stored


@pytest.mark.parametrize("key", ["invoice_next_number", "estimate_next_number"])
@pytest.mark.parametrize("bad", ["abc", "0", "-3", "1.5", "", "12a"])
def test_a_next_number_must_be_a_whole_number_from_1(client, key, bad):
    before = _settings(client)[key]
    r = client.put("/api/settings", json={key: bad})
    assert r.status_code == 422, r.text
    assert "must be a whole number, 1 or more" in r.json()["detail"]
    assert _settings(client)[key] == before


def test_a_next_number_saves_as_the_number_it_is(client):
    r = client.put(
        "/api/settings",
        json={"invoice_next_number": " 0042 ", "estimate_next_number": "5001"},
    )
    assert r.status_code == 200, r.text
    s = _settings(client)
    # the typed zeros stay: they set how invoice numbers are padded
    assert s["invoice_next_number"] == "0042"
    assert s["estimate_next_number"] == "5001"


@pytest.mark.parametrize(
    "key, bad, sentence",
    [
        ("late_fee_rate", "250", "Late fee rate must be a number from 0 to 100"),
        ("late_fee_grace_days", "-1", "Grace days must be a whole number, 0 or more"),
        ("late_fee_grace_days", "ten", "Grace days must be a whole number, 0 or more"),
        ("smtp_port", "70000", "SMTP port must be a whole number from 1 to 65535"),
        ("smtp_port", "0", "SMTP port must be a whole number from 1 to 65535"),
        ("smtp_port", "", "SMTP port must be a whole number from 1 to 65535"),
        ("closing_date", "June 30", "Closing date must be a date (YYYY-MM-DD)"),
        ("closing_date", "2026-02-30", "Closing date must be a date (YYYY-MM-DD)"),
    ],
)
def test_the_other_numeric_settings_are_checked_the_same_way(
    client, key, bad, sentence
):
    r = client.put("/api/settings", json={key: bad})
    assert r.status_code == 422, r.text
    assert sentence in r.json()["detail"]


def test_a_closing_date_that_is_not_a_date_does_not_switch_the_lock_off(client):
    """An unparseable closing date used to be stored as typed and then read
    as "no closing date"."""
    assert client.put("/api/settings", json={"closing_date": "2026-06-30"}).is_success
    r = client.put("/api/settings", json={"closing_date": "30/06/2026"})
    assert r.status_code == 422, r.text
    assert _settings(client)["closing_date"] == "2026-06-30"
    # Empty still means "no closing date".
    assert client.put("/api/settings", json={"closing_date": ""}).is_success
    assert _settings(client)["closing_date"] == ""


def test_one_bad_value_saves_none_of_the_form(client):
    before = _settings(client)
    r = client.put(
        "/api/settings",
        json={
            "company_name": "Harbor Light Bakery",
            "default_tax_rate": "-5",
            "invoice_next_number": "abc",
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # every problem at once, and the fact that nothing changed
    assert "Default tax rate" in detail and "Next invoice number" in detail
    assert detail.endswith("Nothing was saved.")
    after = _settings(client)
    assert after["company_name"] == before["company_name"]


def test_first_run_setup_checks_the_default_tax_rate_too(unauthed_client):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": SETUP_PW, "default_tax_rate": "150"},
    )
    assert r.status_code == 422, r.text
    assert "Default tax rate must be a number from 0 to 100" in r.json()["detail"]
    # nothing was set up: no password, no half-written settings
    assert unauthed_client.get("/api/auth/status").json()["setup_needed"] is True

    ok = unauthed_client.post(
        "/api/auth/setup",
        json={"password": SETUP_PW, "default_tax_rate": "8.25"},
    )
    assert ok.status_code == 200, ok.text
    assert unauthed_client.get("/api/settings").json()["default_tax_rate"] == "8.25"


def _customer(client):
    r = client.post("/api/customers", json={"name": "Tidewater Cafe"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.parametrize(
    "rate, shown", [(1.5, "Tax rate 150% is more than 100%"), (-0.05, "Tax rate -5%")]
)
def test_the_invoice_tax_error_reads_in_percent_and_points_at_settings(
    client, rate, shown
):
    """What a company with a bad default (saved before this release) meets
    on the invoice form: the rate it typed, in percent, and where to fix it."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": _customer(client),
            "date": "2026-09-01",
            "tax_rate": rate,
            "lines": [{"description": "Sourdough", "quantity": 1, "rate": 85}],
        },
    )
    assert r.status_code == 422, r.text
    assert shown in r.text
    assert "Default Tax Rate in Settings" in r.text
    assert "cannot be negative" not in r.text


def test_the_settings_inputs_carry_the_same_limits():
    def tag(name):
        i = SETTINGS_JS.index(f'name="{name}"')
        return SETTINGS_JS[
            SETTINGS_JS.rindex("<input", 0, i) : SETTINGS_JS.index(">", i)
        ]

    tax = tag("default_tax_rate")
    assert 'min="0"' in tax and 'max="100"' in tax and 'step="0.01"' in tax
    for key in ("invoice_next_number", "estimate_next_number"):
        t = tag(key)
        assert 'pattern="[0-9]*[1-9][0-9]*"' in t and "required" in t
    assert 'min="1" max="65535"' in tag("smtp_port")
    assert 'min="0" max="100"' in tag("late_fee_rate")
    assert 'min="0" step="1"' in tag("late_fee_grace_days")
