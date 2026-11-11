"""A recurring invoice's frequency is one the generator steps by (#779).

POST /api/recurring with "frequency": "fortnightly" answered 201 and saved
the schedule, which the generator then stepped as monthly: nobody chose
that and nothing said so. Create and update now refuse any other word with
one sentence naming the allowed ones, and the OpenAPI schema lists them.
"""

import re
from datetime import date
from pathlib import Path

import pytest

from app.schemas.recurring import FREQUENCY_SENTENCE
from app.services.recurring_service import (
    FREQUENCIES,
    _advance_next_due,
    advance_date,
)

RECURRING_JS = (
    Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "recurring.js"
)
LINE = {"description": "Service", "quantity": 1, "rate": 100}

# The occurrence after 2026-01-31 for each word, held to the 31st.
EXPECTED_NEXT = {
    "daily": date(2026, 2, 1),
    "weekly": date(2026, 2, 7),
    "semimonthly": date(2026, 2, 16),
    "monthly": date(2026, 2, 28),
    "quarterly": date(2026, 4, 30),
    "yearly": date(2027, 1, 31),
}


def _create(client, customer_id, frequency):
    return client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": frequency,
            "start_date": "2026-01-31",
            "lines": [LINE],
        },
    )


def _frequency_messages(response):
    return [
        e["message"] for e in response.json()["detail"] if e["loc"][-1] == "frequency"
    ]


def test_the_sentence_names_every_allowed_frequency():
    assert FREQUENCY_SENTENCE == (
        "Frequency must be daily, weekly, semimonthly (twice a month), monthly, "
        "quarterly or yearly."
    )
    for word in FREQUENCIES:
        assert word in FREQUENCY_SENTENCE


def test_a_schedule_made_fortnightly_is_refused_with_the_sentence(
    client, db_session, seed_accounts, seed_customer
):
    r = _create(client, seed_customer.id, "fortnightly")
    assert r.status_code == 422, r.text
    assert _frequency_messages(r) == [FREQUENCY_SENTENCE]
    assert client.get("/api/recurring").json() == []


@pytest.mark.parametrize(
    "bad", ["fortnightly", "biweekly", "Monthly", " monthly", "", 7, None]
)
def test_a_schedule_made_with_any_other_frequency_is_refused(
    client, db_session, seed_accounts, seed_customer, bad
):
    r = _create(client, seed_customer.id, bad)
    assert r.status_code == 422, r.text
    assert _frequency_messages(r) == [FREQUENCY_SENTENCE]


def test_a_schedule_changed_to_an_unknown_frequency_is_refused_and_kept(
    client, db_session, seed_accounts, seed_customer
):
    rec = _create(client, seed_customer.id, "monthly").json()
    r = client.put(f"/api/recurring/{rec['id']}", json={"frequency": "fortnightly"})
    assert r.status_code == 422, r.text
    assert _frequency_messages(r) == [FREQUENCY_SENTENCE]
    assert client.get(f"/api/recurring/{rec['id']}").json()["frequency"] == "monthly"


@pytest.mark.parametrize("word", FREQUENCIES)
def test_every_allowed_frequency_creates_a_schedule_due_on_its_start_date(
    client, db_session, seed_accounts, seed_customer, word
):
    r = _create(client, seed_customer.id, word)
    assert r.status_code == 201, r.text
    assert r.json()["frequency"] == word
    assert r.json()["next_due"] == "2026-01-31"


@pytest.mark.parametrize("word", FREQUENCIES)
def test_every_allowed_frequency_can_be_chosen_on_an_edit(
    client, db_session, seed_accounts, seed_customer, word
):
    start = "yearly" if word == "monthly" else "monthly"
    rec = _create(client, seed_customer.id, start).json()
    r = client.put(f"/api/recurring/{rec['id']}", json={"frequency": word})
    assert r.status_code == 200, r.text
    assert r.json()["frequency"] == word


@pytest.mark.parametrize("word", FREQUENCIES)
def test_the_generator_steps_every_allowed_frequency_to_its_own_next_date(word):
    assert _advance_next_due(date(2026, 1, 31), word, 31) == EXPECTED_NEXT[word]


def test_no_two_allowed_frequencies_step_alike():
    # An unknown word fell through to a month; every allowed one is its own.
    steps = {advance_date(date(2026, 1, 31), w, 1, 31) for w in FREQUENCIES}
    assert len(steps) == len(FREQUENCIES)


def test_the_api_schema_lists_the_allowed_frequencies(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    create = schemas["RecurringCreate"]["properties"]["frequency"]
    assert create["enum"] == list(FREQUENCIES)
    update = schemas["RecurringUpdate"]["properties"]["frequency"]
    enums = [s["enum"] for s in update["anyOf"] if "enum" in s]
    assert enums == [list(FREQUENCIES)]


def test_every_frequency_the_form_offers_is_one_the_api_accepts():
    js = RECURRING_JS.read_text(encoding="utf-8")
    m = re.search(r"<select name=\"frequency\">\s*\$\{\[([^\]]*)\]", js)
    assert m, "the recurring form's frequency select was not found"
    offered = re.findall(r"'([^']+)'", m.group(1))
    assert offered, "the frequency select offers nothing"
    assert set(offered) <= set(FREQUENCIES)
    assert "monthly" in offered


@pytest.mark.parametrize("word", FREQUENCIES)
def test_generating_an_installment_moves_next_due_by_its_frequency(
    client, db_session, seed_accounts, seed_customer, word
):
    rec = _create(client, seed_customer.id, word).json()
    r = client.post(
        f"/api/recurring/{rec['id']}/generate", params={"occurrence": "2026-01-31"}
    )
    assert r.status_code in (200, 201), r.text
    after = client.get(f"/api/recurring/{rec['id']}").json()
    assert after["next_due"] == EXPECTED_NEXT[word].isoformat()
    assert after["invoices_created"] == 1
