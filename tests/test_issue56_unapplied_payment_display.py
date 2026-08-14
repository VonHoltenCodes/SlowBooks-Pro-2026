# Issue #56: an unapplied customer payment (no invoice allocation) drives
# Accounts Receivable to a legitimate credit balance — a customer prepayment.
# The ledger and the balance-sheet API were always correct; the SPA renderers
# wrapped line amounts in Math.abs(), so the A/R line displayed positive while
# the total summed signed values ("$100 + $400 + $600 = $300").
#
# These tests pin both halves: the API contract (signed lines, total == sum of
# lines) and the frontend source (report renderers must not strip signs).

from decimal import Decimal
from pathlib import Path

REPORTS_JS = (
    Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "reports.js"
)


def _make_unapplied_payment(client, customer_id, amount):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "amount": amount,
            "date": "2026-08-14",
            "payment_method": "check",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_unapplied_payment_yields_negative_ar_line(
    client, seed_accounts, seed_customer
):
    """A prepayment with no invoice must surface as a signed (negative) A/R
    line, not vanish or flip positive."""
    _make_unapplied_payment(client, seed_customer.id, 400.0)

    r = client.get("/api/reports/balance-sheet?as_of_date=2026-12-31")
    assert r.status_code == 200, r.text
    bs = r.json()

    ar_lines = [a for a in bs["assets"] if a["account_name"] == "Accounts Receivable"]
    assert ar_lines, "A/R line missing from the balance sheet"
    assert Decimal(str(ar_lines[0]["amount"])) == Decimal("-400.00")


def test_balance_sheet_total_equals_sum_of_lines(client, seed_accounts, seed_customer):
    """The reported totals must equal the sum of the reported lines — the
    exact property whose violation was visible in the issue screenshot."""
    _make_unapplied_payment(client, seed_customer.id, 400.0)

    r = client.get("/api/reports/balance-sheet?as_of_date=2026-12-31")
    assert r.status_code == 200, r.text
    bs = r.json()

    for section, total_key in (
        ("assets", "total_assets"),
        ("liabilities", "total_liabilities"),
    ):
        line_sum = sum((Decimal(str(a["amount"])) for a in bs[section]), Decimal("0"))
        assert line_sum == Decimal(str(bs[total_key])), (
            f"{total_key} ({bs[total_key]}) != sum of {section} lines "
            f"({line_sum}) — a renderer showing these lines unsigned would "
            f"contradict its own total"
        )


def test_report_renderers_do_not_strip_amount_signs():
    """Frontend guard (no JS test rig upstream): report line renderers must
    pass amounts to formatCurrency signed. Math.abs() around a rendered
    amount is how issue #56 happened."""
    source = REPORTS_JS.read_text(encoding="utf-8")
    assert "formatCurrency(Math.abs(" not in source, (
        "reports.js renders an amount through Math.abs() — contra-balance "
        "accounts (e.g. A/R after an unapplied customer payment) must "
        "display signed, matching the totals (issue #56)"
    )
