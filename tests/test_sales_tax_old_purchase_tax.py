"""Books from before 2.18 carry supplier tax in Sales Tax Payable: bills and
PO-made bills debited 2200 with the tax on a purchase. The Sales Tax report
names that amount (this period and to date) so the difference from the
ledger is explained, and says which journal entry corrects it."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.accounting import create_journal_entry

ROOT = Path(__file__).resolve().parents[1]


def test_old_purchase_tax_in_2200_is_named_with_its_correction(
    client, db_session, seed_accounts
):
    # A pre-2.18 bill: DR expense 720, DR 2200 59.24 (the supplier's tax), CR A/P
    create_journal_entry(
        db_session,
        date(2026, 9, 10),
        "Bill BILL-PO-0001 - Cascade Flour Mill",
        [
            {
                "account_id": seed_accounts["6000"].id,
                "debit": Decimal("720.00"),
                "credit": Decimal("0"),
            },
            {
                "account_id": seed_accounts["2200"].id,
                "debit": Decimal("59.24"),
                "credit": Decimal("0"),
            },
            {
                "account_id": seed_accounts["2000"].id,
                "debit": Decimal("0"),
                "credit": Decimal("779.24"),
            },
        ],
        source_type="bill",
        source_id=1,
    )
    db_session.commit()
    r = client.get("/api/reports/sales-tax?start_date=2026-09-01&end_date=2026-09-30")
    assert r.status_code == 200, r.text
    ledger = r.json()["ledger"]
    assert Decimal(str(ledger["purchase_tax"])) == Decimal("59.24")
    assert Decimal(str(ledger["purchase_tax_to_date"])) == Decimal("59.24")
    # the gap between the report and 2200 is exactly that tax
    assert Decimal(str(ledger["difference"])) == Decimal("59.24")


def test_the_report_page_explains_it_and_gives_the_entry():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "ledger.purchase_tax_to_date" in js
    assert (
        "sales tax paid to suppliers on bills entered before SlowBooks Pro 2.18" in js
    )
