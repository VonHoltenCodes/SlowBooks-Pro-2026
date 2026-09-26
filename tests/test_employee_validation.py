"""Employee values payroll cannot use are refused, in words (2.17.3
exploratory test, skytech W-L4).

SSN last 4 "abcd", a pay rate of -5 and a work state of "Illinois" were all
saved. The state is how payroll finds the withholding engine: "Illinois"
matches none, so it withheld no state income tax at all (and the column holds
two characters, so PostgreSQL would have refused it with a 500).
"""

from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _create(client, **fields):
    return client.post(
        "/api/employees",
        json={"first_name": "Hana", "last_name": "Lee", **fields},
    )


@pytest.mark.parametrize(
    "fields, words",
    [
        ({"ssn_last_four": "abcd"}, "SSN last 4 must be four digits"),
        ({"ssn_last_four": "123"}, "SSN last 4 must be four digits"),
        ({"pay_rate": -5}, "Pay rate can't be negative"),
        ({"work_state": "Illinois"}, "Work state must be a two-letter state code"),
        ({"work_state": "ZZ"}, "Work state must be a two-letter state code"),
        ({"residence_state": "Ore"}, "Residence state must be a two-letter state code"),
    ],
)
def test_values_payroll_cannot_use_are_refused(client, fields, words):
    r = _create(client, **fields)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith(words), r.json()["detail"]


def test_the_same_values_are_refused_on_edit(client):
    emp = _create(client, pay_rate=22, work_state="IL").json()
    for fields in (
        {"ssn_last_four": "12a4"},
        {"pay_rate": -1},
        {"work_state": "Illinois"},
    ):
        r = client.put(f"/api/employees/{emp['id']}", json=fields)
        assert r.status_code == 400, (fields, r.text)
    kept = client.get(f"/api/employees/{emp['id']}").json()
    assert kept["pay_rate"] == 22 and kept["work_state"] == "IL"


def test_good_values_are_kept_and_tidied(client):
    r = _create(
        client,
        ssn_last_four=" 0042 ",
        pay_rate=0,
        work_state="or",
        residence_state="wa",
    )
    assert r.status_code == 201, r.text
    emp = r.json()
    assert emp["ssn_last_four"] == "0042"
    assert (emp["work_state"], emp["residence_state"]) == ("OR", "WA")
    # the form sends blanks for fields left empty
    r = _create(client, ssn_last_four="", work_state="", residence_state="")
    assert r.status_code == 201, r.text
    emp = r.json()
    assert emp["ssn_last_four"] is None and emp["work_state"] is None


def test_the_form_asks_for_the_right_shapes():
    js = (JS / "employees.js").read_text(encoding="utf-8")
    form = js[js.index("async showForm(") : js.index("async save(")]
    assert 'name="ssn_last_four" maxlength="4" pattern="[0-9]{4}"' in form
    assert 'name="pay_rate" type="number" step="0.01" min="0"' in form
    assert 'name="work_state" maxlength="2" pattern="[A-Za-z]{2}"' in form
    assert 'name="residence_state" maxlength="2" pattern="[A-Za-z]{2}"' in form
