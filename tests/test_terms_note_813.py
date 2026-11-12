"""A terms note quotes the list's name, never a raw tab (#813).

A bill saved before the list held Net 45, with its terms typed as
"net<TAB>45", got the note 'first saved before "net	45" was on the terms
list': the tab printed as it was typed. The note names the entry as the
list does ("Net 45"); terms that are on no list are quoted with each run
of whitespace one space."""

from tests.test_payment_terms_664 import _bill, _vendor


def test_the_note_names_the_list_entry(client, db_session, seed_accounts):
    from app.services.payment_terms import ensure_seeded

    vendor = _vendor(client)
    bill = _bill(client, vendor["id"], "net\t45", on="2026-09-15")
    ensure_seeded(db_session)
    db_session.commit()
    got = client.get(f"/api/bills/{bill['id']}").json()
    note = got["terms_note"]
    assert note.startswith(
        'This bill\'s terms were first saved before "Net 45" was on the terms list'
    ), note
    assert "\t" not in note


def test_terms_on_no_list_are_quoted_without_the_tab(client, seed_accounts):
    vendor = _vendor(client)
    bill = _bill(client, vendor["id"], "Due\tSoon", on="2026-09-15")
    note = client.get(f"/api/bills/{bill['id']}").json()["terms_note"]
    assert note.startswith('"Due Soon" isn\'t on the terms list'), note
    assert "\t" not in note
