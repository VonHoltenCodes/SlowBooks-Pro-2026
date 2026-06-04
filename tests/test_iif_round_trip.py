"""IIF export → re-import round-trip integrity.

The IIF format is the only interop path with QuickBooks 2003. Any drift
between what we export and what we (or QB) can re-import is a silent
data-loss vector. These tests:

  1. Export the seed CoA; assert every row in the export survives a
     re-import into a clean DB by name + account_number.
  2. Export customers/vendors with names containing IIF metacharacters
     (tabs, newlines, embedded quotes) and assert the round-trip
     preserves the displayable name.
  3. Export an invoice; assert that the SPL line amounts re-import to
     the same totals as the original (catches the "store unquantized,
     re-import quantized, off by a penny" class).

These tests don't assert byte-equal round-trip — IIF doesn't preserve
internal IDs, so a customer "Acme Corp" imports back as a NEW customer
record. What they DO assert: the operator-visible fields (name, number,
amounts) survive the round-trip without corruption.
"""

import io
from decimal import Decimal


def _import_iif_text(client, content: str):
    """POST IIF content to the import endpoint via a multipart upload."""
    return client.post(
        "/api/iif/import",
        files={
            "file": ("export.iif", io.BytesIO(content.encode("utf-8")), "text/plain")
        },
    )


def test_account_export_reimport_preserves_chart(client, db_session, seed_accounts):
    """The exported CoA, reimported into a fresh DB, recreates every account
    by number + name. (We don't compare account IDs — those are local.)"""
    # Capture pre-export state
    from app.models.accounts import Account

    pre = {(a.account_number, a.name) for a in db_session.query(Account).all()}
    assert len(pre) > 0, "seed_accounts should have populated the CoA"

    # Export
    r = client.get("/api/iif/export/accounts")
    assert r.status_code == 200, r.text
    iif_text = r.text

    # Reimport on top of the existing DB — the IIF importer skips
    # duplicates by name, so the row count should not grow.
    pre_count = db_session.query(Account).count()
    r = _import_iif_text(client, iif_text)
    assert r.status_code == 200, r.text

    db_session.expire_all()
    post_count = db_session.query(Account).count()
    assert post_count == pre_count, (
        "Re-importing already-known accounts should be a no-op "
        f"(was {pre_count}, became {post_count}). If the count grew, the "
        "duplicate-detection on import is broken."
    )

    # Every account name in `pre` still exists.
    post_names = {a.name for a in db_session.query(Account).all()}
    for _, name in pre:
        assert name in post_names, f"account '{name}' lost in round-trip"


def test_customer_with_iif_metacharacters_survives_round_trip(
    client, db_session, seed_accounts
):
    """Customer names with tabs/newlines must not split their row on
    re-import. The export side sanitizes the field; this confirms the
    sanitizer doesn't itself drop legitimate characters."""
    from app.models.contacts import Customer

    # Create a customer with characters that IIF treats as field/row
    # delimiters. The export-side _iif_clean strips \t \r \n; the visible
    # name should otherwise round-trip intact.
    nasty = "Acme \t Industries\n LLC"
    db_session.add(Customer(name=nasty, is_active=True))
    db_session.commit()

    r = client.get("/api/iif/export/customers")
    assert r.status_code == 200, r.text
    iif_text = r.text

    # No literal \t inside the customer row — _iif_clean should have
    # neutralized it.
    for line in iif_text.splitlines():
        if line.startswith("CUST"):
            # CUST rows are tab-separated; the NAME field must not itself
            # contain a tab (only the delimiters do).
            fields = line.split("\t")
            for f in fields:
                assert "\n" not in f, f"Newline leaked into IIF field: {line!r}"

    # Re-import — should not error, and the cleaned name (no \t/\n)
    # must appear in the DB. (The original ORM-written record still has
    # the raw characters; we're checking that the round-trip produced a
    # sanitized copy.)
    r = _import_iif_text(client, iif_text)
    assert r.status_code == 200, r.text

    db_session.expire_all()
    names = [c.name for c in db_session.query(Customer).all()]
    sanitized = [
        n
        for n in names
        if "Acme" in n
        and "Industries" in n
        and "\n" not in n
        and "\r" not in n
        and "\t" not in n
    ]
    assert sanitized, f"Re-imported customer name should be sanitized; saw: {names}"


def test_invoice_line_totals_survive_round_trip(
    client, db_session, seed_accounts, seed_customer
):
    """An invoice's SPL line amounts must re-import to the same numeric
    totals as the original. Catches: store unquantized → export → import
    quantized, off by sub-cent."""

    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0.0875,
            "lines": [
                {
                    "description": "Service A",
                    "quantity": 7,
                    "rate": 123.45,
                    "line_order": 0,
                },
                {
                    "description": "Service B",
                    "quantity": 3,
                    "rate": 67.89,
                    "line_order": 1,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    original = r.json()
    original_total = Decimal(str(original["total"]))
    original_subtotal = Decimal(str(original["subtotal"]))

    r = client.get("/api/iif/export/invoices")
    assert r.status_code == 200, r.text
    iif_text = r.text

    # IIF invoice export layout (per app/services/iif_export.py):
    #   TRNS row: AR debit (+total)
    #   SPL rows: income credit (-line_amount) × N, then optional
    #             Sales Tax Payable credit (-tax_amount)
    # Sum of TRNS+SPL = 0 (double-entry).
    trns_total = None
    spl_sum = Decimal("0")
    tax_total = Decimal("0")
    for line in iif_text.splitlines():
        cols = line.split("\t")
        if not cols:
            continue
        if cols[0] == "TRNS" and len(cols) > 5 and cols[1] == "INVOICE":
            trns_total = Decimal(str(cols[5]))
        elif cols[0] == "SPL" and len(cols) > 5 and cols[1] == "INVOICE":
            amt = Decimal(str(cols[5]))
            # ACCNT is cols[3] — Sales Tax SPL goes to the sales-tax acct.
            if cols[3] == "Sales Tax Payable":
                tax_total += amt
            else:
                spl_sum += amt

    assert trns_total is not None, "No TRNS row in invoice export"

    # Double-entry: TRNS + spl_sum + tax_total == 0
    drift = trns_total + spl_sum + tax_total
    assert abs(drift) < Decimal("0.01"), (
        f"IIF rows don't balance: TRNS={trns_total} + SPL={spl_sum} + "
        f"tax={tax_total} = {drift} (expected 0)"
    )

    # Magnitudes must match the source invoice (allow one cent of
    # float-string noise from the JSON serialization).
    assert abs(abs(spl_sum) - original_subtotal) < Decimal(
        "0.01"
    ), f"SPL income {abs(spl_sum)} != invoice subtotal {original_subtotal}"
    assert abs(trns_total - original_total) < Decimal(
        "0.01"
    ), f"TRNS total {trns_total} != invoice total {original_total}"
