"""A receipt on a journal entry (issue #281).

A manual journal entry is sometimes the only right record of a real
transaction — an owner paying a business expense personally, credited to
equity, which an Expense cannot be (it is paid from an asset or liability)
— and its receipt had nowhere to go: /api/attachments whitelisted eight
record types and not journal_entry.
"""

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _entry(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-07-14",
            "description": "Owner paid the hosting bill personally",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": "48.00",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["3000"].id,
                    "debit": "0",
                    "credit": "48.00",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_receipt_attaches_to_a_journal_entry_and_comes_back(client, seed_accounts):
    entry = _entry(client, seed_accounts)
    r = client.post(
        f"/api/attachments/journal_entry/{entry['id']}",
        files={
            "file": (
                "hosting-receipt.pdf",
                io.BytesIO(b"%PDF-1.4 receipt"),
                "application/pdf",
            )
        },
    )
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["entity_type"] == "journal_entry" and att["entity_id"] == entry["id"]
    listed = client.get(f"/api/attachments/journal_entry/{entry['id']}").json()
    assert [a["filename"] for a in listed] == ["hosting-receipt.pdf"]
    down = client.get(f"/api/attachments/download/{att['id']}")
    assert down.status_code == 200 and down.content == b"%PDF-1.4 receipt"
    assert client.delete(f"/api/attachments/{att['id']}").status_code in (200, 204)
    assert client.get(f"/api/attachments/journal_entry/{entry['id']}").json() == []


def test_the_whitelist_names_it_and_still_refuses_the_rest(client, seed_accounts):
    r = client.post(
        "/api/attachments/ledger/1",
        files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
    )
    assert r.status_code == 400 and "journal_entry" in r.json()["detail"]


def test_the_journal_entry_view_has_the_attachments_block():
    js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert "API.get(`/attachments/journal_entry/${id}`)" in js
    assert (
        "fetch(`/api/attachments/journal_entry/${id}`, { method: 'POST', body: formData })"
        in js
    )
    assert 'id="journal-attach-file" aria-label="File to attach"' in js
    assert "JournalPage.loadAttachments(entry.id);" in js


def test_a_read_only_sign_in_is_not_offered_attach_or_delete():
    # Attach and an attachment's X call these, so a read-only sign-in,
    # whom the server refuses, is not shown them (App.hideWriteControls);
    # the file chooser is hidden as every file chooser is.
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "'JournalPage.uploadAttachment', 'JournalPage.deleteAttachment'" in app_js


def test_a_journal_entrys_id_is_not_an_expenses(client, seed_accounts):
    """An expense's file goes on an expense: a journal entry's id is refused
    as one, and taken as the journal entry's."""
    entry = _entry(client, seed_accounts)
    file = {"file": ("r.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    r = client.post(f"/api/attachments/expense/{entry['id']}", files=file)
    assert (r.status_code, r.json()["detail"]) == (404, "Expense not found")
    file = {"file": ("r.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    r = client.post(f"/api/attachments/journal_entry/{entry['id']}", files=file)
    assert r.status_code == 201, r.text
