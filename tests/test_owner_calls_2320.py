"""The owner's calls on the 2.32.0 review list (2026-11-12).

A settings save that changes the company type answers with a sentence
saying an open window keeps its old words until it is started again (the
app does not redraw them live; the window that changes it reloads itself).
The PDF Paper Size setting says, in the page, that checks, mailing labels
and the IRS forms print on Letter whatever it says.
"""

from pathlib import Path

from app.routes.settings import COMPANY_TYPE_RESTART_NOTE

ROOT = Path(__file__).resolve().parent.parent


def _put(client, **fields):
    r = client.put("/api/settings", json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_company_type_change_answers_with_the_restart_note(client):
    got = _put(client, company_type="construction")
    assert got["restart_note"] == COMPANY_TYPE_RESTART_NOTE.format(kind="construction")
    assert "started again" in got["restart_note"]


def test_a_save_that_keeps_the_company_type_carries_no_note(client):
    _put(client, company_type="nonprofit")
    got = _put(client, company_type="nonprofit", invoice_footer="Thanks")
    assert "restart_note" not in got
    got = _put(client, invoice_footer="Thank you")
    assert "restart_note" not in got


def test_the_paper_size_setting_says_what_stays_on_letter():
    src = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    at = src.index('name="pdf_paper_size"')
    after = src[at : at + 900]
    assert (
        "Checks, mailing labels and the IRS forms print on Letter whatever this says"
        in after
    )
    assert 'class="form-hint"' in after
