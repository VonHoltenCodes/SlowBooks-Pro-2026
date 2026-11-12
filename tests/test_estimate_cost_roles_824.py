"""An estimate's cost side is the contractor's own (#824).

A read-only or accountant sign-in or API token reads an estimate (one, the
list, the print preview) without the cost side: no line's unit cost, no
package's cost, no cost of work and no budget cost, on any company type. A
bookkeeper and an administrator read everything as before. The fields are
one named set, ESTIMATE_COST_FIELDS; the job budget, commitments and the
schedule of values keep their own routes and role rules.
"""

import pytest

from app.models.users import User
from app.schemas.estimates import ESTIMATE_COST_FIELDS, without_cost_side
from app.services import auth as auth_service

USER_PW = "long-enough-pw-824"
HIDDEN = ("readonly", "accountant")


def _company(client, mode):
    r = client.put("/api/settings", json={"company_type": mode})
    assert r.status_code == 200, r.text


def _package(client, name):
    r = client.post("/api/trade-packages", json={"name": name, "section_ids": []})
    if r.status_code == 409:  # setup seeds some of the common names
        return next(
            p["id"]
            for p in client.get("/api/trade-packages").json()
            if p["name"] == name
        )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _estimate(client, customer_id, mode):
    if mode == "construction":
        concrete = _package(client, "Concrete")
        lines = [
            {
                "description": "Slab",
                "quantity": 1,
                "unit_cost": 4321.09,
                "trade_package_id": concrete,
            },
            # a line's own markup (#774) is cost side too
            {"description": "Fence", "quantity": 10, "unit_cost": 16, "markup_pct": 25},
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 500,
                "line_kind": "allowance",
            },
        ]
        extra = {"gc_pct": 8, "fee_pct": 5}
    else:
        lines = [
            {"description": "Cabinet", "quantity": 2, "rate": 900, "unit_cost": 432.17}
        ]
        extra = {}
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-03-01",
            "lines": lines,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _keys(data, found=None):
    """Every key anywhere in a JSON value."""
    found = set() if found is None else found
    if isinstance(data, dict):
        for k, v in data.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(data, list):
        for v in data:
            _keys(v, found)
    return found


def _users(db_session):
    for role in ("readonly", "accountant", "bookkeeper"):
        if not db_session.query(User).filter(User.username == role + "-824").first():
            db_session.add(
                User(
                    username=role + "-824",
                    display_name=role.title(),
                    password_hash=auth_service.hash_password(USER_PW),
                    role=role,
                    is_active=True,
                )
            )
    db_session.commit()


def _tokens(client):
    out = {}
    # a token is never an administrator; the bookkeeper's is the whole read
    for role in HIDDEN + ("bookkeeper",):
        r = client.post("/api/tokens", json={"label": f"agent-{role}", "role": role})
        assert r.status_code == 201, r.text
        out[role] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def _reads(client, est_id, headers=None):
    one = client.get(f"/api/estimates/{est_id}", headers=headers)
    listed = client.get("/api/estimates", headers=headers)
    preview = client.get(f"/api/estimates/{est_id}/print-preview", headers=headers)
    for r in (one, listed, preview):
        assert r.status_code == 200, r.text
    return one.json(), listed.json(), preview.text


@pytest.fixture(name="world")
def world_fixture(client, db_session, seed_customer, request):
    mode = request.param
    _company(client, mode)
    est = _estimate(client, seed_customer.id, mode)
    _users(db_session)
    tokens = _tokens(client)
    admin = _reads(client, est["id"])
    client.post("/api/auth/logout")
    return {"mode": mode, "est": est, "tokens": tokens, "admin": admin}


def _login(client, role):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": role + "-824", "password": USER_PW}
    )
    assert r.status_code == 200, r.text


MODES = ("business", "nonprofit", "construction")


def _assert_no_cost(one, listed, preview, mode):
    assert _keys(one) & ESTIMATE_COST_FIELDS == set(), _keys(one) & ESTIMATE_COST_FIELDS
    assert _keys(listed) & ESTIMATE_COST_FIELDS == set()
    assert listed and all(not (_keys(e) & ESTIMATE_COST_FIELDS) for e in listed)
    # the price side stays: rate, amount, the totals and the packages' names
    line = one["lines"][0]
    assert "rate" in line and "amount" in line
    assert one["total"] and one["subtotal"]
    if mode == "construction":
        t = one["totals"]
        assert t["bid_total"] and t["gc"] and t["fee"]
        assert [p["package_name"] for p in t["by_package"]] == [
            "Concrete",
            "Unassigned",
        ]
        assert all("price" in p for p in t["by_package"])
    # the print preview is the owner's PDF: a business line's unit cost is not
    # on it (a construction line is priced at cost, so its rate is printed)
    if mode != "construction":
        assert "432.17" not in preview


@pytest.mark.parametrize("world", MODES, indirect=True)
def test_the_administrator_reads_the_whole_estimate(world):
    one, listed, preview = world["admin"]
    assert "unit_cost" in one["lines"][0] and one["lines"][0]["unit_cost"] is not None
    assert "unit_cost" in listed[0]["lines"][0]
    if world["mode"] == "construction":
        t = one["totals"]
        assert {"budget_cost", "markup_base"} <= set(t)
        assert all("cost" in p for p in t["by_package"])


@pytest.mark.parametrize("world", MODES, indirect=True)
@pytest.mark.parametrize("role", HIDDEN)
def test_a_read_only_or_accountant_sign_in_reads_no_cost_side(client, world, role):
    _login(client, role)
    one, listed, preview = _reads(client, world["est"]["id"])
    _assert_no_cost(one, listed, preview, world["mode"])
    # everything else is the administrator's estimate, field for field
    assert one == without_cost_side(world["admin"][0])
    assert listed == without_cost_side(world["admin"][1])
    assert preview == world["admin"][2]


@pytest.mark.parametrize("world", MODES, indirect=True)
@pytest.mark.parametrize("role", HIDDEN)
def test_a_read_only_or_accountant_token_reads_no_cost_side(client, world, role):
    one, listed, preview = _reads(
        client, world["est"]["id"], headers=world["tokens"][role]
    )
    _assert_no_cost(one, listed, preview, world["mode"])
    assert one == without_cost_side(world["admin"][0])
    assert listed == without_cost_side(world["admin"][1])


@pytest.mark.parametrize("world", MODES, indirect=True)
def test_a_bookkeeper_sign_in_and_its_token_read_everything(client, world):
    _login(client, "bookkeeper")
    session = _reads(client, world["est"]["id"])
    client.post("/api/auth/logout")
    token = _reads(client, world["est"]["id"], headers=world["tokens"]["bookkeeper"])
    for one, listed, preview in (session, token):
        assert one == world["admin"][0]
        assert listed == world["admin"][1]
        assert preview == world["admin"][2]


def test_the_print_preview_prints_no_cost_figure(client, seed_customer):
    """The preview is the owner's document for every role: a business line's
    unit cost (432.17) is not on it, nor a construction bid's budget cost."""
    est = _estimate(client, seed_customer.id, "business")
    html = client.get(f"/api/estimates/{est['id']}/print-preview").text
    assert "432.17" not in html and "864.34" not in html
    assert "900.00" in html
    _company(client, "construction")
    est = _estimate(client, seed_customer.id, "construction")
    budget = client.get(f"/api/estimates/{est['id']}").json()["totals"]["budget_cost"]
    html = client.get(f"/api/estimates/{est['id']}/print-preview").text
    assert f"{float(budget):,.2f}" not in html


def test_the_cost_side_is_one_named_set():
    assert ESTIMATE_COST_FIELDS == {
        "unit_cost",
        "cost",
        "markup_base",
        "budget_cost",
        "markup_pct",
        "cost_total",
        "profit",
        "margin_pct",
    }
    shaped = without_cost_side(
        {
            "id": 1,
            "lines": [{"rate": "5", "unit_cost": "3"}],
            "totals": {
                "budget_cost": "3",
                "markup_base": "3",
                "bid_total": "5",
                "by_package": [{"package_name": "A", "cost": "3", "price": "5"}],
            },
        }
    )
    assert shaped == {
        "id": 1,
        "lines": [{"rate": "5"}],
        "totals": {
            "bid_total": "5",
            "by_package": [{"package_name": "A", "price": "5"}],
        },
    }
