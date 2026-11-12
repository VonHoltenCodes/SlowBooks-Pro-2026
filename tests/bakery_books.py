"""The bakery's books: the company every browser module and the report-link
tests are run on, entered through the API as a person would (every list has
rows, every document a status, every dialog something to show).

A module of its own, with no Playwright in it: tests/test_theme_contrast.py
skips as a whole where Playwright is missing, and tests/test_report_links.py
(six server-side tests) was skipped with it for importing the fixture from
there (2.22.0 Windows gate, W-2). The `books` fixture is in tests/conftest.py.
"""

from datetime import date, timedelta


def _ok(r):
    assert r.status_code in (200, 201), (str(r.request.url), r.status_code, r.text)
    return r.json()


def _job_cost_on_payables(seed_accounts, S):
    """The job cost entry that credits Accounts Payable with no vendor: saved
    before 2.32, when the entry took any offset account. The window and the
    API refuse one now (#897), so it is written as it was then, through the
    posting service, and Books Check still finds the $300 it leaves off the
    A/P Aging until the books' fixture voids it."""
    from decimal import Decimal

    from sqlalchemy.orm import object_session

    from app.models.job_costing import JobCost, JobCostLine
    from app.services.job_costing import next_job_cost_number, post_job_cost

    db = object_session(seed_accounts["1000"])
    jc = JobCost(
        number=next_job_cost_number(db),
        date=date(2026, 9, 12),
        job_id=S["job"],
        memo="Rental chairs",
        source="manual",
    )
    db.add(jc)
    db.flush()
    jc.lines.append(
        JobCostLine(
            job_cost_id=jc.id,
            cost_code_id=S["cost_code"],
            cost_type="labor",  # the cost code's, as the entry form took it
            description="Chair rental",
            quantity=Decimal("120"),
            rate=Decimal("2.5"),
            amount=Decimal("300.00"),
            debit_account_id=seed_accounts["5000"].id,
            credit_account_id=seed_accounts["2000"].id,
            line_order=0,
        )
    )
    db.flush()
    post_job_cost(db, jc)
    db.commit()
    return jc.id


def seed_books(client, seed_accounts):
    """The ids of what it made, by name."""
    a = {number: account.id for number, account in seed_accounts.items()}

    def post(path, body=None):
        return _ok(client.post(path, json=body or {}))

    S = {"checking": a["1000"]}
    S["bank"] = post(
        "/api/banking/accounts",
        {
            "name": "Harbor Checking",
            "account_id": a["1000"],
            "bank_name": "Columbia Bank",
            "last_four": "4417",
            "opening_balance": "12500.00",
            "opening_date": "2026-01-01",
        },
    )["id"]
    S["service"] = post(
        "/api/items",
        {
            "name": "Catering Service",
            "item_type": "service",
            "description": "Event catering, per hour",
            "rate": 85,
            "income_account_id": a["4000"],
            "is_taxable": False,
        },
    )["id"]
    S["loaf"] = post(
        "/api/items",
        {
            "name": "Sourdough Loaf",
            "item_type": "product",
            "description": "Sourdough loaf, 1 kg",
            "rate": 8.5,
            "cost": 3.1,
            "is_taxable": True,
            "track_inventory": True,
            "quantity_on_hand": 40,
            "reorder_point": 10,
        },
    )["id"]
    S["customer"] = post(
        "/api/customers",
        {
            "name": "Salt & Pine Catering Co.",
            "company": "Salt & Pine Catering Co.",
            "email": "orders@saltandpine.example",
            "phone": "(503) 555-0142",
            "bill_address1": "1180 Commercial St",
            "bill_city": "Astoria",
            "bill_state": "OR",
            "bill_zip": "97103",
            "terms": "Net 15",
            "is_taxable": True,
        },
    )["id"]
    S["customer2"] = post(
        "/api/customers",
        {
            "name": "Harbor District Events",
            "email": "hello@harborevents.example",
            "terms": "Net 30",
        },
    )["id"]
    S["vendor"] = post(
        "/api/vendors",
        {
            "name": "Cascade Flour Mill",
            "company": "Cascade Flour Mill",
            "address1": "44 Mill Rd",
            "city": "Portland",
            "state": "OR",
            "zip": "97201",
            "terms": "Net 30",
        },
    )["id"]
    S["vendor2"] = post(
        "/api/vendors",
        {
            "name": "Blue Heron Installs",
            "is_1099_vendor": True,
            "vendor_1099_type": "NEC",
        },
    )["id"]

    def invoice(amount, date, **extra):
        return post(
            "/api/invoices",
            {
                "customer_id": S["customer"],
                "date": date,
                "tax_rate": 0,
                "lines": [
                    {
                        "item_id": S["service"],
                        "description": "Event catering",
                        "quantity": 1,
                        "rate": amount,
                    }
                ],
                **extra,
            },
        )

    def pay(inv, amount):
        return post(
            "/api/payments",
            {
                "customer_id": S["customer"],
                "date": "2026-09-20",
                "amount": amount,
                "method": "Check",
                "check_number": "2231",
                "allocations": [{"invoice_id": inv["id"], "amount": amount}],
            },
        )

    # an invoice in every status a list shows
    S["draft"] = invoice(120, "2026-09-24")["id"]
    S["sent"] = invoice(240, "2026-09-22", due_date="2026-12-31")["id"]
    _ok(client.post(f"/api/invoices/{S['sent']}/send"))
    paid = invoice(300, "2026-09-10")
    S["paid"], S["payment"] = paid["id"], pay(paid, 300)["id"]
    part = invoice(500, "2026-09-12")
    S["partial"] = part["id"]
    pay(part, 200)
    S["overdue"] = invoice(80, "2026-06-01", due_date="2026-06-15")["id"]
    _ok(client.post(f"/api/invoices/{S['overdue']}/send"))
    S["void"] = invoice(999, "2026-09-05")["id"]
    _ok(client.post(f"/api/invoices/{S['void']}/void"))
    loaves = [{"item_id": S["loaf"], "description": "Sourdough Loaf", "rate": 8.5}]
    S["estimate"] = post(
        "/api/estimates",
        {
            "customer_id": S["customer"],
            "date": "2026-09-18",
            "expiration_date": "2026-10-18",
            "tax_rate": 0.0825,
            "lines": [dict(loaves[0], quantity=24)],
        },
    )["id"]
    S["receipt"] = post(
        "/api/sales-receipts",
        {
            "customer_id": S["customer2"],
            "date": "2026-09-19",
            "tax_rate": 0.0825,
            "method": "card",
            "lines": [dict(loaves[0], quantity=3, is_taxable=True)],
        },
    )["invoice"]["id"]
    S["memo"] = post(
        "/api/credit-memos",
        {
            "customer_id": S["customer2"],
            "date": "2026-09-21",
            "lines": [
                {"description": "Returned tray deposit", "quantity": 1, "rate": 45}
            ],
        },
    )["id"]
    # an open invoice beside the credit memo, for the apply-credit dialogs
    S["invoice2"] = post(
        "/api/invoices",
        {
            "customer_id": S["customer2"],
            "date": "2026-09-23",
            "tax_rate": 0,
            "lines": [{"description": "Tasting menu", "quantity": 1, "rate": 150}],
        },
    )["id"]
    S["deposit"] = post(
        "/api/deposits",
        {"deposit_to_account_id": a["1000"], "date": "2026-09-22", "total": "300"},
    )["transaction_id"]
    S["recurring"] = post(
        "/api/recurring",
        {
            "customer_id": S["customer2"],
            "frequency": "monthly",
            "start_date": "2026-10-01",
            "lines": [
                {
                    "item_id": S["service"],
                    "description": "Monthly tasting",
                    "quantity": 2,
                    "rate": 85,
                }
            ],
        },
    )["id"]

    # purchases and the bank
    S["bill"] = post(
        "/api/bills",
        {
            "vendor_id": S["vendor"],
            "date": "2026-09-08",
            "terms": "Net 30",
            "bill_number": "CFM-0918",
            "lines": [
                {
                    "account_id": a["5000"],
                    "description": "Flour, 50 lb",
                    "quantity": 12,
                    "rate": 38.5,
                }
            ],
        },
    )["id"]
    S["paid_bill"] = post(
        "/api/bills",
        {
            "vendor_id": S["vendor2"],
            "date": "2026-08-28",
            "terms": "Net 15",
            "bill_number": "BHI-221",
            "lines": [
                {
                    "account_id": a["6000"],
                    "description": "Oven hood install",
                    "quantity": 1,
                    "rate": 640,
                }
            ],
        },
    )["id"]
    S["bill_payment"] = post(
        "/api/bill-payments",
        {
            "vendor_id": S["vendor2"],
            "date": "2026-09-09",
            "amount": 640,
            "allocations": [{"bill_id": S["paid_bill"], "amount": 640}],
        },
    )["id"]
    S["po"] = post(
        "/api/purchase-orders",
        {
            "vendor_id": S["vendor"],
            "date": "2026-09-15",
            "tax_rate": 0,
            "lines": [{"description": "Rye flour, 25 lb", "quantity": 8, "rate": 22}],
        },
    )["id"]
    S["vendor_credit"] = post(
        "/api/vendor-credits",
        {
            "vendor_id": S["vendor"],
            "date": "2026-09-16",
            "lines": [
                {
                    "description": "Short-shipped bag",
                    "quantity": 1,
                    "rate": 38.5,
                    "account_id": a["5000"],
                }
            ],
        },
    )["id"]
    S["journal"] = post(
        "/api/journal",
        {
            "date": "2026-09-25",
            "description": "September accrual",
            "lines": [
                {"account_id": a["6000"], "debit": "50.00", "credit": "0"},
                {"account_id": a["1000"], "debit": "0", "credit": "50.00"},
            ],
        },
    )["id"]
    S["expense"] = post(
        "/api/expenses",
        {
            "date": "2026-09-02",
            "expense_account_id": a["6000"],
            "paid_from_account_id": a["1000"],
            "amount": "30.30",
            "reference": "rcpt 1187",
            "memo": "Team lunch",
            "vendor_id": S["vendor"],
        },
    )["id"]
    S["cc_charge"] = post(
        "/api/cc-charges",
        {
            "date": "2026-09-01",
            "payee": "Fuel",
            "amount": "40",
            "account_id": a["6000"],
        },
    )["transaction_id"]
    S["bank_rule"] = post(
        "/api/bank-rules",
        {"name": "Fuel", "pattern": "SHELL", "account_id": a["6000"]},
    )["id"]
    # a statement imported to the bank feed: two lines it matches, one to review
    statement = (
        "Date,Description,Amount\n"
        "09/02/2026,SWEET FOREST CAFE,-30.30\n"
        "09/24/2026,SHELL OIL 5521,-48.12\n"
        '09/25/2026,MOBILE DEPOSIT,"300.00"\n'
    )
    _ok(
        client.post(
            f"/api/bank-import/import-csv/{S['bank']}",
            files={"file": ("statement.csv", statement.encode(), "text/csv")},
        )
    )
    feed = _ok(client.get(f"/api/banking/transactions?bank_account_id={S['bank']}"))
    S["feed_line"] = next(t["id"] for t in feed if t["match_status"] == "auto")
    # January's statement, reconciled
    recon = post(
        "/api/banking/reconciliations",
        {
            "account_id": a["1000"],
            "statement_date": "2026-01-31",
            "statement_balance": "12500.00",
        },
    )["id"]
    session = _ok(client.get(f"/api/banking/reconciliations/{recon}/transactions"))
    for line in session["transactions"]:
        post(f"/api/banking/reconciliations/{recon}/toggle/{line['id']}")
    post(f"/api/banking/reconciliations/{recon}/complete")
    S["reconciliation"] = recon

    # a job
    S["cost_code"] = post(
        "/api/cost-codes", {"code": "02-100", "name": "Site prep", "cost_type": "labor"}
    )["id"]
    S["job"] = post(
        "/api/jobs",
        {
            "customer_id": S["customer"],
            "name": "Waterfront Gala",
            "job_number": "J-1001",
            "job_type": "Catering",
            "start_date": "2026-09-01",
            "contract_amount": "4800.00",
            "notes": "Tent setup the day before.",
        },
    )["id"]
    S["job_cost"] = _job_cost_on_payables(seed_accounts, S)

    # people
    staff = {
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "city": "Astoria",
        "state": "OR",
        "zip": "97103",
        "work_state": "OR",
        "residence_state": "OR",
        "role": "employee",
    }
    S["employee"] = post(
        "/api/employees",
        dict(
            staff,
            first_name="Lena",
            last_name="Ortiz",
            ssn_last_four="4410",
            pay_type="salary",
            pay_rate=56000,
            address1="310 Duane St",
            email="lena@example.com",
            hire_date="2025-03-01",
        ),
    )["id"]
    S["employee2"] = post(
        "/api/employees",
        dict(
            staff,
            first_name="Jonah",
            last_name="Pike",
            ssn_last_four="5521",
            pay_type="hourly",
            pay_rate=19.5,
            hire_date="2026-02-10",
        ),
    )["id"]
    S["time_entry"] = post(
        "/api/time-entries",
        {
            "employee_id": S["employee2"],
            "date": "2026-09-15",
            "hours_regular": 8,
            "job_id": S["job"],
            "cost_code_id": S["cost_code"],
            "notes": "Gala prep",
        },
    )["id"]
    S["pay_run"] = post(
        "/api/payroll",
        {
            "period_start": "2026-09-01",
            "period_end": "2026-09-14",
            "pay_date": "2026-09-19",
            "stubs": [
                {"employee_id": S["employee"]},
                {"employee_id": S["employee2"], "hours": 72},
            ],
        },
    )["id"]
    S["pto_policy"] = post(
        "/api/pto/policies",
        {
            "name": "Paid Sick Leave",
            "pto_type": "sick",
            "accrual_method": "per_hour_worked",
            "accrual_rate": 0.025,
            "max_carryover": 40,
            "accrue_liability": True,
        },
    )["id"]
    post(
        "/api/pto/accruals",
        {"employee_id": S["employee"], "policy_id": S["pto_policy"], "balance": 6},
    )
    post("/api/benefits/setup-accounts")
    S["benefit_code"] = post("/api/benefits/codes/seed-standard")[0]["id"]
    S["benefit_group"] = post(
        "/api/benefits/groups",
        {"name": "Full-time staff", "description": "Salaried, and 30 or more hours"},
    )["id"]

    post("/api/email-templates/seed-defaults")
    S["template"] = _ok(client.get("/api/email-templates"))[0]["id"]

    # a fixed asset
    S["asset_type"] = post(
        "/api/fixed-assets/types",
        {
            "name": "Kitchen Equipment",
            "description": "Ovens, mixers, proofers",
            "depreciation_method": "straight_line",
            "effective_life_years": 7,
            "asset_account_id": a["1500"],
            "accumulated_depreciation_account_id": a["1510"],
            "depreciation_expense_account_id": a["6810"],
        },
    )["id"]
    S["asset"] = post(
        "/api/fixed-assets",
        {
            "name": "Deck oven",
            "asset_type_id": S["asset_type"],
            "purchase_date": "2025-11-04",
            "purchase_price": "18400.00",
            "salvage_value": "0.00",
        },
    )["id"]

    # a reseller permit in each state the page tells apart
    def permit(number, expires, active=True):
        return post(
            "/api/reseller-permits",
            {
                "entity_type": "customer",
                "entity_id": S["customer"],
                "jurisdiction": "WA",
                "permit_number": number,
                "issued_at": "2024-01-15",
                "expires_at": expires,
                "is_active": active,
            },
        )["id"]

    # "Expires soon" is within 30 days of today, so the date is worked out
    # from today: a fixed one went from soon to expired on 2026-10-07 and
    # the sweep lost the "Expires soon" badge it measures.
    soon = (date.today() + timedelta(days=10)).isoformat()
    S["permit_expired"] = permit("603112457", "2026-08-01")
    S["permit_soon"] = permit("603112458", soon)
    S["permit_active"] = permit("603112459", "2028-01-14")
    S["permit_inactive"] = permit("603112460", "2028-06-30", active=False)
    post(
        f"/api/reseller-permits/{S['permit_active']}/mark-verified",
        {"verified_by": "TVH"},
    )
    # a void expense and an inactive vendor and item, which their lists keep,
    # set aside
    voided = post(
        "/api/expenses",
        {
            "date": "2026-09-03",
            "expense_account_id": a["6000"],
            "paid_from_account_id": a["1000"],
            "amount": "12.00",
            "memo": "Entered twice",
        },
    )["id"]
    post(f"/api/expenses/{voided}/void")
    old = post("/api/vendors", {"name": "Old Harbor Ice Co."})["id"]
    _ok(client.put(f"/api/vendors/{old}", json={"is_active": False}))
    stollen = post(
        "/api/items",
        {"name": "Holiday Stollen", "item_type": "product", "rate": 14},
    )["id"]
    _ok(client.put(f"/api/items/{stollen}", json={"is_active": False}))
    # every card on the dashboard, not only the ones it starts with
    cards = _ok(client.get("/api/dashboard/widgets"))["widgets"]
    order = {"value": {"order": [c["id"] for c in cards]}}
    _ok(client.put("/api/preferences/dashboard", json=order))
    return S
