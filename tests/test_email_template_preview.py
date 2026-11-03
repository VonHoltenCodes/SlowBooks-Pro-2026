"""Previewing a template edit before saving it.

#140 gave the send dialog a preview of the mail it is about to send. This is
the other half: the operator editing `invoice_email` under Settings -> Email
Templates could only see the result by saving over a working template and
mailing a real customer. The preview renders what is currently typed, against
a real invoice, without writing or sending anything.

It deliberately renders through `email_service.template_env()` — the same
environment the send uses — because a preview under different rules than the
mail is how the template and the send drifted apart in the first place.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.email_log import EmailLog
from app.models.email_templates import EmailTemplate
from app.models.invoices import Invoice, InvoiceStatus
from app.models.transactions import Transaction


@pytest.fixture
def invoice(db_session, seed_customer):
    seed_customer.email = "client@example.com"
    inv = Invoice(
        invoice_number="PREV-1",
        customer_id=seed_customer.id,
        date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        subtotal=Decimal("125"),
        total=Decimal("125"),
        balance_due=Decimal("125"),
        status=InvoiceStatus.SENT,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _preview(client, invoice_id, subject="S", body="<p>B</p>"):
    return client.post(
        "/api/email-templates/preview",
        json={
            "invoice_id": invoice_id,
            "subject_template": subject,
            "body_template": body,
        },
    )


def test_preview_renders_unsaved_text_and_saves_nothing(client, db_session, invoice):
    before = (
        db_session.query(Transaction).count(),
        db_session.query(EmailLog).count(),
        db_session.query(EmailTemplate).count(),
    )
    r = _preview(
        client,
        invoice.id,
        subject="Draft {{ invoice.invoice_number }}",
        body="<p>{{ customer_name }} owes {{ invoice.total|currency }}</p>",
    )
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == "Draft PREV-1"
    assert "$125.00" in r.json()["html_body"]
    assert before == (
        db_session.query(Transaction).count(),
        db_session.query(EmailLog).count(),
        db_session.query(EmailTemplate).count(),
    )


def test_preview_does_not_touch_the_saved_template(client, db_session, invoice):
    db_session.add(
        EmailTemplate(
            name="invoice_email",
            template_type="invoice",
            subject_template="Saved subject",
            body_template="<p>Saved body</p>",
        )
    )
    db_session.commit()

    assert (
        _preview(client, invoice.id, subject="Unsaved").json()["subject"] == "Unsaved"
    )
    saved = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()
    assert saved.subject_template == "Saved subject"


def test_preview_subject_is_plain_text_and_the_body_is_escaped(
    client, db_session, invoice
):
    """The subject goes into a mail header, so an apostrophe or an ampersand
    reaches it as typed; the body is HTML and keeps the sandbox's escaping.
    The preview and the send render through the same function."""
    invoice.customer.name = "Pike's Plumbing & Heating"
    db_session.commit()
    r = _preview(
        client,
        invoice.id,
        subject="For {{ customer_name }}",
        body="<p>{{ customer_name }}</p>",
    ).json()
    assert r["subject"] == "For Pike's Plumbing & Heating"
    assert "Pike&#39;s Plumbing &amp; Heating" in r["html_body"]


def test_preview_escapes_customer_supplied_text(client, db_session, invoice):
    invoice.customer.name = "<img src=x onerror=alert(1)>"
    db_session.commit()
    body = _preview(client, invoice.id, body="<p>{{ customer_name }}</p>").json()[
        "html_body"
    ]
    assert "<img" not in body
    assert "&lt;img" in body


def test_preview_cannot_read_settings_secrets(client, db_session, invoice):
    """GHSA-c3v4-f43f-4wqm: the context is redacted at the point it is built,
    and this endpoint must be no exception."""
    from app.services.settings_service import set_setting

    set_setting(db_session, "smtp_password", "hunter2-SECRET")
    db_session.commit()

    body = _preview(
        client, invoice.id, body="<p>{{ company.smtp_password }}{{ company }}</p>"
    ).json()["html_body"]
    assert "hunter2-SECRET" not in body
    assert "********" in body


def test_preview_rejects_a_sandbox_escape(client, db_session, invoice):
    r = _preview(client, invoice.id, body="{{ invoice.__class__.__mro__ }}")
    assert r.status_code == 400


def test_preview_rejects_bad_input_as_400_not_500(client, db_session, invoice):
    """Syntax errors, unknown filters and arithmetic blowups are all just bad
    text from the client."""
    for bad in ("{% if %}", "{{ 1/0 }}", "{{ x | frobnicate }}"):
        r = _preview(client, invoice.id, body=bad)
        assert r.status_code == 400, f"{bad!r} gave {r.status_code}"


def test_preview_404s_for_an_unknown_invoice(client, db_session):
    assert _preview(client, 999999).status_code == 404


# ── A blank is the one outcome that explains nothing ─────────────────────


def test_the_preview_names_what_rendered_as_nothing(client, db_session, invoice):
    """Raised by @macbase1 on the 2.12.1 gate.

    `{{ config }}`, `{{ request }}` and anything the sandbox refuses all
    resolve to undefined and render as **empty**, with a 200. The author sees
    a working template with a hole in it and no reason for the hole — which
    is the same class as an error naming a command nobody can run, a mistake
    this product has now shipped four times.

    The body stays byte-identical to what would be sent; it would not be a
    preview otherwise. What changes is that the names are reported beside it.
    """
    r = _preview(client, invoice.id, body="<p>{{ config }}{{ request }}</p>")
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["resolved_to_nothing"]) >= {"config", "request"}


def test_a_template_that_renders_reports_nothing_missing(client, db_session, invoice):
    """The control. Without it, a list that is always full passes the test
    above for the wrong reason."""
    r = _preview(
        client,
        invoice.id,
        body="<p>{{ invoice.invoice_number }} {{ customer_name }}</p>",
    )
    assert r.status_code == 200, r.text
    assert r.json()["resolved_to_nothing"] == []


def test_the_body_is_unchanged_by_the_reporting(client, db_session, invoice):
    """The preview must render under the same rules as the mail. Recording
    what vanished must not make it render differently."""
    r = _preview(client, invoice.id, body="<p>before{{ config }}after</p>")
    assert r.status_code == 200
    assert "<p>beforeafter</p>" in r.json()["html_body"]


def test_the_editor_shows_it(client):
    """Server-side only would leave the author looking at the same hole."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    # The classified fields, since the banner now tells the two kinds of
    # blank apart rather than calling both "not available".
    assert "p.unavailable" in js
    assert "p.conditional" in js
    assert "Not available to an email template" in js
    assert "is not set for this" in js


def test_two_renders_at_once_do_not_see_each_others_blanks():
    """@skytech reproduced cross-request contamination in the first version:
    the recorder appended to a **class attribute**, and `preview_template` is
    a sync `def`, so FastAPI runs it in a threadpool. With sixteen concurrent
    previews, three returned another request's variable names and two
    returned none of their own. Invisible on a desktop; **Server Edition
    serves a LAN**, and two bookkeepers previewing at once got each other's.

    Driven at the renderer rather than through the test client on purpose.
    The client fixture shares one in-memory SQLite connection via StaticPool,
    so eight threads through it break inside SQLAlchemy's result handling —
    a failure about the harness, not the product. An earlier version of this
    test did exactly that: it passed alone and failed in the full suite,
    which is a flaky test, which is worse than no test.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.services.email_service import recording_template_env

    def render(tag):
        env, seen = recording_template_env()
        names = [f"zz_{tag}_{i}" for i in range(40)]
        body = "".join("{{ %s }}" % n for n in names)
        env.from_string(body).render()
        return tag, list(seen)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(render, range(16)))

    for tag, seen in results:
        assert seen, f"render {tag} reported nothing of its own"
        foreign = [n for n in seen if not n.startswith(f"zz_{tag}_")]
        assert not foreign, f"render {tag} saw another's names: {foreign[:3]}"
        assert len(seen) == 40, f"render {tag} reported {len(seen)} of its 40"


def test_a_guard_is_not_reported_as_a_hole(client, db_session, invoice):
    """`{% if pay_url %}` asks whether something is there. That is a guard
    doing its job, not a blank an author needs explaining — and the shipped
    default template uses exactly that, so reporting it would raise a false
    alarm on every preview and teach operators to ignore the line."""
    r = _preview(client, invoice.id, body="{% if pay_url %}<p>Pay</p>{% endif %}")
    assert r.status_code == 200, r.text
    assert r.json()["resolved_to_nothing"] == []


def test_pay_url_does_not_render_the_word_None(client, db_session, invoice):
    """It used to be passed as `None` when no provider is enabled, so a bare
    `{{ pay_url }}` mailed customers the literal text "None" — and
    `resolved_to_nothing` could not flag it, because None is a real value.
    Omitted from the context now: renders empty, and is reported."""
    r = _preview(client, invoice.id, body="<p>Pay: {{ pay_url }}</p>")
    assert r.status_code == 200, r.text
    out = r.json()
    assert "None" not in out["html_body"], out["html_body"]
    assert "pay_url" in out["resolved_to_nothing"]
    # And it is reported as CONDITIONAL, not as unavailable — the editor's
    # own variable list says pay_url is available, so calling it unavailable
    # would have the product contradicting itself two inches apart.
    assert [c["name"] for c in out["conditional"]] == ["pay_url"]
    assert "pay_url" not in out["unavailable"]
    assert "payment provider" in out["conditional"][0]["why"]


def test_the_editor_hint_only_advertises_variables_that_exist(
    client, db_session, invoice
):
    """@skytech found `{{ amount }}` in the editor's own hint line and not in
    the context, so an operator following the product's hint was told by the
    product that the hint was wrong. Every name the hint offers is rendered
    here and must not come back as a blank."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    hint = js[js.index("Variables:") : js.index("Filters:")]
    names = re.findall(r"\{\{\s*([A-Za-z_][\w.]*)", hint)
    assert names, "could not read the hint line"

    body = "".join("<p>%s = {{ %s }}</p>" % (n, n) for n in names)
    r = _preview(client, invoice.id, body=body)
    assert r.status_code == 200, r.text
    blank = [n for n in r.json()["resolved_to_nothing"]]
    # pay_url is legitimately absent without a payment provider, and the hint
    # now says so in the same breath.
    unexpected = [n for n in blank if n != "pay_url"]
    assert (
        not unexpected
    ), f"the editor advertises {unexpected}, which render as nothing"


def test_a_name_no_template_can_use_is_reported_as_unavailable(
    client, db_session, invoice
):
    """The other side of the split. `config` genuinely cannot work, and
    saying so is correct — the defect was applying that sentence to a
    variable the editor advertises."""
    r = _preview(client, invoice.id, body="<p>{{ config }}</p>")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["unavailable"] == ["config"]
    assert out["conditional"] == []


def test_the_banner_does_not_contradict_the_editors_variable_list(
    client, db_session, invoice
):
    """@skytech, 2.12.1 gate. Every name the editor advertises must be
    reported as conditional or not reported at all — never as "not available
    to an email template", which is what the banner used to say about
    `pay_url` while the hint line above called it available.

    Read out of the editor rather than hardcoded, so correcting one and not
    the other fails here instead of shipping."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    hint = js[js.index("Variables:") : js.index("Filters:")]
    advertised = {
        n.split(".")[0] for n in re.findall(r"\{\{\s*([A-Za-z_][\w.]*)", hint)
    }

    body = "".join("<p>{{ %s }}</p>" % n for n in sorted(advertised))
    out = _preview(client, invoice.id, body=body).json()
    wrongly_unavailable = [n for n in out["unavailable"] if n in advertised]
    assert not wrongly_unavailable, (
        f"the editor advertises {wrongly_unavailable} and the preview calls "
        f"them unavailable; the product is contradicting itself"
    )
