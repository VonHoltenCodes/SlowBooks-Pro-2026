"""Unit prices to four decimal places (2.17.3 exploratory, macbase1
suggestion S-f).

Bulk packaging is priced like $0.045 a box, and the sales forms refused it
(the rate box stepped in cents). Worse, the API took it and the line's
amount was worked out at $0.045, but the rate column held two places: the
document then read 1,000 x $0.04 = $45.00, and editing it (which re-sends
or recomputes from the stored rate) quietly changed the total.

Invoice, estimate, credit memo and recurring lines now keep their rate to
four places (migration 6f57f762f464); each line amount is still rounded to
the cent, and tax is taken on the rounded lines, as before. The forms take
four places and the documents print them only when they are there.
"""

import json
import shutil
import sqlite3
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import pdf_service

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
BOXES = {"description": "Cake boxes", "quantity": 1000, "rate": "0.045"}


def test_an_invoice_keeps_a_sub_cent_price(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [
                BOXES,
                {"description": "Labels", "quantity": 3, "rate": "0.3333"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert [ln["rate"] for ln in got["lines"]] == ["0.045", "0.3333"]
    assert [ln["amount"] for ln in got["lines"]] == ["45.00", "1.00"]
    assert Decimal(got["total"]) == Decimal("46.00")

    # an edit that recomputes from the stored lines keeps the total
    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": 0.1})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["subtotal"]) == Decimal("46.00")
    assert Decimal(r.json()["tax_amount"]) == Decimal("4.60")


def test_whole_cent_prices_read_as_before(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "lines": [{"description": "Banner", "quantity": 2, "rate": "120.5"}],
        },
    )
    assert r.json()["lines"][0]["rate"] == "120.50"


def test_estimates_credit_memos_and_schedules_keep_it_too(
    client, seed_accounts, seed_customer
):
    est = client.post(
        "/api/estimates",
        json={"customer_id": seed_customer.id, "date": "2026-09-01", "lines": [BOXES]},
    ).json()
    assert est["lines"][0]["rate"] == "0.045" and est["total"] == "45.00"
    inv = client.post(f"/api/estimates/{est['id']}/convert").json()
    assert inv["lines"][0]["rate"] == "0.045" and inv["total"] == "45.00"

    cm = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-26",
            "lines": [dict(BOXES, quantity=100)],
        },
    ).json()
    assert cm["lines"][0]["rate"] == "0.045" and cm["total"] == "4.50"

    rec = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-09-01",
            "lines": [BOXES],
        },
    ).json()
    assert rec["lines"][0]["rate"] == 0.045
    run = client.post("/api/recurring/generate?as_of=2026-09-26").json()
    made = client.get(f"/api/invoices/{run['invoice_ids'][0]}").json()
    assert made["lines"][0]["rate"] == "0.045" and made["total"] == "45.00"


def test_the_pdf_prints_the_price_it_was_given(
    client, seed_accounts, seed_customer, monkeypatch
):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    inv = client.post(
        "/api/invoices",
        json={"customer_id": seed_customer.id, "date": "2026-09-01", "lines": [BOXES]},
    ).json()
    html = client.get(f"/api/invoices/{inv['id']}/pdf").text
    assert "$0.045" in html and "$45.00" in html
    assert pdf_service._format_rate(Decimal("12.5000")) == "$12.50"
    assert pdf_service._format_rate(Decimal("1234.0450"), "EUR") == "EUR 1,234.045"


def test_the_sales_forms_take_four_places():
    for page in (
        "invoices.js",
        "estimates.js",
        "sales_receipts.js",
        "credit_memos.js",
        "recurring.js",
    ):
        src = (JS / page).read_text(encoding="utf-8")
        assert '<input class="line-rate" type="number" step="0.0001" min="0"' in src
        assert 'class="line-rate" type="number" step="0.01"' not in src, page


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_form_rounds_each_line_to_the_cent():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "sales_lines_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["rates"] == ["$0.045", "$12.50", "EUR 1.0055", "$1,234.50"]
    assert got["subCentAmounts"] == ["$45.00", "$1.00"]
    assert got["subCent"] == {"subtotal": 46, "tax": 0, "total": 46}


def test_the_migration_widens_the_rate_and_keeps_every_value(tmp_path):
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "old.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "a9b0c1d2e3f4")

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO customers (name, is_active, send_year_end_statement) "
        "VALUES ('Harbor Light', 1, 1)"
    )
    con.execute(
        "INSERT INTO invoices (invoice_number, customer_id, date, is_sales_receipt, "
        "is_pledge) VALUES ('1001', 1, '2026-09-01', 0, 0)"
    )
    for rate, amount in ((0.045, 45.0), (120.5, 241.0)):
        con.execute(
            "INSERT INTO invoice_lines (invoice_id, quantity, rate, amount, "
            "is_taxable, line_order) VALUES (1, ?, ?, ?, 1, 0)",
            (amount / rate, rate, amount),
        )
    con.commit()
    con.close()

    def rate_types():
        c = sqlite3.connect(db)
        try:
            return {
                t: next(
                    col[2]
                    for col in c.execute(f"PRAGMA table_info({t})")
                    if col[1] == "rate"
                )
                for t in (
                    "invoice_lines",
                    "estimate_lines",
                    "credit_memo_lines",
                    "recurring_invoice_lines",
                )
            }
        finally:
            c.close()

    command.upgrade(cfg, "6f57f762f464")
    assert set(rate_types().values()) == {"NUMERIC(17, 4)"}
    con = sqlite3.connect(db)
    assert con.execute("SELECT rate FROM invoice_lines ORDER BY id").fetchall() == [
        (0.045,),
        (120.5,),
    ]
    con.close()

    command.downgrade(cfg, "a9b0c1d2e3f4")
    assert set(rate_types().values()) == {"NUMERIC(15, 2)"}
