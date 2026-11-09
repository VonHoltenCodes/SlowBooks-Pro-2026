"""Construction mode — the contractor accounts, the cost codes, and the
one-time setup the Settings page runs when a company switches.

Mirrors the nonprofit setup checks: the accounts are idempotent and yield
already-used numbers; the setup route leaves existing data alone and
changes nothing on a second call."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.accounting import CONSTRUCTION_ACCOUNTS
from app.services.cost_code_catalog import (
    DIVISIONS,
    SECTIONS,
    TRADE_PACKAGES,
    normalize_code,
)

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app/static/js"


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def test_construction_accounts_detail_types_are_right(client, seed_accounts):
    r = client.post("/api/construction/setup")
    assert r.status_code == 200, r.text
    by_name = {a["name"]: a for a in r.json()}
    # the number-and-name rule would call these Other Current / COGS anyway,
    # but the setup pins them so the balance sheet never drifts (#431)
    assert by_name["Retainage Receivable"]["detail_type"] == "other_current_asset"
    assert by_name["Construction in Progress"]["detail_type"] == "other_current_asset"
    assert by_name["Retainage Payable"]["detail_type"] == "other_current_liability"
    assert by_name["Customer Deposits"]["detail_type"] == "other_current_liability"
    assert by_name["Use Tax Payable"]["detail_type"] == "other_current_liability"
    assert by_name["Equipment Cost"]["detail_type"] == "cost_of_goods_sold"
    assert by_name["Other Job Costs"]["detail_type"] == "cost_of_goods_sold"
    assert all(a["is_system"] for a in r.json())


def test_construction_accounts_are_idempotent_and_yield_taken_numbers(
    client, seed_accounts
):
    # 1350 is free in the seed chart; take 1150 first to prove the yield.
    r = client.post(
        "/api/accounts",
        json={
            "name": "Old Retainage",
            "account_number": "1150",
            "account_type": "asset",
        },
    )
    assert r.status_code in (200, 201), r.text

    first = client.post("/api/construction/setup")
    assert first.status_code == 200, first.text
    by_name = {a["name"]: a for a in first.json()}
    assert by_name["Retainage Receivable"]["account_number"] is None  # yielded
    assert by_name["Construction in Progress"]["account_number"] == "1350"
    assert by_name["Equipment Cost"]["account_number"] == "5400"

    # a second pass creates nothing new
    second = client.post("/api/construction/setup")
    assert [a["id"] for a in second.json()] == [a["id"] for a in first.json()]
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert names.count("Retainage Receivable") == 1


# ---------------------------------------------------------------------------
# Setup route
# ---------------------------------------------------------------------------


def test_setup_loads_accounts_and_cost_codes_and_repeats_cleanly(client, seed_accounts):
    first = client.post("/api/construction/setup")
    assert first.status_code == 200
    names = {a["name"] for a in first.json()}
    assert names == {name for _n, name, _t, _d in CONSTRUCTION_ACCOUNTS}

    codes = {c["code"] for c in client.get("/api/cost-codes").json()}
    # every standard division and common section lands
    for code, _name in DIVISIONS:
        assert code in codes, code
    for raw, _name in SECTIONS:
        assert normalize_code(raw) in codes, raw
    # the default trade packages are seeded, mapped to sections
    pkgs = client.get("/api/trade-packages").json()
    assert len(pkgs) == len(TRADE_PACKAGES)
    concrete = next(p for p in pkgs if p["name"] == "Concrete")
    assert "03 30 00" in {s["code"] for s in concrete["sections"]}

    # second call: same accounts, no duplicate cost codes, no duplicate packages
    accounts_before = client.get("/api/accounts").json()
    codes_before = client.get("/api/cost-codes").json()
    second = client.post("/api/construction/setup")
    assert second.status_code == 200
    assert len(client.get("/api/accounts").json()) == len(accounts_before)
    assert len(client.get("/api/cost-codes").json()) == len(codes_before)
    assert len(client.get("/api/trade-packages").json()) == len(TRADE_PACKAGES)


def test_setup_creates_the_cost_types_the_codes_reference(client, seed_accounts):
    client.post("/api/construction/setup")
    # seeded standard codes all default to "other"
    rows = client.get("/api/cost-codes").json()
    assert {c["cost_type"] for c in rows} == {"other"}
    # the five cost types exist for a per-line type to point at
    types = {t["code"] for t in client.get("/api/cost-types").json()}
    assert {"subcontract", "material", "labor", "equipment", "other"} <= types


# ---------------------------------------------------------------------------
# Switch points (behaviour) — the three new JS branches, driven through
# their node probes rather than source-grepped.
# ---------------------------------------------------------------------------


requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _probe(name, *args):
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / name), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@requires_node
def test_report_center_leads_with_contractor_cards_and_keeps_the_set():
    got = _probe("reports_cards_probe.js")
    # the Construction group leads: the three contractor reports that always
    # existed, then the seven job reports added in #556, the Field Ticket Log
    # and the retainage and sub compliance reports (construction only)
    construction_only = [
        "wip-schedule",
        "cost-to-complete",
        "change-order-log",
        "buyout",
        "job-cost-by-vendor",
        "cost-to-complete-forecast",
        "commitment-status",
        "field-ticket-log",
        "retainage",
        "compliance",
    ]
    assert (
        got["construction"][:13]
        == [
            "job-profitability",
            "job-budget-vs-actual",
            "profit-loss-by-job",
        ]
        + construction_only
    )
    # the five show in construction mode only; everything else is the business
    # set, unchanged
    assert set(got["construction"]) - set(got["business"]) == set(construction_only)
    assert all(v not in got["business"] for v in construction_only)
    assert len(got["construction"]) == len(got["business"]) + len(construction_only)


@requires_node
def test_vendor_form_reveals_subs_and_a_sub_defaults_to_1099_nec():
    got = _probe("vendor_1099_probe.js")
    # a new vendor in construction mode shows the Sub section, fields hidden
    assert 'name="vendor_type"' in got["subNew"]
    for name in ("license_number", "coi_carrier", "coi_policy_number", "coi_expires"):
        assert f'name="{name}"' in got["subNew"]
    assert got["subNew"].count('sub-field" hidden') == 4
    # an existing sub shows them, and an unsafe COI value is escaped
    assert got["subEdit"].count('sub-field" hidden') == 0
    assert "<script>x" not in got["subEdit"]
    # marking a vendor a Sub turns on 1099-NEC and reveals the four fields
    assert got["subToggle"]["on"]["is_1099_vendor"] == "true"
    assert got["subToggle"]["on"]["vendor_1099_type"] == "NEC"
    assert got["subToggle"]["on"]["hidden"] == [False, False, False, False]
    # turning it off hides and clears them (a hidden input still submits)
    assert got["subToggle"]["off"]["hidden"] == [True, True, True, True]
    assert got["subToggle"]["off"]["cleared"] == ["", "", "", ""]
    # a Sub already on 1099-MISC keeps MISC, not switched to NEC silently
    assert got["subToggleMisc"]["is_1099_vendor"] == "true"
    assert got["subToggleMisc"]["vendor_1099_type"] == "MISC"


@requires_node
def test_construction_route_guard_shows_the_placeholder_for_a_business():
    business = _probe("app_routes_probe.js", '["#/__ctest"]')["#/__ctest"]
    assert "construction companies" in business["page"]
    assert "CTest" not in business["page"]
    con = _probe("app_routes_probe.js", '["#/__ctest"]', "--construction")["#/__ctest"]
    assert "CTest" in con["page"]
    assert "construction companies" not in con["page"]


def test_any_construction_nav_item_is_hidden_by_default():
    """NEW-145 invariant: a construction-only sidebar page must ship with
    `hidden` so the Sidebar picker leaves it out of other company types.
    This phase adds none, so this guards future additions."""
    import re

    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<li\b[^>]*\bdata-construction\b[^>]*>", html):
        assert "hidden" in m.group(0), m.group(0)


def test_settings_offers_construction_and_runs_setup_on_switch():
    # the switch flow has no probe of its own; this keeps the wiring honest
    s = (JS / "settings.js").read_text(encoding="utf-8")
    assert '<option value="construction"' in s
    assert "/construction/setup" in s


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _message(r) -> str:
    """A 422's sentence, from an HTTPException detail or a schema error."""
    body = r.json()
    detail = body.get("detail")
    if isinstance(detail, list):
        return " ".join(e.get("message", "") for e in detail)
    return detail if isinstance(detail, str) else str(detail)


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


@pytest.fixture
def con(client):
    _construction(client)
    return client


def test_validation_sentences_name_the_field_and_its_choices_by_label(
    con, seed_customer
):
    """ "Sales_tax_treatment must be charge_owner …" and "Line 1:
    Line_type must be one of labor, equipment, material, ot…"."""
    job = _job(con, seed_customer.id)
    r = con.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": "owner"}
    )
    assert r.status_code == 422, r.text
    assert _message(r) == (
        "Sales tax must be Charge sales tax to the owner or Contractor pays tax "
        "on materials."
    )
    assert "sales_tax_treatment is one of" in r.json()["detail"][0]["msg"]
    r = con.post(
        "/api/field-tickets",
        json={"job_id": job["id"], "lines": [{"line_type": "labour", "quantity": 1}]},
    )
    assert r.status_code == 422, r.text
    assert (
        _message(r) == "Line 1: Line type must be Labor, Equipment, Material or Other."
    )
    for url, body, label in (
        ("/api/field-tickets", {"job_id": job["id"], "kind": "slip"}, "Kind"),
        ("/api/change-orders", {"job_id": job["id"], "funding": "x"}, "Funding"),
        (
            f"/api/jobs/{job['id']}/contract",
            {"contract_type": "fixed"},
            "Contract type",
        ),
        (f"/api/jobs/{job['id']}/contract", {"retainage_pct": 150}, "Retainage"),
    ):
        r = con.request("PUT" if "contract" in url else "POST", url, json=body)
        assert r.status_code == 422, (url, r.text)
        message = _message(r)
        assert message.startswith(label + " must be"), message
        assert "_" not in message, message
