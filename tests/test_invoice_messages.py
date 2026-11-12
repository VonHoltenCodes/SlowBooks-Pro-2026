"""Saved invoice messages (#392): a short list kept under Settings, one of
them the default, chosen on the invoice form and copied into its Notes."""

from pathlib import Path

from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
FIXTURE_PW = "keeper-password-1"


def _mk_user(db, username, role):
    u = User(
        username=username,
        display_name=username.title(),
        password_hash=auth_service.hash_password(FIXTURE_PW),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _login_as(client, username):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": FIXTURE_PW}
    )
    assert r.status_code == 200, r.text


def _make(client, name, body="Thank you for your business.", **extra):
    r = client.post("/api/invoice-messages", json={"name": name, "body": body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_list_update_delete(client):
    assert client.get("/api/invoice-messages").json() == []
    m = _make(client, "  Zelle  ", "To pay by Zelle use pay@example.com.")
    assert m["name"] == "Zelle"  # stripped
    assert m["is_default"] is False

    r = client.put(
        f"/api/invoice-messages/{m['id']}",
        json={"body": "To pay by Zelle use pay@example.com; by card, ask for a link."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["body"].endswith("ask for a link.")
    assert r.json()["name"] == "Zelle"  # untouched

    listed = client.get("/api/invoice-messages").json()
    assert [x["name"] for x in listed] == ["Zelle"]

    assert client.delete(f"/api/invoice-messages/{m['id']}").status_code == 200
    assert client.get("/api/invoice-messages").json() == []
    assert client.delete(f"/api/invoice-messages/{m['id']}").status_code == 404
    assert (
        client.put(f"/api/invoice-messages/{m['id']}", json={"name": "x"}).status_code
        == 404
    )


def test_blank_or_duplicate_names_are_refused(client):
    _make(client, "Thanks")
    r = client.post("/api/invoice-messages", json={"name": "Thanks", "body": "x"})
    assert r.status_code == 400
    other = _make(client, "Card")
    r = client.put(f"/api/invoice-messages/{other['id']}", json={"name": "thanks "})
    assert r.status_code == 200  # a different name: case counts
    r = client.put(f"/api/invoice-messages/{other['id']}", json={"name": "Thanks"})
    assert r.status_code == 400
    for bad in ({"name": "   ", "body": "x"}, {"name": "ok", "body": " "}):
        assert client.post("/api/invoice-messages", json=bad).status_code == 422
    # unknown fields are a 422, as everywhere
    r = client.post(
        "/api/invoice-messages", json={"name": "n", "body": "b", "text": "b"}
    )
    assert r.status_code == 422
    # a NOT NULL column may not be nulled
    r = client.put(f"/api/invoice-messages/{other['id']}", json={"body": None})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# One default at a time
# ---------------------------------------------------------------------------


def _defaults(client):
    return [
        m["name"] for m in client.get("/api/invoice-messages").json() if m["is_default"]
    ]


def test_one_default_at_a_time(client):
    a = _make(client, "A", is_default=True)
    b = _make(client, "B")
    assert _defaults(client) == ["A"]
    # creating a second default clears the first
    _make(client, "C", is_default=True)
    assert _defaults(client) == ["C"]
    # setting one by PUT clears the others, atomically
    r = client.put(f"/api/invoice-messages/{b['id']}", json={"is_default": True})
    assert r.status_code == 200 and r.json()["is_default"] is True
    assert _defaults(client) == ["B"]
    # the default is listed first
    assert client.get("/api/invoice-messages").json()[0]["name"] == "B"
    # an edit that leaves is_default out does not touch it
    client.put(f"/api/invoice-messages/{b['id']}", json={"body": "new text"})
    assert _defaults(client) == ["B"]
    # unsetting it leaves none
    client.put(f"/api/invoice-messages/{b['id']}", json={"is_default": False})
    assert _defaults(client) == []
    # deleting the default is allowed
    client.put(f"/api/invoice-messages/{a['id']}", json={"is_default": True})
    assert client.delete(f"/api/invoice-messages/{a['id']}").status_code == 200
    assert _defaults(client) == []


def _audit(client, record_id):
    rows = client.get(
        f"/api/audit?table_name=invoice_messages&record_id={record_id}&limit=50"
    ).json()
    if not isinstance(rows, list):
        rows = rows.get("items", rows.get("entries", []))
    return rows


def test_the_old_default_turning_off_is_audited(client):
    # #407 review: _clear_default flips each row through the ORM, not a bulk
    # Query.update, so the after_flush hook logs the old default going false.
    a = _make(client, "A", is_default=True)
    b = _make(client, "B")
    # setting B as default must clear A — and that change is in A's audit log
    client.put(f"/api/invoice-messages/{b['id']}", json={"is_default": True})
    cleared = [
        r
        for r in _audit(client, a["id"])
        if r.get("action", "").lower() == "update"
        and "is_default" in (r.get("changed_fields") or [])
    ]
    assert cleared, [
        (r.get("action"), r.get("changed_fields")) for r in _audit(client, a["id"])
    ]
    assert cleared[0]["old_values"]["is_default"] in (True, 1)
    assert cleared[0]["new_values"]["is_default"] in (False, 0)


def test_default_message_body_is_deterministic_with_two_defaults(client, db_session):
    # #407 review: default_message_body has an ORDER BY now, so if a row
    # somehow carries two defaults (the one-default rule is app-level) the
    # lowest id wins rather than whatever the DB hands back.
    from app.models.invoice_messages import InvoiceMessage
    from app.routes.invoice_messages import default_message_body

    db_session.add(InvoiceMessage(name="First", body="first body", is_default=True))
    db_session.add(InvoiceMessage(name="Second", body="second body", is_default=True))
    db_session.commit()
    assert default_message_body(db_session) == "first body"


# ---------------------------------------------------------------------------
# Who may write
# ---------------------------------------------------------------------------


def test_non_admin_reads_but_cannot_write(client, db_session):
    m = _make(client, "Thanks")
    _mk_user(db_session, "keeper", ROLE_BOOKKEEPER)
    _login_as(client, "keeper")
    assert client.get("/api/invoice-messages").status_code == 200
    assert (
        client.post(
            "/api/invoice-messages", json={"name": "x", "body": "y"}
        ).status_code
        == 403
    )
    assert (
        client.put(f"/api/invoice-messages/{m['id']}", json={"body": "z"}).status_code
        == 403
    )
    assert client.delete(f"/api/invoice-messages/{m['id']}").status_code == 403
    _mk_user(db_session, "viewer", ROLE_READONLY)
    _login_as(client, "viewer")
    assert client.get("/api/invoice-messages").status_code == 200
    assert client.delete(f"/api/invoice-messages/{m['id']}").status_code == 403


# ---------------------------------------------------------------------------
# The invoice keeps the text, not the choice
# ---------------------------------------------------------------------------


def test_an_invoice_keeps_the_text_and_an_edited_message_leaves_it(
    client, seed_accounts, seed_customer
):
    m = _make(client, "Zelle", "Pay by Zelle.", is_default=True)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-22",
            "notes": m["body"],
            "lines": [
                {"description": "Work", "quantity": 1, "rate": 100, "amount": 100}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]
    client.put(f"/api/invoice-messages/{m['id']}", json={"body": "Pay by card."})
    assert client.get(f"/api/invoices/{inv_id}").json()["notes"] == "Pay by Zelle."


def test_the_pdf_and_email_print_notes_as_before():
    # nothing new to print: the chosen text lands in Notes, which both show
    for name in ("invoice_pdf.html", "invoice_email.html"):
        html = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert "{% if inv.notes %}" in html


def test_an_estimate_converted_without_notes_starts_with_the_default(
    client, seed_accounts, seed_customer
):
    _make(client, "Zelle", "Pay by Zelle.", is_default=True)
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-22",
            "lines": [
                {"description": "Work", "quantity": 1, "rate": 100, "amount": 100}
            ],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/api/estimates/{r.json()['id']}/convert")
    assert r.status_code in (200, 201), r.text
    assert r.json()["notes"] == "Pay by Zelle."


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


def test_the_invoice_form_offers_the_messages():
    js = (JS / "invoices.js").read_text(encoding="utf-8")
    form = js[js.index("async showForm(") : js.index("async save(e, id)")]
    assert "API.get('/invoice-messages')" in form
    assert 'id="inv-message-select"' in form
    assert '<option value="">(none)</option>' in form
    assert "InvoicesPage.messageChosen(this.value)" in form
    # the default message fills Notes on a new invoice; the setting otherwise
    assert (
        "defaultMessage ? defaultMessage.body : (settings.invoice_notes || '')" in form
    )
    # the invoice sends plain notes, never the choice
    save = js[js.index("async save(e, id)") :]
    assert "notes: form.notes.value || null" in save
    assert "message_id" not in save


def test_the_settings_page_has_the_section():
    js = (JS / "settings.js").read_text(encoding="utf-8")
    assert 'id="settings-h-invoice-messages"' in js
    assert 'id="invoice-message-list"' in js
    assert "SettingsPage.loadInvoiceMessages();" in js
    section = js[
        js.index("async loadInvoiceMessages()") : js.index(
            "async deleteInvoiceMessage("
        )
    ]
    # every write control is the administrator's
    assert section.count("data-admin data-write") >= 3
    # the Default radios are not the Settings form's fields: no name
    assert 'class="msg-default"' in section
    assert 'name="invoice_message' not in section
    assert "API.put(`/invoice-messages/${id}`, { is_default: true })" in js
    assert "API.del(`/invoice-messages/${id}`)" in js
    assert "API.post('/invoice-messages', data)" in js
