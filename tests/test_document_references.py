"""What a posting writes for itself, in the company's words — and the guard
that says none of it is ever read back.

The vocabulary audit's walk found fourteen sites composing "Invoice #1081 -
Boise Neon Supply" into stored ledger descriptions, an inventory memo and
the Stripe, Square and PayPal line items a payer sees. That is data by the
time it is displayed, so no render-time swap can reach it. The words are
chosen once, at posting time, through one helper; history keeps the words
in use when it was posted; and the integrations key on ids and metadata,
never on the text — which these tests pin, because that is the promise
that lets the words change at all.
"""

import re
from pathlib import Path

from tests.test_payment_providers import SETTINGS as PROVIDER_SETTINGS
from tests.test_square_provider import SETTINGS as SQUARE_SETTINGS
from tests.test_square_provider import _invoice as square_invoice

ROOT = Path(__file__).resolve().parents[1]


def _customer(client, name="Boise Neon Supply"):
    r = client.post("/api/customers", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _invoice(client, cid, amount=250, date="2026-03-01", pledge=False):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": date,
            "tax_rate": 0,
            "is_pledge": pledge,
            "lines": [
                {"description": "x", "quantity": 1, "rate": amount, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ledger_rows(client, invoice_number):
    gl = client.get(
        "/api/reports/general-ledger?start_date=2020-01-01&end_date=2030-12-31"
    ).json()
    text = str(gl)
    return [
        m
        for m in re.findall(r"'description': '([^']*)'", text)
        if str(invoice_number) in m
    ]


def test_business_postings_keep_their_words(client, seed_accounts):
    inv = _invoice(client, _customer(client))
    rows = _ledger_rows(client, inv["invoice_number"])
    assert rows and all(
        r == f"Invoice #{inv['invoice_number']} - Boise Neon Supply" for r in rows
    ), rows


def test_a_nonprofit_posts_a_pledge_as_a_pledge_and_an_invoice_as_an_invoice(
    client, seed_accounts
):
    """The document decides, not the company: a flagged pledge posts as
    Pledge; a program-fee invoice in the same nonprofit stays Invoice —
    which is what its printed page says."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    cid = _customer(client, "Grant Foundation")
    pledge = _invoice(client, cid, pledge=True)
    fee = _invoice(client, cid, pledge=False)
    assert all(
        r == f"Pledge #{pledge['invoice_number']} - Grant Foundation"
        for r in _ledger_rows(client, pledge["invoice_number"])
    )
    assert all(
        r == f"Invoice #{fee['invoice_number']} - Grant Foundation"
        for r in _ledger_rows(client, fee["invoice_number"])
    )
    # and the three surfaces agree, per document
    for inv, face in ((pledge, "Pledge"), (fee, "Invoice")):
        page = client.get(f"/api/invoices/{inv['id']}/print-preview").text
        assert f"<title>{face}</title>" in page, (face, page[:200])
        mail = client.post(f"/api/invoices/{inv['id']}/email-preview", json={}).json()
        assert mail["subject"].startswith(f"{face} #{inv['invoice_number']}"), mail


def test_editing_keeps_the_document_face(client, seed_accounts):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    inv = _invoice(client, _customer(client, "Grant Foundation"), pledge=True)
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [{"description": "y", "quantity": 2, "rate": 100, "line_order": 0}]
        },
    )
    assert r.status_code == 200, r.text
    rows = _ledger_rows(client, inv["invoice_number"])
    assert rows and all("Pledge #" in row for row in rows), rows


def test_history_keeps_the_words_it_was_posted_with(client, seed_accounts):
    """Nothing rewrites the ledger. A pledge posted as a pledge keeps
    "Pledge #" after the company flips back to business (its page then
    prints Invoice — the ledger is history, the page is now)."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    old = _invoice(
        client, _customer(client, "Grant Foundation"), date="2026-01-10", pledge=True
    )
    client.put("/api/settings", json={"company_type": "business"})
    new = _invoice(client, _customer(client), date="2026-02-10")
    assert any(
        r.startswith("Pledge #") for r in _ledger_rows(client, old["invoice_number"])
    )
    assert any(
        r.startswith("Invoice #") for r in _ledger_rows(client, new["invoice_number"])
    )


def test_estimate_conversion_and_reissue_use_the_same_helper():
    """Every site goes through document_reference or text() — a grep, so a
    new site written as f"Invoice #{n}" fails here before it reaches a
    nonprofit. The first version only matched the word at the START of the
    f-string; check 0 on the 2.13.1 artifact found "VOID Invoice #" and
    "Late fee - Invoice #" waiting behind it, then six more of that shape."""
    offenders = []
    for path in list((ROOT / "app/routes").rglob("*.py")) + list(
        (ROOT / "app/services").rglob("*.py")
    ):
        if (
            "iif_import" in path.name
            or "checks.py" in path.name
            or "check_memo.py" in path.name
        ):
            # QuickBooks interop keeps QuickBooks' words; a vendor's invoice is
            # an invoice, on a check and on its memo line (#800)
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            # black may put the wrapper on the line above the f-string
            window = (lines[n - 2] if n >= 2 else "") + line
            if (
                re.search(r'f"[^"]*\b(Invoice|Sales Receipt) #?\{', line)
                and "HTTPException" not in window
                and "detail=" not in window
                and "document_label(" not in window
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    assert offenders == [], "\n".join(offenders)


# ---------------------------------------------------------------------------
# The guard: integrations key on ids and metadata, never on the words
# ---------------------------------------------------------------------------

NONPROFIT_SETTINGS = {**PROVIDER_SETTINGS, "company_type": "nonprofit"}


def test_square_link_says_pledge_and_keeps_every_key():
    from app.services.payments import square as sq

    biz = sq.build_payment_link_request(
        square_invoice(), SQUARE_SETTINGS, "https://b.example", "idem"
    )
    pledge = square_invoice()
    pledge.is_pledge = True
    npo = sq.build_payment_link_request(
        pledge,
        {**SQUARE_SETTINGS, "company_type": "nonprofit"},
        "https://b.example",
        "idem",
    )
    fee = sq.build_payment_link_request(
        square_invoice(),
        {**SQUARE_SETTINGS, "company_type": "nonprofit"},
        "https://b.example",
        "idem",
    )
    assert (
        fee["json"]["quick_pay"]["name"] == "Invoice #INV-1042"
    )  # unflagged stays an invoice
    assert biz["json"]["quick_pay"]["name"] == "Invoice #INV-1042"
    assert npo["json"]["quick_pay"]["name"] == "Pledge #INV-1042"
    assert npo["json"]["payment_note"] == "Pledge #INV-1042"
    # everything the webhook and the order lookup rely on is identical
    for k in ("idempotency_key", "checkout_options"):
        assert biz["json"][k] == npo["json"][k], k
    assert (
        biz["json"]["quick_pay"]["price_money"]
        == npo["json"]["quick_pay"]["price_money"]
    )
    assert (
        biz["json"]["quick_pay"]["location_id"]
        == npo["json"]["quick_pay"]["location_id"]
    )
    assert biz["url"] == npo["url"] and biz["headers"] == npo["headers"]


def test_paypal_order_says_pledge_and_keeps_its_ids():
    from app.services.payments import paypal as pp

    biz = pp.build_order_request(
        square_invoice(), PROVIDER_SETTINGS, "https://b.example", "TOK"
    )
    pledge = square_invoice()
    pledge.is_pledge = True
    npo = pp.build_order_request(pledge, NONPROFIT_SETTINGS, "https://b.example", "TOK")
    b, n = biz["json"]["purchase_units"][0], npo["json"]["purchase_units"][0]
    assert (
        b["description"] == "Invoice #INV-1042"
        and n["description"] == "Pledge #INV-1042"
    )
    # PayPal's own `invoice_id` field is a key, not a sentence; custom_id ties the order back
    assert b["custom_id"] == n["custom_id"] == "42"
    assert b["invoice_id"] == n["invoice_id"] == "INV-1042"
    assert b["amount"] == n["amount"]
    assert biz["json"]["application_context"] == npo["json"]["application_context"]


def test_stripe_session_says_pledge_and_keeps_its_metadata(monkeypatch):
    from app.services.payments import stripe as st

    captured = {}

    class _Session:
        @staticmethod
        def create(**kw):
            captured.update(kw)

            class _S:
                id = "cs_test"
                url = "https://checkout.stripe.test/cs_test"

            return _S()

    monkeypatch.setattr(st.stripe.checkout, "Session", _Session)
    settings = {**NONPROFIT_SETTINGS, "stripe_secret_key": "sk_test"}
    pledge = square_invoice()
    pledge.is_pledge = True
    st.StripeProvider().create_checkout(pledge, settings, "https://b.example")
    product = captured["line_items"][0]["price_data"]["product_data"]
    assert product["name"] == "Pledge #INV-1042"
    assert product["description"] == "Payment for pledge #INV-1042"
    assert captured["metadata"] == {"invoice_id": "42", "payment_token": "tok-abc"}


def test_nothing_parses_a_description_for_the_document_words():
    """The contract that lets the words change: descriptions, memos and
    provider line items are display text. If any code ever keys on
    "Invoice #" in one, this names it."""
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if "state_tax" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(
            r"(description|memo|payment_note)[^\n]*(startswith|\.match\(|\.search\(|\bin\b)[^\n]*(Invoice|Pledge|Sales Receipt)",
            text,
        ):
            offenders.append(f"{path.relative_to(ROOT)}: {m.group(0)[:100]}")
        for m in re.finditer(r"[\"'](Invoice|Pledge) #[\"'] in ", text):
            offenders.append(f"{path.relative_to(ROOT)}: {m.group(0)[:100]}")
    assert offenders == [], "\n".join(offenders)


def test_email_defaults_name_the_document_through_doc_label(client, seed_accounts):
    """The seeded templates say {{ doc_label }}, so one saved template reads
    Invoice for an invoice and Pledge for a pledge — and the Jinja
    expression stays invoice.invoice_number (the first cut swapped inside it)."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    r = client.post("/api/email-templates/seed-defaults")
    assert r.status_code == 200, r.text
    tpl = next(
        t
        for t in client.get("/api/email-templates").json()
        if t["name"] == "invoice_email"
    )
    assert tpl["subject_template"].startswith(
        "{{ doc_label }} #{{ invoice.invoice_number }}"
    )
    assert (
        "attached {{ doc_label }} #{{ invoice.invoice_number }}" in tpl["body_template"]
    )
    assert "pledge.invoice_number" not in tpl["subject_template"] + tpl["body_template"]
    cid = _customer(client, "Grant Foundation")
    pledge = _invoice(client, cid, pledge=True)
    fee = _invoice(client, cid, pledge=False)
    assert (
        client.post(f"/api/invoices/{pledge['id']}/email-preview", json={})
        .json()["subject"]
        .startswith("Pledge #")
    )
    assert (
        client.post(f"/api/invoices/{fee['id']}/email-preview", json={})
        .json()["subject"]
        .startswith("Invoice #")
    )


def test_void_and_late_fee_postings_use_the_document_face(client, seed_accounts):
    """Found by check 0 on the shipped 2.13.1 artifact, not by the grep:
    the void reversal and the late-fee posting compose the word in the
    middle of a sentence."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    cid = _customer(client, "Grant Foundation")
    inv = _invoice(client, cid, date="2025-01-05", pledge=True)
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text
    gl = str(
        client.get(
            "/api/reports/general-ledger?start_date=2024-01-01&end_date=2030-12-31"
        ).json()
    )
    assert f"VOID Pledge #{inv['invoice_number']}" in gl
    assert "VOID Invoice" not in gl

    overdue = _invoice(client, cid, date="2025-01-05", pledge=True)
    assert (
        client.post(f"/api/invoices/{overdue['id']}/send").status_code == 200
    )  # drafts are never charged
    r = client.put(
        "/api/settings",
        json={
            "late_fee_enabled": "true",
            "late_fee_rate": "1.5",
            "late_fee_grace_days": "0",
        },
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/invoices/apply-late-fees")
    assert r.status_code == 200, r.text
    gl = str(
        client.get(
            "/api/reports/general-ledger?start_date=2024-01-01&end_date=2030-12-31"
        ).json()
    )
    assert f"Late fee - Pledge #{overdue['invoice_number']}" in gl, gl[-600:]
    assert "Late fee - Invoice" not in gl
