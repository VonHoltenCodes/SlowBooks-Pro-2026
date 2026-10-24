"""A bank entry's journal link says what it opens.

A bank entry posted from the register or the bank feed showed in the
register with its memo linking to #/journal/{id}, so it read as a journal
entry — the kind that cannot be edited — when Edit on the same row is where
it changes. The register row now links a Posting beside the memo (the page
side is tests/test_register_posting_link_browser.py), and the journal
page's entry names the register it was posted from, so it can say "edit it
there" and link back: `register_account_id` / `register_account_name` on
GET /api/journal/{id}, empty for every other kind of entry.
"""


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _bank_entry(client, accounts, **over):
    body = {
        "account_id": accounts["1000"].id,
        "date": "2026-05-04",
        "amount": "-48.20",
        "category_account_id": accounts["6400"].id,
        "payee": "Office Depot",
        "description": "Toner",
    }
    body.update(over)
    return _ok(client.post("/api/banking/transactions", json=body))["id"]


def test_a_bank_entrys_journal_names_the_register_it_was_posted_from(
    client, seed_accounts
):
    txn = _bank_entry(client, seed_accounts)
    entry = _ok(client.get(f"/api/journal/{txn}"))
    assert entry["source_type"] == "bank_entry"
    assert entry["register_account_id"] == seed_accounts["1000"].id
    assert entry["register_account_name"] == "Checking"
    # the register row still opens the posting, and is editable there
    reg = _ok(
        client.get(
            "/api/banking/check-register",
            params={"account_id": seed_accounts["1000"].id},
        )
    )
    (row,) = [e for e in reg["entries"] if e["transaction_id"] == txn]
    assert row["source_link"] == f"/#/journal/{txn}" and row["editable"] is True


def test_a_card_entry_names_its_card_account(client, seed_accounts):
    """The register is the line on the account with a bank kind, a card's as
    much as a bank's."""
    card = next(a for a in seed_accounts.values() if a.bank_kind == "credit_card")
    txn = _bank_entry(client, seed_accounts, account_id=card.id)
    entry = _ok(client.get(f"/api/journal/{txn}"))
    assert (entry["register_account_id"], entry["register_account_name"]) == (
        card.id,
        card.name,
    )


def test_other_entries_name_no_register(client, seed_accounts):
    """A hand-typed journal entry that touches the bank account is not a
    bank entry: it is edited nowhere else, and the note stays off it."""
    manual = _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-05-05",
                "description": "Bank charge, typed",
                "lines": [
                    {
                        "account_id": seed_accounts["6900"].id,
                        "debit": "12",
                        "credit": "0",
                    },
                    {
                        "account_id": seed_accounts["1000"].id,
                        "debit": "0",
                        "credit": "12",
                    },
                ],
            },
        )
    )
    entry = _ok(client.get(f"/api/journal/{manual['id']}"))
    assert entry["register_account_id"] is None
    assert entry["register_account_name"] == ""
    # the list keeps its shape, the new fields at their defaults
    listed = _ok(client.get("/api/journal"))
    assert all(
        e["register_account_id"] is None and e["register_account_name"] == ""
        for e in listed
    )
