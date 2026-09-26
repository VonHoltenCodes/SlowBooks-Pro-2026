"""An account number is digits (2.17.3 exploratory test, W-L4).

"ABC" and a blank number were accepted on the chart of accounts, and the
blank one then listed as " - No Number Acct" in every picker. A number is
now digits, optionally in dotted or dashed groups for a sub-account — the
pattern the seeded chart and the chart importer use.
"""

from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"


def _create(client, number, name="Account"):
    return client.post(
        "/api/accounts",
        json={"name": name, "account_type": "expense", "account_number": number},
    )


@pytest.mark.parametrize(
    "number", ["ABC", "61a0", "6150.", "-6150", "6150 1", "1" * 21]
)
def test_a_number_that_is_not_digits_is_refused(client, number):
    r = _create(client, number)
    assert r.status_code == 400, r.text
    assert "is not an account number" in r.json()["detail"]
    assert "6150.1 or 6150-01" in r.json()["detail"]


@pytest.mark.parametrize("number", ["6150", "6150.1", "6150-01", "1000.10.2"])
def test_plain_and_sub_account_numbers_are_accepted(client, number):
    r = _create(client, number, name=f"Acct {number}")
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] == number


def test_renumbering_to_letters_is_refused(client):
    acct = _create(client, "6150").json()
    r = client.put(f"/api/accounts/{acct['id']}", json={"account_number": "ABC"})
    assert r.status_code == 400
    assert client.get(f"/api/accounts/{acct['id']}").json()["account_number"] == "6150"


def test_the_account_form_asks_for_digits():
    js = APP_JS.read_text(encoding="utf-8")
    assert 'pattern="\\\\d+([.\\\\-]\\\\d+)*"' in js
    assert "numberRequired ? 'required' : ''" in js
