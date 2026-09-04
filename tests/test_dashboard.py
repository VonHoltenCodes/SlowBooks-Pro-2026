"""Customizable dashboard: widget catalog, per-widget data, and the
per-user layout preference (operator session vs named users)."""

from datetime import date, timedelta

from app.models.accounts import Account, AccountType
from app.services.dashboard_widgets import DEFAULT_LAYOUT, WIDGETS


def test_catalog_lists_every_widget_and_the_classic_default(client):
    body = client.get("/api/dashboard/widgets").json()
    ids = {w["id"] for w in body["widgets"]}
    assert ids == set(WIDGETS)
    assert body["default_order"] == list(DEFAULT_LAYOUT)
    assert {
        "job_budget_vs_actual",
        "cash_position",
        "pnl_month",
        "open_pos",
        "receipts_review",
    } <= ids
    for w in body["widgets"]:
        assert w["size"] in ("stat", "half", "full") and w["title"] and w["description"]


def test_every_widget_builds_on_an_empty_company(client, seed_accounts):
    resp = client.get("/api/dashboard/data?ids=" + ",".join(WIDGETS))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == set(WIDGETS)
    for wid, payload in data.items():
        assert "error" not in payload, f"{wid}: {payload.get('error')}"
    assert data["receivables"] == {"total": 0.0, "overdue_count": 0}
    assert len(data["monthly_revenue"]["months"]) == 12
    assert data["cash_position"]["forecast_30"] == data["cash_position"]["cash"]
    assert data["job_budget_vs_actual"]["count"] == 0


def test_data_ignores_unknown_ids_and_defaults_when_empty(client, seed_accounts):
    assert set(client.get("/api/dashboard/data?ids=receivables,bogus").json()) == {
        "receivables"
    }
    assert set(client.get("/api/dashboard/data").json()) == set(DEFAULT_LAYOUT)


def test_widgets_reflect_activity(client, db_session, seed_accounts, seed_customer):
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .first()
    )
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    today = date.today()
    # an overdue invoice, a bill due next week, an open PO on a budgeted job
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": (today - timedelta(days=45)).isoformat(),
            "due_date": (today - timedelta(days=15)).isoformat(),
            "lines": [{"description": "work", "quantity": 1, "rate": 400}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    vendor = client.post("/api/vendors", json={"name": "V"}).json()
    client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-1",
            "date": today.isoformat(),
            "terms": "Net 15",
            "lines": [{"account_id": expense.id, "quantity": 1, "rate": 150}],
        },
    )
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Shed"}
    ).json()
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_type": "other", "amount": 1000}]},
    )
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": today.isoformat(),
            "job_id": job["id"],
            "lines": [{"description": "lumber", "quantity": 1, "rate": 300}],
        },
    ).json()
    client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    client.post(
        "/api/journal",
        json={
            "date": today.isoformat(),
            "description": "cash sale",
            "lines": [
                {"account_id": expense.id, "debit": 50, "credit": 0},
                {"account_id": income.id, "debit": 0, "credit": 50},
            ],
        },
    )

    d = client.get(
        "/api/dashboard/data?ids=receivables,overdue_invoices,payables,open_pos,job_budget_vs_actual,pnl_month,cash_position,monthly_revenue"
    ).json()
    assert d["receivables"] == {"total": 400.0, "overdue_count": 1}
    assert (
        d["overdue_invoices"]["count"] == 1
        and d["overdue_invoices"]["items"][0]["days_overdue"] == 15
    )
    assert d["payables"]["total"] == 150.0 and d["payables"]["overdue_count"] == 0
    assert d["open_pos"]["count"] == 1 and d["open_pos"]["total"] == 300.0
    assert d["open_pos"]["items"][0]["job"].endswith("Shed")
    jb = d["job_budget_vs_actual"]
    assert (
        jb["count"] == 1
        and jb["items"][0]["committed"] == 300.0
        and jb["items"][0]["variance"] == 700.0
    )
    # the invoice was dated 45 days ago, so only the journal credit is this month
    assert d["pnl_month"]["this_month"]["income"] == 50.0
    assert d["pnl_month"]["this_month"]["expenses"] == 200.0
    assert (
        d["cash_position"]["ar_due_30"] == 400.0
        and d["cash_position"]["ap_due_30"] == 150.0
    )
    assert d["cash_position"]["forecast_30"] == d["cash_position"]["cash"] + 250.0
    assert d["monthly_revenue"]["months"][-1]["amount"] == 50.0
    assert sum(m["amount"] for m in d["monthly_revenue"]["months"]) == 450.0


def test_layout_preference_round_trips_and_resets(client):
    assert client.get("/api/preferences/dashboard").json() == {
        "key": "dashboard",
        "value": None,
    }
    saved = client.put(
        "/api/preferences/dashboard",
        json={"value": {"order": ["payables", "cash_position"]}},
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/preferences/dashboard").json()["value"] == {
        "order": ["payables", "cash_position"]
    }
    # replace, not merge
    client.put("/api/preferences/dashboard", json={"value": {"order": ["receivables"]}})
    assert client.get("/api/preferences/dashboard").json()["value"]["order"] == [
        "receivables"
    ]
    assert (
        client.put("/api/preferences/dashboard", json={"value": "nope"}).status_code
        == 422
    )
    assert client.put("/api/preferences/Bad Key", json={"value": {}}).status_code == 422
    assert client.delete("/api/preferences/dashboard").status_code == 200
    assert client.get("/api/preferences/dashboard").json()["value"] is None


def test_layout_is_per_user_once_users_exist(client, db_session):
    """The operator session's layout and a named user's layout are
    separate rows; a second user starts from the default."""
    # operator (single-password session) saves a layout
    client.put("/api/preferences/dashboard", json={"value": {"order": ["payables"]}})
    # create two users and log in as each
    a = client.post(
        "/api/users",
        json={
            "username": "ann",
            "display_name": "Ann",
            "password": "pw-ann-123456",
            "role": "bookkeeper",
        },
    )
    b = client.post(
        "/api/users",
        json={
            "username": "bob",
            "display_name": "Bob",
            "password": "pw-bob-123456",
            "role": "bookkeeper",
        },
    )
    assert a.status_code in (200, 201), a.text
    assert b.status_code in (200, 201), b.text
    assert client.post("/api/auth/logout").status_code == 200
    login = client.post(
        "/api/auth/login", json={"username": "ann", "password": "pw-ann-123456"}
    )
    assert login.status_code == 200, login.text
    assert (
        client.get("/api/preferences/dashboard").json()["value"] is None
    )  # not the operator's
    client.put(
        "/api/preferences/dashboard",
        json={"value": {"order": ["cash_position", "receivables"]}},
    )
    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"username": "bob", "password": "pw-bob-123456"}
        ).status_code
        == 200
    )
    assert client.get("/api/preferences/dashboard").json()["value"] is None
    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"username": "ann", "password": "pw-ann-123456"}
        ).status_code
        == 200
    )
    assert client.get("/api/preferences/dashboard").json()["value"]["order"] == [
        "cash_position",
        "receivables",
    ]
