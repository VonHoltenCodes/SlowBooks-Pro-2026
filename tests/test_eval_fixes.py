"""Regression tests for the enterprise-eval remediation pass.

Each test pins a confirmed CRITICAL/HIGH finding so it can't silently come
back. See the eval risk register for context.
"""

from decimal import Decimal
import pytest


def _acct_seed(db_session):
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
    from app.models.accounts import Account, AccountType

    for d in CHART_OF_ACCOUNTS:
        db_session.add(
            Account(
                account_number=d["account_number"],
                name=d["name"],
                account_type=AccountType(d["account_type"]),
                is_system=True,
                balance=Decimal("0"),
            )
        )
    db_session.commit()


# --- CRITICAL: void of a paid invoice must be blocked --------------------
def test_void_paid_invoice_blocked(client, db_session, seed_accounts, seed_customer):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "tax_rate": "0",
            "lines": [
                {"description": "x", "quantity": "1", "rate": "100", "line_order": 0}
            ],
        },
    ).json()
    client.post(f"/api/invoices/{inv['id']}/send")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-02",
            "amount": "100",
            "allocations": [{"invoice_id": inv["id"], "amount": "100"}],
        },
    )
    assert pay.status_code == 201, pay.text
    v = client.post(f"/api/invoices/{inv['id']}/void")
    assert v.status_code == 400, "voiding a paid invoice must be rejected"
    assert "payment" in v.json()["detail"].lower()


# --- HIGW: payment over-allocation beyond balance rejected ---------------
def test_payment_cannot_exceed_invoice_balance(
    client, db_session, seed_accounts, seed_customer
):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "tax_rate": "0",
            "lines": [
                {"description": "x", "quantity": "1", "rate": "50", "line_order": 0}
            ],
        },
    ).json()
    over = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-02",
            "amount": "80",
            "allocations": [{"invoice_id": inv["id"], "amount": "80"}],
        },
    )
    assert over.status_code == 400  # 80 > 50 balance


# --- W-2: box_4/box_6 are the actual withheld taxes, not wages*rate ------
def test_w2_boxes_use_actual_withheld_tax(client, db_session):
    from app.models.payroll import Employee, PayRun, PayStub

    emp = Employee(
        first_name="Tess",
        last_name="Withheld",
        pay_type="hourly",
        pay_rate=Decimal("50"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    run = PayRun(
        pay_date=__import__("datetime").date(2026, 3, 15),
        period_start=__import__("datetime").date(2026, 3, 1),
        period_end=__import__("datetime").date(2026, 3, 14),
        status="processed",
    )
    db_session.add(run)
    db_session.commit()
    db_session.add(
        PayStub(
            pay_run_id=run.id,
            employee_id=emp.id,
            gross_pay=Decimal("2000"),
            federal_tax=Decimal("200"),
            state_tax=Decimal("50"),
            ss_tax=Decimal("124.00"),
            medicare_tax=Decimal("29.00"),
            net_pay=Decimal("1597"),
        )
    )
    db_session.commit()
    r = client.post(f"/api/payroll/forms/w2/{emp.id}?year=2026")
    assert r.status_code == 200, r.text
    d = r.json()
    # box_4 == actual SS tax (124.00), NOT 124*0.062; box_6 == medicare (29.00), NOT 29*1.45
    assert Decimal(d["box_4"]) == Decimal("124.00"), d["box_4"]
    assert Decimal(d["box_6"]) == Decimal("29.00"), d["box_6"]


# --- MEDIUM: CSV export neutralizes formula injection --------------------
def test_csv_export_neutralizes_formula(db_session):
    from app.models.contacts import Customer
    from app.services.csv_export import export_customers

    db_session.add(Customer(name='=HYPERLINK("http://evil","x")', is_active=True))
    db_session.commit()
    out = export_customers(db_session)
    assert "=HYPERLINK" not in out.replace(
        "'=HYPERLINK", ""
    )  # the bare formula is neutralized
    assert "'=HYPERLINK" in out  # apostrophe-prefixed


# --- HIGH: dev encryption key + real DB must fail hard even in debug -----
def test_dev_key_with_real_db_fails_even_in_debug(monkeypatch):
    import app.main as m
    import app.config as cfg

    monkeypatch.setattr(
        cfg, "DATABASE_URL", "postgresql://u:p@h:5432/db?sslmode=require"
    )
    monkeypatch.setattr(
        cfg, "PAYROLL_ENCRYPTION_SECRET", "slowbooks-dev-payroll-key-change-me"
    )
    monkeypatch.setattr(cfg, "APP_DEBUG", True)  # debug ON — must still fail
    with pytest.raises(RuntimeError, match="dev default"):
        m._run_startup_security_checks()
