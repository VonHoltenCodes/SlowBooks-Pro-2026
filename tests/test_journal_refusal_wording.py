"""An unbalanced journal entry is refused in a sentence a person can act on.

Exploratory 2.17.3 (macbase1, S-g): the refusal read "Journal entry not
balanced: debits=5000, credits=4000" — a log line. The journal form's own
live wording ("Out of balance by $1,000.00") is the model: say what must be
true and by how much it is off, in money."""


def test_an_unbalanced_entry_says_by_how_much_in_dollars(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-09-10",
            "description": "owner puts cash in",
            "lines": [
                {
                    "account_id": seed_accounts["1000"].id,
                    "debit": "5000",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["3000"].id,
                    "debit": "0",
                    "credit": "4000",
                },
            ],
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail == (
        "Debits and credits must be equal: this entry is out of balance by $1,000.00"
    )
    assert "debits=" not in detail and "credits=" not in detail


def test_a_balanced_entry_still_posts(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-09-10",
            "description": "owner puts cash in",
            "lines": [
                {
                    "account_id": seed_accounts["1000"].id,
                    "debit": "1234.56",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["3000"].id,
                    "debit": "0",
                    "credit": "1234.56",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["total_debit"] == 1234.56
