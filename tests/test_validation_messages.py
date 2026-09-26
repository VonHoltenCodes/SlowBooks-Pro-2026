"""A refused request says what to fix in a sentence (explore 2.17.3, skytech L5).

Pydantic's text reached people as "name: String should have at least 1
character" and "String should have at most 200 characters", and the name
inputs had no maxlength. Every 422 entry now also carries "message", a plain
sentence naming the field as the form labels it; type/loc/msg are unchanged
for agents. The page shows the sentence.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _messages(r):
    assert r.status_code == 422, r.text
    return [e["message"] for e in r.json()["detail"]]


@pytest.mark.parametrize(
    "body, sentence",
    [
        ({"name": ""}, "Name is required."),
        ({"name": "   "}, "Name is required."),
        ({}, "Name is required."),
        ({"name": "x" * 201}, "Name must be 200 characters or fewer."),
        (
            {"name": "Acme", "email": "not-an-email"},
            "Email must be an email address, like name@example.com.",
        ),
        ({"name": "Acme", "credit_limit": "abc"}, "Credit limit must be a number."),
        ({"name": "Acme", "bogus": 1}, "'bogus' is not a field this accepts."),
    ],
)
def test_customer_form_refusals_read_as_sentences(client, body, sentence):
    r = client.post("/api/customers", json=body)
    assert _messages(r) == [sentence]


def test_the_validator_fields_are_still_there_for_agents(client):
    r = client.post("/api/customers", json={"name": ""})
    [entry] = r.json()["detail"]
    assert entry["type"] == "string_too_short"
    assert entry["loc"] == ["body", "name"]
    assert entry["msg"] == "String should have at least 1 character"
    assert entry["message"] == "Name is required."


def test_invoice_refusals_name_the_field_and_the_line(client):
    r = client.post("/api/invoices", json={"date": "2026-09-01", "lines": []})
    assert _messages(r) == [
        "Customer is required.",
        "Invoice must have at least one line.",
    ]

    r = client.post(
        "/api/invoices",
        json={"customer_id": 1, "date": "09/01/2026", "lines": [{"description": "x"}]},
    )
    assert _messages(r) == ["Date must be a date (YYYY-MM-DD)."]

    r = client.post(
        "/api/invoices",
        json={
            "customer_id": 1,
            "date": "2026-09-01",
            "lines": [
                {"description": "ok", "quantity": 1, "rate": 5},
                {"description": "x", "quantity": -1, "rate": 5},
            ],
        },
    )
    assert _messages(r) == [
        "Line 2: Quantity must be non-negative; use a credit memo for refunds."
    ]


def test_the_api_note_stays_in_msg_and_out_of_the_sentence(client):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": 1,
            "date": "2026-09-01",
            "tax_rate": 1.5,
            "lines": [{"description": "x", "quantity": 1, "rate": 5}],
        },
    )
    [entry] = r.json()["detail"]
    assert "divide by 100" in entry["msg"]  # agents still learn the unit
    assert entry["message"] == (
        "Tax rate 150% is more than 100%. Use a rate from 0 to 100%; if it "
        "came from the company default, correct Default Tax Rate in Settings."
    )


def test_other_shapes_of_refusal(client):
    r = client.post("/api/items", json={"name": "Thing", "item_type": "banana"})
    assert _messages(r) == [
        "Item type must be one of: product, service, material or labor."
    ]
    r = client.post(
        "/api/items",
        json={"name": "Thing", "item_type": "service", "is_taxable": "maybe"},
    )
    assert _messages(r) == ["Is taxable must be yes or no (true or false)."]
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-09-01",
            "description": "x",
            "lines": [{"account_id": 1, "debit": "abc", "credit": "0"}],
        },
    )
    assert _messages(r) == ["Line 1: Debit must be a number."]
    r = client.get("/api/invoices?limit=abc")
    assert _messages(r) == ["Limit must be a whole number."]


def test_a_nonprofit_reads_its_own_words(client):
    assert client.put("/api/settings", json={"company_type": "nonprofit"}).is_success
    r = client.post("/api/invoices", json={"date": "2026-09-01", "lines": []})
    assert _messages(r)[0] == "Donor is required."


def _probe(scenario):
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "api_request_probe.js"),
            json.dumps(scenario),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_shows_the_sentences_from_a_real_422(client):
    real = client.post("/api/invoices", json={"date": "2026-09-01", "lines": []})
    assert real.status_code == 422
    out = _probe(
        {
            "call": ["POST", "/invoices", {"date": "2026-09-01", "lines": []}],
            "responses": [{"status": 422, "body": real.json()}],
        }
    )
    assert out["error"] == {
        "message": "Customer is required. Invoice must have at least one line.",
        "status": 422,
    }

    real = client.post("/api/customers", json={"name": "x" * 201})
    out = _probe(
        {
            "call": ["POST", "/customers", {"name": "x" * 201}],
            "responses": [{"status": 422, "body": real.json()}],
        }
    )
    assert out["error"]["message"] == "Name must be 200 characters or fewer."
    assert "String should" not in out["error"]["message"]


def test_first_run_setup_shows_sentences_too():
    """auth.js posts setup/login itself; a 422 list used to print as
    "[object Object]"."""
    auth = (JS / "auth.js").read_text(encoding="utf-8")
    assert "API.errorMessage(data.detail, detail)" in auth


@pytest.mark.parametrize(
    "page, tag",
    [
        ("customers.js", '<input name="name" required maxlength="200"'),
        ("vendors.js", '<input name="name" required maxlength="200"'),
        ("items.js", '<input name="name" required maxlength="200"'),
    ],
)
def test_name_inputs_stop_at_the_schema_limit(page, tag):
    assert tag in (JS / page).read_text(encoding="utf-8")
