"""A receipt on a register entry (#600): a check, deposit or card charge
typed straight into a register takes the same Attachments panel as a journal
entry, keyed by the entry's id, so it need not be re-entered as an Expense."""

import io
from pathlib import Path

from tests.test_register_edit_entry import _entry

ROOT = Path(__file__).resolve().parents[1]


def _pdf(name="receipt.pdf", data=b"%PDF-1.4 receipt"):
    return {"file": (name, io.BytesIO(data), "application/pdf")}


def test_a_receipt_attaches_to_a_register_entry_and_comes_back(client, seed_accounts):
    entry = _entry(client, seed_accounts)
    r = client.post(f"/api/attachments/bank_entry/{entry['id']}", files=_pdf())
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["entity_type"] == "bank_entry" and att["entity_id"] == entry["id"]
    listed = client.get(f"/api/attachments/bank_entry/{entry['id']}").json()
    assert [a["filename"] for a in listed] == ["receipt.pdf"]
    down = client.get(f"/api/attachments/download/{att['id']}")
    assert down.status_code == 200 and down.content == b"%PDF-1.4 receipt"
    assert client.delete(f"/api/attachments/{att['id']}").status_code == 200
    assert client.get(f"/api/attachments/bank_entry/{entry['id']}").json() == []
    assert client.get(f"/api/attachments/download/{att['id']}").status_code == 404


def test_an_id_that_is_not_a_register_entry_is_refused(client, seed_accounts):
    assert (
        client.post("/api/attachments/bank_entry/999999", files=_pdf()).status_code
        == 404
    )
    assert client.get("/api/attachments/bank_entry/999999").status_code == 404
    # a manual journal entry is a transaction, but not a register entry
    j = client.post(
        "/api/journal",
        json={
            "date": "2026-07-14",
            "description": "Owner paid personally",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "debit": "48", "credit": "0"},
                {"account_id": seed_accounts["3000"].id, "debit": "0", "credit": "48"},
            ],
        },
    ).json()
    assert (
        client.post(f"/api/attachments/bank_entry/{j['id']}", files=_pdf()).status_code
        == 404
    )
    assert client.get(f"/api/attachments/bank_entry/{j['id']}").status_code == 404


def test_a_voided_entry_keeps_its_receipt(client, seed_accounts):
    entry = _entry(client, seed_accounts)
    att = client.post(f"/api/attachments/bank_entry/{entry['id']}", files=_pdf()).json()
    assert client.post(f"/api/banking/entries/{entry['id']}/void").status_code == 200
    listed = client.get(f"/api/attachments/bank_entry/{entry['id']}").json()
    assert [a["id"] for a in listed] == [att["id"]]
    assert client.get(f"/api/attachments/download/{att['id']}").status_code == 200


def test_the_register_entry_dialog_has_the_attachments_panel():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "API.get(`/attachments/bank_entry/${id}`)" in js
    assert (
        "fetch(`/api/attachments/bank_entry/${id}`, { method: 'POST', body: formData })"
        in js
    )
    assert 'id="entry-attach-file" aria-label="File to attach"' in js
    # the Activity panel (#894) follows the attachments panel on the same line
    assert "${entry ? BankingPage._attachmentsHtml(entry.id) + " in js
    assert "BankingPage.loadAttachments(entry.id);" in js
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "'BankingPage.uploadAttachment', 'BankingPage.deleteAttachment'" in app_js
