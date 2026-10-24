"""Nonprofit mode, end to end, the way a bring-your-own-AI agent works it:
scoped API tokens, the OpenAPI spec for discovery, and the public
endpoints only — the agents never use the session cookie or the database.

An administrator, signed in, switches the company to nonprofit and seeds
its accounts (settings are a person's: no API token is an administrator,
#434). Then two agents share the company file:
  the bookkeeper's    runs a condensed Riverbend Community Arts year
  the treasurer's     (readonly) pulls every statement and checks that
                      they reconcile to the cent — and cannot write

The numbers are chosen so every equality below is checkable by hand.
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app


def _mint(client, label, role):
    r = client.post("/api/tokens", json={"label": label, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _bearer(token):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _ok(r, *codes):
    assert r.status_code in (
        codes or (200, 201)
    ), f"{r.request.method} {r.url}: {r.status_code} {r.text[:300]}"
    return r.json() if r.text else None


NONPROFIT_PATHS = (
    "/api/nonprofit/setup-accounts",
    "/api/nonprofit/allocation-rules",
    "/api/nonprofit/allocation-rules/{rule_id}/split",
    "/api/nonprofit/allocation-rules/{rule_id}/preview",
    "/api/nonprofit/allocations",
    "/api/nonprofit/allocations/{fa_id}/void",
    "/api/nonprofit/releases",
    "/api/nonprofit/releases/suggest",
    "/api/nonprofit/releases/{rel_id}/void",
    "/api/donors/gifts/{kind}/{gift_id}/acknowledgment/pdf",
    "/api/donors/gifts/{kind}/{gift_id}/acknowledgment/email",
    "/api/donors/giving-statements/pdf",
    "/api/donors/giving-statements/batch-email",
    "/api/in-kind-gifts",
    "/api/reports/statement-of-activities",
    "/api/reports/statement-of-financial-position",
    "/api/reports/fund-balances",
    "/api/reports/functional-expenses",
    "/api/reports/pledges",
    "/api/invoices/{invoice_id}/write-off",
    "/api/credit-memos/{cm_id}/void",
)


def test_nonprofit_year_through_the_api(client, seed_accounts, monkeypatch):
    # ---- the admin, signed in, mints the agents' tokens (a session-only
    # power) and does the setup: a token is never an administrator (#434) ----
    admin = client
    keeper = _bearer(_mint(client, "bookkeeper-agent", "bookkeeper"))
    reader = _bearer(_mint(client, "treasurer-agent", "readonly"))

    # ---- discovery: the spec names every nonprofit path, with a summary ----
    spec = admin.get("/openapi.json").json()
    for path in NONPROFIT_PATHS:
        assert path in spec["paths"], path
        for method, op in spec["paths"][path].items():
            assert op.get("summary") or op.get(
                "description"
            ), f"{method} {path} has no description"

    # ---- setup, as the signed-in administrator ------------------------------
    _ok(admin.put("/api/settings", json={"company_type": "nonprofit"}))
    assert admin.get("/api/settings").json()["company_type"] == "nonprofit"
    first = _ok(admin.post("/api/nonprofit/setup-accounts"))
    again = _ok(admin.post("/api/nonprofit/setup-accounts"))
    assert [a["id"] for a in first] == [a["id"] for a in again]
    by_name = {a["name"]: a["id"] for a in first}
    assert set(by_name) == {
        "Net Assets Without Donor Restrictions",
        "Net Assets With Donor Restrictions",
        "In-Kind Contributions",
        "Bad Debt Expense",
    }
    # the daily-work role cannot flip the company type
    assert (
        keeper.put("/api/settings", json={"company_type": "business"}).status_code
        == 403
    )

    # ---- the vocabulary and structure an agent would read first -------------
    widgets = reader.get("/api/dashboard/widgets").json()
    titles = {w["id"]: w["title"] for w in widgets["widgets"]}
    assert titles["receivables"] == "Pledges Receivable"
    assert titles["active_customers"] == "Active Donors"
    assert "job_budget_vs_actual" in widgets["default_order"]
    assert {"restriction", "default_function"} <= set(
        reader.get("/api/classes").json()[0]
    )

    # ---- Riverbend, condensed, as the bookkeeper's agent --------------------
    def fund(name, restriction, function):
        return _ok(
            keeper.post(
                "/api/classes",
                json={
                    "name": name,
                    "restriction": restriction,
                    "default_function": function,
                },
            )
        )

    general = fund("General Fund", "unrestricted", "management")
    youth = fund("Youth Program", "temporarily_restricted", "program")
    endow = fund("Scholarship Endowment", "permanently_restricted", "program")
    gala = fund("Spring Gala", "unrestricted", "fundraising")

    foundation = _ok(
        keeper.post(
            "/api/customers",
            json={
                "name": "Riverbend County Community Foundation",
                "donor_type": "organization",
                "email": "grants@rccf.example",
            },
        )
    )
    hartwell = _ok(
        keeper.post(
            "/api/customers",
            json={"name": "Hartwell Family Trust", "donor_type": "organization"},
        )
    )
    maria = _ok(
        keeper.post(
            "/api/customers",
            json={
                "name": "Maria Okafor",
                "donor_type": "individual",
                "salutation": "Dear Maria",
                "email": "maria@example.org",
            },
        )
    )
    guest = _ok(
        keeper.post(
            "/api/customers",
            json={
                "name": "Gala Guest",
                "donor_type": "individual",
                "send_year_end_statement": False,
            },
        )
    )
    vendor = _ok(keeper.post("/api/vendors", json={"name": "Riverbend Properties LLC"}))

    checking = seed_accounts["1010"].id
    supplies = seed_accounts["6000"].id
    rent = _ok(
        keeper.post(
            "/api/accounts",
            json={
                "name": "Rent Expense",
                "account_number": "6105",
                "account_type": "expense",
            },
        )
    )["id"]

    # grant award: 24,000 to the restricted Youth Program (a job with a budget)
    grant = _ok(
        keeper.post(
            "/api/jobs",
            json={
                "customer_id": foundation["id"],
                "name": "Youth Program Grant FY26",
                "contract_amount": 24000,
            },
        )
    )
    award = _ok(
        keeper.post(
            "/api/invoices",
            json={
                "customer_id": foundation["id"],
                "date": "2026-03-01",
                "tax_rate": "0",
                "class_id": youth["id"],
                "job_id": grant["id"],
                "is_pledge": False,
                "lines": [
                    {"description": "Grant award FY26", "quantity": 1, "rate": "24000"}
                ],
            },
        )
    )
    _ok(
        keeper.post(
            "/api/payments",
            json={
                "customer_id": foundation["id"],
                "date": "2026-03-15",
                "amount": "24000",
                "deposit_to_account_id": checking,
                "allocations": [{"invoice_id": award["id"], "amount": "24000"}],
            },
        )
    )
    # endowment gift: 50,000 permanently restricted
    _ok(
        keeper.post(
            "/api/sales-receipts",
            json={
                "customer_id": hartwell["id"],
                "date": "2026-01-15",
                "tax_rate": "0",
                "class_id": endow["id"],
                "deposit_to_account_id": checking,
                "lines": [
                    {"description": "Endowment gift", "quantity": 1, "rate": "50000"}
                ],
            },
        )
    )
    # a monthly pledge, two installments generated, the first paid
    pledge = _ok(
        keeper.post(
            "/api/recurring",
            json={
                "customer_id": maria["id"],
                "frequency": "monthly",
                "start_date": "2026-01-01",
                "class_id": general["id"],
                "lines": [
                    {"description": "Monthly pledge", "quantity": 1, "rate": "100"}
                ],
            },
        )
    )
    jan = _ok(keeper.post("/api/recurring/generate?as_of=2026-01-10"))["invoice_ids"][0]
    feb = _ok(keeper.post("/api/recurring/generate?as_of=2026-02-10"))["invoice_ids"][0]
    got = keeper.get(f"/api/invoices/{jan}").json()
    assert got["recurring_invoice_id"] == pledge["id"] and got["is_pledge"] is True
    _ok(
        keeper.post(
            "/api/payments",
            json={
                "customer_id": maria["id"],
                "date": "2026-01-20",
                "amount": "100",
                "deposit_to_account_id": checking,
                "allocations": [{"invoice_id": jan, "amount": "100"}],
            },
        )
    )
    # gala ticket with a $45 dinner
    ticket = _ok(
        keeper.post(
            "/api/sales-receipts",
            json={
                "customer_id": guest["id"],
                "date": "2026-05-09",
                "tax_rate": "0",
                "class_id": gala["id"],
                "deposit_to_account_id": checking,
                "fair_value_amount": "45",
                "fair_value_description": "gala dinner",
                "lines": [{"description": "Gala ticket", "quantity": 1, "rate": "150"}],
            },
        )
    )
    pdf = keeper.get(f"/api/invoices/{ticket['invoice']['id']}/pdf")
    assert (
        pdf.content[:5] == b"%PDF-"
        and "DonationReceipt_" in pdf.headers["content-disposition"]
    )
    html = keeper.get(f"/api/invoices/{ticket['invoice']['id']}/print-preview").text
    assert (
        "DONATION RECEIPT" in html
        and "$105.00" in html
        and "No goods or services" not in html
    )
    # an unapplied gift is acknowledged; a receipt's own payment is not
    gift = _ok(
        keeper.post(
            "/api/payments",
            json={
                "customer_id": maria["id"],
                "date": "2026-06-01",
                "amount": "500",
                "deposit_to_account_id": checking,
            },
        )
    )
    assert keeper.get(
        f"/api/donors/gifts/payment/{gift['id']}/acknowledgment/preview"
    ).json() == {"eligible": True, "amount": 500.0, "reason": None}
    assert (
        keeper.get(
            f"/api/donors/gifts/payment/{ticket['payment']['id']}/acknowledgment/preview"
        ).json()["eligible"]
        is False
    )
    assert (
        keeper.get(
            f"/api/donors/gifts/payment/{gift['id']}/acknowledgment/pdf"
        ).content[:5]
        == b"%PDF-"
    )
    # the piano
    piano = _ok(
        keeper.post(
            "/api/in-kind-gifts",
            json={
                "customer_id": hartwell["id"],
                "date": "2026-04-20",
                "class_id": general["id"],
                "lines": [
                    {
                        "description": "Yamaha U1 upright piano",
                        "quantity": 1,
                        "fair_value": "6500",
                        "debit_account_id": seed_accounts["1500"].id,
                    }
                ],
            },
        )
    )
    ack = keeper.get(f"/api/donors/gifts/in-kind/{piano['id']}/acknowledgment/pdf")
    assert ack.content[:5] == b"%PDF-"
    # youth program spending, tagged to the grant
    bill = _ok(
        keeper.post(
            "/api/bills",
            json={
                "vendor_id": vendor["id"],
                "date": "2026-04-10",
                "terms": "Net 30",
                "lines": [
                    {
                        "account_id": supplies,
                        "description": "Sheet music",
                        "quantity": 1,
                        "rate": "1800",
                        "class_id": youth["id"],
                        "job_id": grant["id"],
                    },
                    {
                        "account_id": supplies,
                        "description": "Teaching artist",
                        "quantity": 1,
                        "rate": "1500",
                        "class_id": youth["id"],
                        "job_id": grant["id"],
                    },
                ],
            },
        )
    )
    assert Decimal(str(bill["total"])) == Decimal("3300")
    # shared rent, unassigned, then split by a saved rule at month end
    _ok(
        keeper.post(
            "/api/bills",
            json={
                "vendor_id": vendor["id"],
                "date": "2026-04-01",
                "terms": "Net 30",
                "class_id": general["id"],
                "lines": [
                    {
                        "account_id": rent,
                        "description": "April rent",
                        "quantity": 1,
                        "rate": "2000",
                        "function": None,  # unassigned until the rule runs
                    }
                ],
            },
        )
    )
    rule = _ok(
        keeper.post(
            "/api/nonprofit/allocation-rules",
            json={
                "name": "Rent by square footage",
                "basis": "square_feet",
                "source_account_id": rent,
                "targets": [
                    {"function": "program", "weight": 1400},
                    {"function": "management", "weight": 400},
                    {"function": "fundraising", "weight": 200},
                ],
            },
        )
    )
    split = _ok(
        keeper.get(f"/api/nonprofit/allocation-rules/{rule['id']}/split?amount=2000")
    )
    assert {ln["function"]: Decimal(ln["amount"]) for ln in split["lines"]} == {
        "program": Decimal("1400.00"),
        "management": Decimal("400.00"),
        "fundraising": Decimal("200.00"),
    }
    preview = _ok(
        keeper.get(
            f"/api/nonprofit/allocation-rules/{rule['id']}/preview?start_date=2026-04-01&end_date=2026-04-30"
        )
    )
    assert Decimal(preview["total"]) == Decimal("2000")
    run = _ok(
        keeper.post(
            "/api/nonprofit/allocations",
            json={
                "date": "2026-04-30",
                "rule_id": rule["id"],
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            },
        )
    )
    assert run["number"].startswith("FA-")
    assert (
        keeper.post(
            "/api/nonprofit/allocations",
            json={
                "date": "2026-04-30",
                "rule_id": rule["id"],
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            },
        ).status_code
        == 422
    )
    # release what the youth program spent
    suggest = _ok(
        keeper.get(
            f"/api/nonprofit/releases/suggest?class_id={youth['id']}&start_date=2026-01-01&end_date=2026-06-30"
        )
    )
    assert Decimal(suggest["suggested"]) == Decimal("3300")
    release = _ok(
        keeper.post(
            "/api/nonprofit/releases",
            json={
                "date": "2026-06-30",
                "class_id": youth["id"],
                "period_start": "2026-01-01",
                "period_end": "2026-06-30",
            },
        )
    )
    assert Decimal(release["amount"]) == Decimal("3300")
    # write off February, then change our mind
    cm = _ok(keeper.post(f"/api/invoices/{feb}/write-off", json={"date": "2026-06-30"}))
    assert cm["is_write_off"] is True
    _ok(keeper.post(f"/api/credit-memos/{cm['id']}/void"))
    assert Decimal(keeper.get(f"/api/invoices/{feb}").json()["balance_due"]) == Decimal(
        "100"
    )

    # ---- the treasurer's agent: every statement, reconciled to the cent ----
    qs = "start_date=2026-01-01&end_date=2026-06-30"
    soa = _ok(reader.get(f"/api/reports/statement-of-activities?{qs}"))
    pl = _ok(reader.get(f"/api/reports/profit-loss?{qs}"))
    t = soa["totals"]
    assert abs(t["change_total"] - pl["net_income"]) < 0.005
    assert soa["releases"]["without"] == 3300.0 and soa["releases"]["with"] == -3300.0
    # restricted revenue = grant + endowment; unrestricted = pledges 200 + ticket 150 + piano 6500
    # (the unapplied 500 is a donor credit on A/R until it meets a pledge — enter a
    # gift with no pledge as a Donation so it is revenue at once)
    assert t["revenue_with"] == 74000.0 and t["revenue_without"] == 6850.0
    assert t["change_with"] == 74000.0 - 3300.0
    sfe = _ok(reader.get(f"/api/reports/functional-expenses?{qs}"))
    assert (
        abs(sfe["totals"]["total"] - (pl["total_expenses"] + pl["total_cogs"])) < 0.005
    )
    assert sfe["totals"] == {
        "program": 3300.0 + 1400.0,
        "management": 400.0,
        "fundraising": 200.0,
        "unassigned": 0.0,
        "total": 5300.0,
    }
    sofp = _ok(
        reader.get("/api/reports/statement-of-financial-position?as_of_date=2026-06-30")
    )
    bs = _ok(reader.get("/api/reports/balance-sheet?as_of_date=2026-06-30"))
    assert (
        abs(
            sofp["total_assets"]
            - (
                sofp["total_liabilities"]
                + sofp["net_assets_without"]
                + sofp["net_assets_with"]
            )
        )
        < 0.005
    )
    assert abs(sofp["total_net_assets"] - bs["total_equity"]) < 0.005
    assert sofp["net_assets_with"] == 70700.0
    fb = _ok(reader.get(f"/api/reports/fund-balances?{qs}"))
    funds = {f["class_name"]: f for f in fb["funds"]}
    assert (
        funds["Youth Program"]["ending"] == 20700.0
        and funds["Scholarship Endowment"]["ending"] == 50000.0
    )
    assert abs(fb["totals"]["ending"] - sofp["net_assets_with"]) < 0.005
    pledges = _ok(reader.get(f"/api/reports/pledges?{qs}"))
    assert pledges["totals"] == {
        "pledged": 600.0,
        "invoiced": 200.0,
        "not_yet_invoiced": 400.0,
        "received": 100.0,
        "written_off": 0.0,
        "outstanding": 100.0,
    }
    byc = _ok(reader.get(f"/api/reports/profit-loss-by-class?{qs}"))
    assert abs(byc["total_net_income"] - pl["net_income"]) < 0.005
    for path, q in (
        ("statement-of-activities", qs),
        ("statement-of-financial-position", "as_of_date=2026-06-30"),
        ("fund-balances", qs),
        ("functional-expenses", qs),
        ("pledges", qs),
    ):
        p = reader.get(f"/api/reports/{path}/pdf?{q}")
        assert p.status_code == 200 and p.content[:5] == b"%PDF-", path
        c = reader.get(f"/api/reports/{path}/csv?{q}")
        assert c.status_code == 200 and c.headers["content-type"].startswith(
            "text/csv"
        ), path
        assert c.text.splitlines()[0]
        for line in c.text.splitlines():
            for cell in line.split(","):
                assert not cell or cell[0] not in "=+@", f"{path}: unsafe cell {cell!r}"
    pack = reader.get(f"/api/reports/financial-statements/pdf?{qs}")
    assert pack.content[:5] == b"%PDF-"
    # readonly cannot write anything it just read
    assert (
        reader.post(
            "/api/nonprofit/releases",
            json={"date": "2026-06-30", "class_id": youth["id"], "amount": "1"},
        ).status_code
        == 403
    )
    assert reader.post("/api/nonprofit/setup-accounts").status_code == 403
    assert (
        reader.post(
            f"/api/invoices/{feb}/write-off", json={"date": "2026-06-30"}
        ).status_code
        == 403
    )
    assert reader.post("/api/in-kind-gifts", json={}).status_code == 403

    # ---- year-end ------------------------------------------------------------
    one = reader.get(f"/api/donors/{maria['id']}/giving-statement/pdf?year=2026")
    assert one.content[:5] == b"%PDF-"
    every = reader.get("/api/donors/giving-statements/pdf?year=2026")
    assert every.content[:5] == b"%PDF-"
    import app.routes.donors as donors_routes

    sent = []
    monkeypatch.setattr(
        donors_routes,
        "send_email",
        lambda db, to_email, **kw: sent.append(to_email) or True,
    )
    batch = _ok(
        keeper.post("/api/donors/giving-statements/batch-email", json={"year": 2026})
    )
    # Maria and the Foundation have addresses; Hartwell has none; the guest opted out
    assert batch["sent"] == 2 and batch["failed"] == 1 and batch["skipped"] == 1
    assert sorted(sent) == ["grants@rccf.example", "maria@example.org"]

    # ---- switch back: words revert, numbers do not move ----------------------
    _ok(admin.put("/api/settings", json={"company_type": "business"}))
    assert (
        'filename="profit-loss_'
        in reader.get(f"/api/reports/profit-loss/pdf?{qs}").headers[
            "content-disposition"
        ]
    )
    assert (
        "SalesReceipt_"
        in reader.get(f"/api/invoices/{ticket['invoice']['id']}/pdf").headers[
            "content-disposition"
        ]
    )
    assert reader.get(f"/api/reports/profit-loss?{qs}").json() == pl
