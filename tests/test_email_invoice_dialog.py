"""The Email Invoice dialog's own payload was rejected (issue #140, mdornich).

`_EmailInvoiceRequest` is a StrictModel accepting `recipient` and `subject`.
The dialog in `app/static/js/invoices.js` has always also posted `message`,
so **every send from the interface failed validation with a 422** before
reaching any of the sending code. The Message box did not merely get
ignored; it broke the button it sat on.

No test caught it because every test called the endpoint with a payload the
endpoint accepted, rather than the payload the interface sends. That is the
same shape as 2.10.3's shadowed download route: a test of the handler is not
a test of what the page does.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def invoice(client):
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "a@b.com"}
    ).json()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_payload_the_dialog_actually_sends_is_accepted(
    client, seed_accounts, invoice
):
    """The claim is only this: the request clears validation.

    What happens *after* validation is host-dependent and is not what this
    test is about — SMTP is unconfigured everywhere, so a machine with the
    PDF stack answers 502 from the send, and one without it answers 500
    because the attachment cannot be rendered. Asserting 502 made this pass
    on Linux and fail on Windows CI, which is precisely the defect class
    that job exists to catch (@ContractorKeith found the same shape in
    2.10.2: a test that only passed where something was absent). Caught on
    the 2.11.2 gate, by the job, before anyone ran it.
    """
    r = client.post(
        f"/api/invoices/{invoice['id']}/email",
        json={
            "recipient": "a@b.com",
            "subject": f"Invoice #{invoice['invoice_number']} from us",
            "message": f"Please find attached Invoice #{invoice['invoice_number']}.",
        },
    )
    assert r.status_code != 422, (
        "the dialog's own payload is rejected before reaching the send: " f"{r.text}"
    )
    # It got past the request model and into the handler.
    assert r.status_code in (200, 500, 502), r.text


def test_an_unknown_field_is_still_refused():
    """The control, and it is @macbase1's — their gate harness had it and my
    test did not.

    "The dialog's payload is accepted" is equally true of a fix that named
    `message` and of one that simply deleted the model's strictness. Only
    this tells them apart, and the second would be a far wider change than
    #140 asked for: `_EmailInvoiceRequest` is a StrictModel on purpose, so
    that a typo in the page is a loud 422 rather than a field silently
    dropped on the floor.

    It lives here as well as in the harness because CI runs this suite on
    every push and does not run the harness.
    """
    from app.routes.invoices.documents import _EmailInvoiceRequest
    import pydantic

    with pytest.raises(pydantic.ValidationError) as e:
        _EmailInvoiceRequest(
            recipient="a@b.com", subject="s", message="m", nonsense="x"
        )
    assert "extra_forbidden" in str(e.value)

    # And the four it does take still validate together.
    ok = _EmailInvoiceRequest(recipient="a@b.com", subject="s", message="m")
    assert ok.message == "m"


def test_the_dialog_and_the_route_agree_on_their_fields():
    """The tripwire. These two drifted apart and nothing noticed, because
    they are in different languages in different files."""
    js = (ROOT / "app/static/js/invoices.js").read_text(encoding="utf-8")
    py = (ROOT / "app/routes/invoices/documents.py").read_text(encoding="utf-8")

    body = re.search(r"API\.post\(`/invoices/\$\{id\}/email`,\s*\{(.+?)\}\)", js, re.S)
    assert body, "could not find the dialog's email POST"
    sent = set(re.findall(r"(\w+):", body.group(1)))

    model = re.search(r"class _EmailInvoiceRequest\(StrictModel\):(.+?)\n\n", py, re.S)
    assert model, "could not find _EmailInvoiceRequest"
    accepted = set(re.findall(r"^\s{4}(\w+):", model.group(1), re.M))

    assert sent <= accepted, (
        f"the dialog posts {sorted(sent - accepted)} which the route rejects; "
        f"_EmailInvoiceRequest is a StrictModel so this is a 422, not an ignore"
    )

    # The subset relation above only catches the direction that broke us: the
    # page posting a field the route refuses. It says nothing about the page
    # quietly *dropping* a field the route supports, which is the likelier
    # mistake now that the box works — a branch that deleted the Message
    # textarea outright passed everything above this line. Name the field.
    assert "message" in sent, (
        "the Email Invoice dialog no longer posts `message`; the Message box "
        "is part of the contract now (#140) and an operator's text must reach "
        "the email rather than being dropped on the page"
    )
    assert 'name="message"' in js, (
        "the Message textarea is gone from the dialog; `message` is still in "
        "the POST body, so every send would now throw on form.message.value"
    )
    assert "message" in accepted, (
        "_EmailInvoiceRequest no longer accepts `message`; the dialog posts it "
        "and this is a StrictModel, so every send from the interface is a 422"
    )


def test_the_operators_message_reaches_the_email_body(
    client, db_session, seed_accounts, invoice
):
    """The box says Message. It should be the message."""
    from app.models.invoices import Invoice
    from app.services.email_service import render_invoice_email
    from app.services.settings_service import get_all_settings as get_settings

    inv = db_session.query(Invoice).filter(Invoice.id == invoice["id"]).first()
    body = render_invoice_email(
        inv, get_settings(db_session), note="Ten days, as agreed."
    )
    assert "Ten days, as agreed." in body


def test_an_operator_message_is_escaped(client, db_session, seed_accounts, invoice):
    """It is operator-supplied text landing in an HTML email."""
    from app.models.invoices import Invoice
    from app.services.email_service import render_invoice_email
    from app.services.settings_service import get_all_settings as get_settings

    inv = db_session.query(Invoice).filter(Invoice.id == invoice["id"]).first()
    body = render_invoice_email(
        inv, get_settings(db_session), note="<script>x</script>"
    )
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
