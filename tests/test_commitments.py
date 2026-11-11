"""Buyout and commitments (#553): a commitment is a purchase order — a plain
PO or a subcontract — whose lines are its schedule of values and which bills
in parts, optionally holding retainage. Committed cost becomes the remaining
(unbilled) open commitment, with committed_total and billed_to_date alongside.
The buyout endpoint pairs each package's bid against what was bought, and the
bills carry the Sub's compliance flags and a lien-waiver status.

Later issues add sub change orders (#554), progress billing / retainage
release (#555) and the buyout report / job cost by vendor (#556); this one
leaves those seams.
"""

import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.models.bills import Bill, BillLine
from app.models.change_orders import ChangeOrder
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.models.transactions import TransactionLine
from app.services import job_reports
from app.services.job_costing import commitment_lines
from tests.test_change_orders import _co, _sent_commitment, _sub_line, _submit_approve
from tests.test_change_orders import _job as _change_job
from tests.test_change_orders import _vendor as _change_vendor

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum(
        (Decimal(str(r.debit)) - Decimal(str(r.credit)) for r in rows), Decimal("0")
    )


def _retainage_account(db):
    return db.query(Account).filter(Account.name == "Retainage Payable").first()


def _vendor(client, name="Ace Concrete", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name):
    r = client.post("/api/trade-packages", json={"name": name, "section_ids": []})
    assert r.status_code == 201, r.text
    return r.json()


def _po(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-09-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/purchase-orders", json=body)
    return r


def _sent_po(client, vendor_id, lines, **extra):
    """A commitment that has been issued — a PO only counts as committed once
    it has left draft."""
    po = _po(client, vendor_id, lines, **extra).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def _convert(client, po_id, **body):
    return client.post(
        f"/api/purchase-orders/{po_id}/convert-to-bill", json=(body or None)
    )


SUB_LINES = [
    {"description": "Foundations", "quantity": 1, "rate": 10000},
    {"description": "Slab", "quantity": 1, "rate": 5000},
]


# ── PO create/update with the commitment fields ──────────────────────────


@pytest.mark.parametrize("company_type", ["business", "construction"])
def test_po_accepts_commitment_fields_in_both_company_types(
    client, seed_customer, company_type
):
    assert (
        client.put("/api/settings", json={"company_type": company_type}).status_code
        == 200
    )
    pkg = _package(client, "Concrete")
    v = _vendor(client, vendor_type="sub")
    r = _po(
        client,
        v["id"],
        SUB_LINES,
        commitment_type="subcontract",
        trade_package_id=pkg["id"],
        retainage_pct=10,
        scope_notes="Per plans A-1..A-9",
    )
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["commitment_type"] == "subcontract"
    assert po["trade_package_id"] == pkg["id"]
    assert po["trade_package_name"] == "Concrete"
    assert Decimal(po["retainage_pct"]) == Decimal("10.00")
    assert po["scope_notes"] == "Per plans A-1..A-9"
    # every line starts with nothing billed
    assert all(Decimal(ln["billed_amount"]) == 0 for ln in po["lines"])


def test_po_defaults_keep_todays_behaviour(client, seed_customer):
    v = _vendor(client)
    po = _po(client, v["id"], SUB_LINES).json()
    assert po["commitment_type"] == "po"
    assert po["trade_package_id"] is None
    assert po["retainage_pct"] is None

    up = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={"commitment_type": "subcontract", "retainage_pct": 5},
    )
    assert up.status_code == 200, up.text
    assert up.json()["commitment_type"] == "subcontract"


# ── Partial billing ──────────────────────────────────────────────────────


def test_two_bills_from_one_po_partial_then_closed(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, job_id=job["id"]).json()
    ids = [ln["id"] for ln in po["lines"]]

    # First period: $4,000 of the $10,000 foundations line only.
    r1 = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])
    assert r1.status_code == 200, r1.text
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value == "partial"
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    assert Decimal(str(line0.billed_amount)) == Decimal("4000.00")
    bill1 = db_session.get(Bill, r1.json()["bill_id"])
    assert Decimal(str(bill1.total)) == Decimal("4000.00")
    # the bill line links back to the commitment line
    bl = db_session.query(BillLine).filter_by(bill_id=bill1.id).one()
    assert bl.purchase_order_line_id == ids[0]

    # Second period: everything that remains (both lines), default amounts.
    r2 = _convert(client, po["id"])
    assert r2.status_code == 200, r2.text
    db_session.refresh(po_row)
    assert po_row.status.value == "closed"
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("10000.00")
    bill2 = db_session.get(Bill, r2.json()["bill_id"])
    assert Decimal(str(bill2.total)) == Decimal("11000.00")  # 6000 + 5000


def test_over_remaining_is_422_and_nothing_left_is_409(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES).json()
    ids = [ln["id"] for ln in po["lines"]]

    over = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 99999}])
    assert over.status_code == 422, over.text

    # Every line zero (the construction dialog sends a zero per line) bills
    # nothing → 409, not a 422.
    zero = _convert(
        client,
        po["id"],
        lines=[{"line_id": ids[0], "amount": 0}, {"line_id": ids[1], "amount": 0}],
    )
    assert zero.status_code == 409, zero.text

    # Bill it all, then a second convert finds nothing to bill.
    done = _convert(client, po["id"])
    assert done.status_code == 200, done.text
    again = _convert(client, po["id"])
    # a closed PO is refused up front; a partial with nothing left is the 409
    assert again.status_code in (400, 409), again.text


def test_zeroed_convert_dialog_bills_nothing(
    client, db_session, seed_accounts, seed_customer
):
    """The construction dialog sends {line_id, amount} for every line; an
    all-zero submit is a partial request that bills nothing — 409, and the
    commitment is untouched."""
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES).json()
    ids = [ln["id"] for ln in po["lines"]]
    r = _convert(
        client,
        po["id"],
        lines=[{"line_id": i, "amount": 0} for i in ids],
    )
    assert r.status_code == 409, r.text
    assert db_session.query(Bill).count() == 0


def test_put_po_keeps_billed_and_refuses_removing_a_billed_line(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES, job_id=job["id"])
    ids = [ln["id"] for ln in po["lines"]]
    _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])

    # Edit the scope notes, resending both lines by id: billed-to-date and the
    # committed figures survive.
    up = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={
            "scope_notes": "revised",
            "lines": [
                {
                    "id": ids[0],
                    "description": "Foundations",
                    "quantity": 1,
                    "rate": 10000,
                },
                {"id": ids[1], "description": "Slab", "quantity": 1, "rate": 5000},
            ],
        },
    )
    assert up.status_code == 200, up.text
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("4000.00")
    detail = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert detail["billed_to_date"] == 4000.0
    assert detail["committed_cost"] == 11000.0

    # Dropping the billed line is a sub-change-order move → 409.
    drop = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={
            "lines": [
                {"id": ids[1], "description": "Slab", "quantity": 1, "rate": 5000}
            ]
        },
    )
    assert drop.status_code == 409, drop.text
    assert drop.json()["detail"]["code"] == "commitment_billed"

    # Cutting a billed line below what's billed is also refused.
    cut = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={
            "lines": [
                {
                    "id": ids[0],
                    "description": "Foundations",
                    "quantity": 1,
                    "rate": 1000,
                },
                {"id": ids[1], "description": "Slab", "quantity": 1, "rate": 5000},
            ]
        },
    )
    assert cut.status_code == 409, cut.text


# ── Retainage posting ─────────────────────────────────────────────────────


def test_retainage_splits_ap_and_2050_with_exact_decimals(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10).json()
    r = _convert(client, po["id"])
    assert r.status_code == 200, r.text
    bill = db_session.get(Bill, r.json()["bill_id"])
    # subtotal 15,000; 10% held
    assert Decimal(str(bill.retainage_held)) == Decimal("1500.00")
    assert Decimal(str(bill.balance_due)) == Decimal("13500.00")
    ap = seed_accounts["2000"].id
    assert _gl(db_session, ap) == Decimal("-13500.00")  # A/P credited net
    ret = _retainage_account(db_session)
    assert ret is not None
    assert _gl(db_session, ret.id) == Decimal("-1500.00")  # 2050 credited


def test_void_restores_billed_amount_and_reverses_2050(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10).json()
    ids = [ln["id"] for ln in po["lines"]]
    r = _convert(client, po["id"])
    bill_id = r.json()["bill_id"]

    vr = client.post(f"/api/bills/{bill_id}/void")
    assert vr.status_code == 200, vr.text
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("0.00")
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value != "closed"
    # 2050 nets to zero after the reversing entry
    ret = _retainage_account(db_session)
    assert _gl(db_session, ret.id) == Decimal("0.00")


def test_hand_entered_bill_carries_retainage(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-10",
            "retainage_held": 500,
            "lines": [
                {
                    "description": "Framing draw",
                    "amount": 5000,
                    "account_id": seed_accounts["5100"].id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert Decimal(bill["retainage_held"]) == Decimal("500.00")
    assert Decimal(bill["balance_due"]) == Decimal("4500.00")
    ret = _retainage_account(db_session)
    assert _gl(db_session, ret.id) == Decimal("-500.00")

    # over the subtotal is refused
    bad = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-11",
            "retainage_held": 99999,
            "lines": [{"description": "x", "amount": 100}],
        },
    )
    assert bad.status_code == 422, bad.text


# ── Committed / billed figures ────────────────────────────────────────────


def test_committed_and_billed_figures_track_partial_billing(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES, job_id=job["id"])
    assert po["status"] == "sent"
    ids = [ln["id"] for ln in po["lines"]]

    # Before billing: committed is the whole $15,000 open, nothing billed.
    detail = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert detail["committed_cost"] == 15000.0
    assert detail["committed_total"] == 15000.0
    assert detail["billed_to_date"] == 0.0
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert tree["committed"] == 15000.0
    assert tree["committed_total"] == 15000.0
    assert tree["billed_to_date"] == 0.0

    # Bill $4,000 of the foundations line.
    _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])
    detail = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert detail["committed_cost"] == 11000.0  # remaining open
    assert detail["committed_total"] == 15000.0  # the whole commitment
    assert detail["billed_to_date"] == 4000.0
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert tree["committed"] == 11000.0
    assert tree["billed_to_date"] == 4000.0


# ── Buyout endpoint ────────────────────────────────────────────────────────


def _accepted_two_package_estimate(client, customer_id, job_id):
    pkg_c = _package(client, "Concrete")
    pkg_e = _package(client, "Electrical")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-02-01",
            "job_id": job_id,
            "lines": [
                {
                    "description": "Concrete",
                    "quantity": 1,
                    "rate": 16000,
                    "unit_cost": 14000,
                    "trade_package_id": pkg_c["id"],
                },
                {
                    "description": "Electrical",
                    "quantity": 1,
                    "rate": 9000,
                    "unit_cost": 8000,
                    "trade_package_id": pkg_e["id"],
                },
            ],
        },
    ).json()
    acc = client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job_id})
    assert acc.status_code == 200, acc.text
    return pkg_c, pkg_e


def _buyout(client, job_id):
    """The job's buyout, which is construction mode's, as its report is."""
    client.put("/api/settings", json={"company_type": "construction"})
    return client.get(f"/api/jobs/{job_id}/buyout").json()


def test_buyout_rows_and_totals(client, db_session, seed_accounts, seed_customer):
    job = _job(client, seed_customer.id)
    pkg_c, pkg_e = _accepted_two_package_estimate(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)

    # Concrete bought in full at 13,000 (under the 14,000 bid), billed partly.
    po_c = _sent_po(
        client,
        v["id"],
        [{"description": "Concrete sub", "quantity": 1, "rate": 13000}],
        job_id=job["id"],
        commitment_type="subcontract",
        trade_package_id=pkg_c["id"],
    )
    _convert(
        client, po_c["id"], lines=[{"line_id": po_c["lines"][0]["id"], "amount": 5000}]
    )

    # Electrical bought partly (4,000 of the 8,000 bid), nothing billed.
    _sent_po(
        client,
        v["id"],
        [{"description": "Elec sub", "quantity": 1, "rate": 4000}],
        job_id=job["id"],
        commitment_type="subcontract",
        trade_package_id=pkg_e["id"],
    )

    # An unassigned commitment (no package).
    _sent_po(
        client,
        v["id"],
        [{"description": "General conditions", "quantity": 1, "rate": 1000}],
        job_id=job["id"],
    )

    data = _buyout(client, job["id"])
    rows = {r["package_name"]: r for r in data["rows"]}

    assert rows["Concrete"]["bid_cost"] == 14000.0
    assert rows["Concrete"]["bought"] == 13000.0
    assert rows["Concrete"]["variance"] == 1000.0
    assert rows["Concrete"]["committed_remaining"] == 8000.0  # 13000 - 5000 billed
    assert rows["Concrete"]["billed_to_date"] == 5000.0
    assert len(rows["Concrete"]["commitments"]) == 1

    assert rows["Electrical"]["bid_cost"] == 8000.0
    assert rows["Electrical"]["bought"] == 4000.0
    assert rows["Electrical"]["variance"] == 4000.0

    # Unassigned gathers the commitment with no package.
    assert rows["Unassigned"]["bought"] == 1000.0

    totals = data["totals"]
    assert totals["bid_cost"] == 22000.0  # 14000 + 8000
    assert totals["bought"] == 18000.0  # 13000 + 4000 + 1000


# ── Vendor compliance + lien waiver ───────────────────────────────────────


def test_vendor_compliance_flags_a_sub(client, db_session, seed_accounts):
    expired = (date.today() - timedelta(days=1)).isoformat()
    sub = _vendor(
        client,
        name="Ace Electric",
        vendor_type="sub",
        coi_expires=expired,
        default_expense_account_id=seed_accounts["5100"].id,
    )
    plain = _vendor(
        client,
        name="Lumber Yard",
        default_expense_account_id=seed_accounts["5100"].id,
    )

    r = client.post(
        "/api/bills",
        json={
            "vendor_id": sub["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 100}],
        },
    )
    comp = r.json()["vendor_compliance"]
    assert comp["is_sub"] is True
    assert comp["w9_on_file"] is False
    assert comp["coi_expired"] is True

    r2 = client.post(
        "/api/bills",
        json={
            "vendor_id": plain["id"],
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "paper",
                    "amount": 50,
                    "account_id": seed_accounts["5100"].id,
                }
            ],
        },
    )
    comp2 = r2.json()["vendor_compliance"]
    assert comp2["is_sub"] is False
    assert comp2["coi_expired"] is False


def test_lien_waiver_put_round_trip(client, seed_accounts):
    v = _vendor(
        client,
        name="Ace",
        vendor_type="sub",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 100}],
        },
    ).json()
    assert bill["lien_waiver_status"] is None
    up = client.put(
        f"/api/bills/{bill['id']}", json={"lien_waiver_status": "conditional"}
    )
    assert up.status_code == 200, up.text
    assert up.json()["lien_waiver_status"] == "conditional"
    # a bad value is refused
    bad = client.put(f"/api/bills/{bill['id']}", json={"lien_waiver_status": "nope"})
    assert bad.status_code == 422, bad.text


# ── Edit / void / status edge cases ───────────────────────────────────────


def test_edit_po_bill_then_void_restores_billed_amount(
    client, db_session, seed_accounts, seed_customer
):
    """A PO bill carries a currency + rate (so the edit doesn't 500), the edit
    keeps the commitment back-links, and the later void hands the billed
    dollars back to the commitment."""
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    ids = [ln["id"] for ln in po["lines"]]
    conv = _convert(client, po["id"])
    bill_id = conv.json()["bill_id"]

    # Edit the tax rate — this re-posts the journal through create's path.
    up = client.put(f"/api/bills/{bill_id}", json={"tax_rate": 0.05})
    assert up.status_code == 200, up.text

    vr = client.post(f"/api/bills/{bill_id}/void")
    assert vr.status_code == 200, vr.text
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("0.00")


def test_paying_a_retainage_bill_in_full_marks_paid_and_aging_nets_held(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10).json()
    bill_id = _convert(client, po["id"]).json()["bill_id"]
    bill = db_session.get(Bill, bill_id)
    net = Decimal(str(bill.balance_due))
    assert net == Decimal("13500.00")

    # A/P aging excludes the held retainage (it is on 2050, not A/P).
    from app.routes.reports.payables_tax import ap_aging_report

    aging = ap_aging_report(db_session, date(2026, 12, 31))
    row = next(r for r in aging["items"] if r["vendor_id"] == v["id"])
    assert Decimal(str(row["total"])) == float(net)

    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-15",
            "amount": float(net),
            "allocations": [{"bill_id": bill_id, "amount": float(net)}],
        },
    )
    assert pay.status_code == 201, pay.text
    got = client.get(f"/api/bills/{bill_id}").json()
    assert got["status"] == "paid"


def test_raising_retainage_on_a_paid_bill_is_422(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES).json()  # no retainage
    bill_id = _convert(client, po["id"]).json()["bill_id"]
    # pay it in full (15,000)
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-15",
            "amount": 15000,
            "allocations": [{"bill_id": bill_id, "amount": 15000}],
        },
    )
    assert pay.status_code == 201, pay.text
    # now hold retainage that would leave less than is already paid
    up = client.put(f"/api/bills/{bill_id}", json={"retainage_held": 1000})
    assert up.status_code == 422, up.text


def test_hand_entered_bill_rejects_a_po_line_link(client, seed_accounts):
    v = _vendor(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "x",
                    "amount": 100,
                    "account_id": seed_accounts["5100"].id,
                    "purchase_order_line_id": 1,
                }
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_void_keeps_a_received_commitment_received(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES).json()
    client.put(f"/api/purchase-orders/{po['id']}", json={"status": "received"})
    ids = [ln["id"] for ln in po["lines"]]
    # partial bill — a received commitment stays received, not partial
    _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value == "received"
    # void it — still received, never downgraded to sent
    bill_id = db_session.query(Bill).filter(Bill.po_id == po["id"]).one().id
    client.post(f"/api/bills/{bill_id}/void")
    db_session.refresh(po_row)
    assert po_row.status.value == "received"


def test_full_billing_line_by_line_then_409_and_partial_after_void(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    ids = [ln["id"] for ln in po["lines"]]
    # bill each line in full, one at a time
    r1 = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 10000}])
    assert r1.status_code == 200, r1.text
    r2 = _convert(client, po["id"], lines=[{"line_id": ids[1], "amount": 5000}])
    assert r2.status_code == 200, r2.text
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value == "closed"
    # nothing left — a closed PO is refused up front
    nope = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 1}])
    assert nope.status_code in (400, 409), nope.text

    # void the second bill; the PO reopens to partial and can bill again
    client.post(f"/api/bills/{r2.json()['bill_id']}/void")
    db_session.refresh(po_row)
    assert po_row.status.value == "partial"
    again = _convert(client, po["id"], lines=[{"line_id": ids[1], "amount": 5000}])
    assert again.status_code == 200, again.text


def test_zero_dollar_line_is_skipped_on_convert(
    client, db_session, seed_accounts, seed_customer
):
    """A $0 (description-only) commitment line is not a bill line now; the
    priced lines still convert."""
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(
        client,
        v["id"],
        [
            {"description": "Mobilization note", "quantity": 1, "rate": 0},
            {"description": "Slab", "quantity": 1, "rate": 5000},
        ],
    ).json()
    bill_id = _convert(client, po["id"]).json()["bill_id"]
    lines = db_session.query(BillLine).filter_by(bill_id=bill_id).all()
    assert len(lines) == 1
    assert Decimal(str(lines[0].amount)) == Decimal("5000.00")


def test_taxed_subcontract_holds_retainage_on_pretax_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    """Retainage is a percent of the pre-tax subtotal; tax is payable in full,
    and the journal balances with gross debits."""
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(
        client,
        v["id"],
        [{"description": "Sub work", "quantity": 1, "rate": 10000}],
        tax_rate=0.08,
        retainage_pct=10,
    ).json()
    bill_id = _convert(client, po["id"]).json()["bill_id"]
    bill = db_session.get(Bill, bill_id)
    assert Decimal(str(bill.subtotal)) == Decimal("10000.00")
    assert Decimal(str(bill.tax_amount)) == Decimal("800.00")
    assert Decimal(str(bill.total)) == Decimal("10800.00")
    # 10% of the pre-tax subtotal
    assert Decimal(str(bill.retainage_held)) == Decimal("1000.00")
    # net of the held amount, tax still payable in full
    assert Decimal(str(bill.balance_due)) == Decimal("9800.00")
    # the journal balances: gross debit 10,800 = A/P 9,800 + 2050 1,000
    lines = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=bill.transaction_id)
        .all()
    )
    debits = sum((Decimal(str(line.debit)) for line in lines), Decimal("0"))
    credits = sum((Decimal(str(line.credit)) for line in lines), Decimal("0"))
    assert debits == credits == Decimal("10800.00")


def test_buyout_bought_is_the_pretax_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    pkg_c, _ = _accepted_two_package_estimate(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    _sent_po(
        client,
        v["id"],
        [{"description": "Concrete sub", "quantity": 1, "rate": 10000}],
        job_id=job["id"],
        commitment_type="subcontract",
        trade_package_id=pkg_c["id"],
        tax_rate=0.08,
    )
    data = _buyout(client, job["id"])
    row = next(r for r in data["rows"] if r["package_name"] == "Concrete")
    assert row["bought"] == 10000.0  # pre-tax, not 10,800
    assert row["commitments"][0]["subtotal"] == 10000.0


# ── Migration up/down incl. backfill ──────────────────────────────────────


# ── Review follow-ups (#574) ─────────────────────────────────────────────


def _close_through(client, day):
    r = client.put("/api/settings", json={"closing_date": day})
    assert r.status_code == 200, r.text


def test_progress_bill_is_dated_when_billed_not_to_the_po(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)  # PO dated 2026-09-01
    ids = [ln["id"] for ln in po["lines"]]
    _close_through(client, "2026-09-30")
    today = date.today().isoformat()

    # the PO's month is closed: its own date cannot be billed, today can
    refused = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 1000}], date="2026-09-15"
    )
    assert refused.status_code != 200, refused.text
    ok = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 1000}], date=today
    )
    assert ok.status_code == 200, ok.text
    bill = db_session.get(Bill, ok.json()["bill_id"])
    assert bill.date.isoformat() == today
    assert bill.due_date >= bill.date  # terms run from the bill date
    from app.models.transactions import Transaction

    assert db_session.get(Transaction, bill.transaction_id).date.isoformat() == today


def test_convert_without_a_date_keeps_the_po_date(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    r = _convert(client, po["id"])
    bill = db_session.get(Bill, r.json()["bill_id"])
    assert bill.date.isoformat() == "2026-09-01"


def test_edit_converted_bill_lines_retallies_the_commitment(
    client, db_session, seed_accounts, seed_customer
):
    """Convert 4000, edit to 3000, void: the PO line's billed goes
    4000 -> 3000 -> 0."""
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    ids = [ln["id"] for ln in po["lines"]]
    bill_id = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}]
    ).json()["bill_id"]

    def billed():
        row = db_session.get(PurchaseOrderLine, ids[0])
        db_session.refresh(row)
        return Decimal(str(row.billed_amount))

    assert billed() == Decimal("4000.00")
    bill = client.get(f"/api/bills/{bill_id}").json()
    line = bill["lines"][0]
    assert line["purchase_order_line_id"] == ids[0]

    def edited(rate):
        return {
            "lines": [
                {
                    "purchase_order_line_id": ids[0],
                    "account_id": line["account_id"],
                    "description": line["description"],
                    "quantity": 1,
                    "rate": rate,
                }
            ]
        }

    up = client.put(f"/api/bills/{bill_id}", json=edited(3000))
    assert up.status_code == 200, up.text
    assert up.json()["lines"][0]["purchase_order_line_id"] == ids[0]
    assert billed() == Decimal("3000.00")

    assert client.post(f"/api/bills/{bill_id}/void").status_code == 200
    assert billed() == Decimal("0.00")


def test_edit_cannot_bill_past_the_commitment_or_claim_a_new_link(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    ids = [ln["id"] for ln in po["lines"]]
    bill_id = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}]
    ).json()["bill_id"]
    acct = seed_accounts["5100"].id

    over = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "purchase_order_line_id": ids[0],
                    "account_id": acct,
                    "description": "x",
                    "quantity": 1,
                    "rate": 10001,
                }
            ]
        },
    )
    assert over.status_code == 422, over.text

    # a link to a line this bill never drew on is refused
    stray = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "purchase_order_line_id": ids[1],
                    "account_id": acct,
                    "description": "x",
                    "quantity": 1,
                    "rate": 100,
                }
            ]
        },
    )
    assert stray.status_code == 422, stray.text
    row = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(row)
    assert Decimal(str(row.billed_amount)) == Decimal("4000.00")


def test_editing_a_linked_line_away_gives_the_dollars_back(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    ids = [ln["id"] for ln in po["lines"]]
    bill_id = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}]
    ).json()["bill_id"]
    # replace the linked line with a hand-typed one: unlinked, PO released
    up = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "account_id": seed_accounts["5100"].id,
                    "description": "retyped",
                    "quantity": 1,
                    "rate": 4000,
                }
            ]
        },
    )
    assert up.status_code == 200, up.text
    row = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(row)
    assert Decimal(str(row.billed_amount)) == Decimal("0.00")
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value == "sent"


def _expired_sub_bill(client, seed_accounts):
    sub = _vendor(
        client,
        name="Lapsed Electric",
        vendor_type="sub",
        coi_expires="2026-08-01",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": sub["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 500}],
        },
    ).json()
    return sub, bill


def _pay(client, sub, bill, **extra):
    return client.post(
        "/api/bill-payments",
        json={
            "vendor_id": sub["id"],
            "date": "2026-09-10",
            "amount": 500,
            "allocations": [{"bill_id": bill["id"], "amount": 500}],
            **extra,
        },
    )


def test_payment_to_a_sub_with_an_expired_coi_needs_an_override(client, seed_accounts):
    sub, bill = _expired_sub_bill(client, seed_accounts)
    refused = _pay(client, sub, bill)
    assert refused.status_code == 409, refused.text
    assert "2026-08-01" in refused.json()["detail"]
    assert client.get(f"/api/bills/{bill['id']}").json()["status"] == "unpaid"

    ok = _pay(client, sub, bill, coi_override=True)
    assert ok.status_code == 201, ok.text
    assert ok.json()["coi_override"] is True
    assert "2026-08-01" in ok.json()["coi_override_note"]
    # the record shows it where payment details are read
    got = client.get(f"/api/bill-payments/{ok.json()['id']}").json()
    assert got["coi_override"] is True


def test_payment_with_a_current_coi_records_no_override(client, seed_accounts):
    sub = _vendor(
        client,
        name="Current Electric",
        vendor_type="sub",
        coi_expires="2027-08-01",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": sub["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 500}],
        },
    ).json()
    # an override sent for a current certificate is not recorded
    ok = _pay(client, sub, bill, coi_override=True)
    assert ok.status_code == 201, ok.text
    assert ok.json()["coi_override"] is False


def test_lien_waiver_saves_on_a_closed_period_bill(client, seed_accounts):
    v = _vendor(
        client, vendor_type="sub", default_expense_account_id=seed_accounts["5100"].id
    )
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 100}],
        },
    ).json()
    _close_through(client, "2026-09-30")
    ok = client.put(f"/api/bills/{bill['id']}", json={"lien_waiver_status": "pending"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["lien_waiver_status"] == "pending"
    # anything that is a posting still honours the closing date
    blocked = client.put(f"/api/bills/{bill['id']}", json={"notes": "late edit"})
    assert blocked.status_code != 200, blocked.text


def test_retainage_line_carries_the_job(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10, job_id=job["id"]).json()
    assert _convert(client, po["id"]).status_code == 200
    ret = _retainage_account(db_session)
    lines = db_session.query(TransactionLine).filter_by(account_id=ret.id).all()
    assert lines and all(ln.job_id == job["id"] for ln in lines)


@pytest.mark.timeout(300)  # three alembic walks of the whole chain in subprocesses
def test_migration_upgrades_downgrades_and_backfills(tmp_path):
    db = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    own = "f3b5d7e9a2c4"  # construction buyout and commitments (#553)
    # Up to the revision just before this one, seed a closed PO with a bill.
    assert run("upgrade", "e2a4c6d8f0b1").returncode == 0
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript("""
        INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1);
        INSERT INTO purchase_orders (id, po_number, vendor_id, status, date, subtotal, total)
            VALUES (1, 'PO-1', 1, 'CLOSED', '2026-01-01', 100, 100);
        INSERT INTO purchase_order_lines (id, purchase_order_id, quantity, rate, amount)
            VALUES (1, 1, 1, 100, 100);
        INSERT INTO bills (id, bill_number, vendor_id, status, po_id, date, total, balance_due)
            VALUES (1, 'BILL-PO-1', 1, 'UNPAID', 1, '2026-01-01', 100, 100);
        """)
    con.commit()
    con.close()

    up = run("upgrade", own)  # its own step, not the chain (#770)
    assert up.returncode == 0, up.stderr
    con = sqlite3.connect(db)
    billed = con.execute(
        "SELECT billed_amount FROM purchase_order_lines WHERE id = 1"
    ).fetchone()[0]
    con.close()
    assert Decimal(str(billed)) == Decimal("100")  # backfilled

    assert run("downgrade", "-1").returncode == 0
    assert run("upgrade", own).returncode == 0  # its own step, not the chain (#770)


# ── Retainage follows a converted bill's edited lines (#553) ─────────────


def _converted_4000_at_10(client, seed_accounts):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES, retainage_pct=10)
    ids = [ln["id"] for ln in po["lines"]]
    bill_id = _convert(
        client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}]
    ).json()["bill_id"]
    line = client.get(f"/api/bills/{bill_id}").json()["lines"][0]
    return po, ids, bill_id, line


def _edit_body(ids, line, rate, **extra):
    return {
        "lines": [
            {
                "purchase_order_line_id": ids[0],
                "account_id": line["account_id"],
                "description": line["description"],
                "quantity": 1,
                "rate": rate,
            }
        ],
        **extra,
    }


def test_editing_converted_bill_lines_recomputes_retainage(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 3000))
    assert r.status_code == 200, r.text
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("300.00")
    assert _gl(db_session, seed_accounts["2000"].id) == Decimal("-2700.00")
    assert _gl(db_session, _retainage_account(db_session).id) == Decimal("-300.00")

    # Down to 300 is accepted, with 30 held (it used to be refused).
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 300))
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("30.00")


def test_explicit_retainage_held_wins_on_a_line_edit(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    r = client.put(
        f"/api/bills/{bill_id}",
        json=_edit_body(ids, line, 3000, retainage_held=150),
    )
    assert r.status_code == 200, r.text
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("150.00")


def test_hand_set_retainage_survives_a_repost(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    r = client.put(f"/api/bills/{bill_id}", json={"retainage_held": 250})
    assert r.status_code == 200, r.text

    # A date change alone re-posts without sending retainage_held.
    r = client.put(f"/api/bills/{bill_id}", json={"date": "2026-02-01"})
    assert r.status_code == 200, r.text
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("250.00")
    assert Decimal(str(bill.balance_due)) == Decimal("3750.00")

    # A line change keeps it too, and balance_due agrees.
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 3000))
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("250.00")
    assert Decimal(str(bill.balance_due)) == Decimal("2750.00")
    assert _gl(db_session, _retainage_account(db_session).id) == Decimal("-250.00")


def test_derived_retainage_keeps_following_the_rate_across_reposts(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    for rate, held in ((3000, "300.00"), (2000, "200.00")):
        r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, rate))
        assert r.status_code == 200, r.text
        bill = db_session.get(Bill, bill_id)
        db_session.refresh(bill)
        assert Decimal(str(bill.retainage_held)) == Decimal(held)


def test_hand_set_retainage_above_a_smaller_subtotal_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    assert (
        client.put(f"/api/bills/{bill_id}", json={"retainage_held": 250}).status_code
        == 200
    )
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 200))
    assert r.status_code == 422, r.text
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("250.00")


def test_derived_retainage_follows_a_changed_commitment_rate(
    client, db_session, seed_accounts, seed_customer
):
    """The old check compared the stored figure with rate x old subtotal, so a
    commitment whose rate changed 10% -> 5% left the bill's 400 looking
    hand-set, and a line edit kept it instead of re-deriving 150."""
    po, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    bill = db_session.get(Bill, bill_id)
    assert bill.retainage_manual is False
    r = client.put(f"/api/purchase-orders/{po['id']}", json={"retainage_pct": 5})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 3000))
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("150.00")
    assert bill.retainage_manual is False
    assert Decimal(str(bill.balance_due)) == Decimal("2850.00")
    assert _gl(db_session, seed_accounts["2000"].id) == Decimal("-2850.00")
    assert _gl(db_session, _retainage_account(db_session).id) == Decimal("-150.00")


def test_hand_set_retainage_is_flagged_and_survives_a_line_edit(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    r = client.put(f"/api/bills/{bill_id}", json={"retainage_held": 250})
    assert r.status_code == 200, r.text
    assert r.json()["retainage_manual"] is True
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert bill.retainage_manual is True

    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 3000))
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("250.00")
    assert bill.retainage_manual is True
    assert Decimal(str(bill.balance_due)) == Decimal("2750.00")

    # Sending the derived figure itself counts as set by hand too: it is
    # then kept when the lines change.
    r = client.put(f"/api/bills/{bill_id}", json={"retainage_held": 300})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 2000))
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("300.00")


def test_hand_set_retainage_over_the_new_subtotal_is_422_and_a_new_amount_passes(
    client, db_session, seed_accounts, seed_customer
):
    _, ids, bill_id, line = _converted_4000_at_10(client, seed_accounts)
    assert (
        client.put(f"/api/bills/{bill_id}", json={"retainage_held": 250}).status_code
        == 200
    )
    r = client.put(f"/api/bills/{bill_id}", json=_edit_body(ids, line, 200))
    assert r.status_code == 422, r.text
    assert "cannot exceed the subtotal" in r.json()["detail"]
    bill = db_session.get(Bill, bill_id)
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("250.00")
    assert Decimal(str(bill.subtotal)) == Decimal("4000.00")

    # The same edit with a new amount goes through.
    r = client.put(
        f"/api/bills/{bill_id}", json=_edit_body(ids, line, 200, retainage_held=20)
    )
    assert r.status_code == 200, r.text
    db_session.refresh(bill)
    assert Decimal(str(bill.retainage_held)) == Decimal("20.00")
    assert Decimal(str(bill.subtotal)) == Decimal("200.00")


def test_a_hand_entered_bill_with_retainage_is_flagged_and_keeps_it(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "bill_number": "HAND-RET",
            "date": "2026-03-01",
            "retainage_held": 100,
            "lines": [
                {
                    "description": "Draw",
                    "account_id": seed_accounts["5100"].id,
                    "amount": 1000,
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["retainage_manual"] is True
    bill_id = r.json()["id"]
    r = client.put(
        "/api/bills/%d" % bill_id,
        json={
            "lines": [
                {
                    "description": "Draw",
                    "account_id": seed_accounts["5100"].id,
                    "amount": 800,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["retainage_held"]) == Decimal("100.00")

    # Entered without one, the flag is off (nothing to follow: no commitment).
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "bill_number": "HAND-NORET",
            "date": "2026-03-01",
            "lines": [
                {
                    "description": "Draw",
                    "account_id": seed_accounts["5100"].id,
                    "amount": 1000,
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["retainage_manual"] is False


def test_retainage_manual_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    before, revision = "a4c6e8f0b2d4", "b5d7f9a1c3e5"
    db = tmp_path / "manual.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(revision).down_revision == before
    command.upgrade(cfg, revision)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("bills")}
    assert "retainage_manual" in cols
    assert cols["retainage_manual"]["nullable"] is False
    engine.dispose()
    command.downgrade(cfg, before)
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("bills")}
    assert "retainage_manual" not in names and "retainage_held" in names
    engine.dispose()


def test_raising_a_closed_commitment_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES)
    assert _convert(client, po["id"]).status_code == 200
    ids = [ln["id"] for ln in po["lines"]]

    def put(rate):
        return client.put(
            f"/api/purchase-orders/{po['id']}",
            json={
                "lines": [
                    {
                        "id": ids[0],
                        "description": "Foundations",
                        "quantity": 1,
                        "rate": rate,
                    },
                    {"id": ids[1], "description": "Slab", "quantity": 1, "rate": 5000},
                ]
            },
        )

    r = put(12000)
    assert r.status_code == 409, r.text
    assert "new commitment" in r.text
    assert put(10000).status_code == 200  # unchanged amounts still save


def test_a_po_line_too_large_for_the_column_is_a_422(client, seed_accounts):
    """99,999,999 x 999,999 and 9,999,999 x 9,999,999 (figures that fit their
    columns, products that do not) are each a 422 sentence,
    never a 500 or a stored figure Postgres would reject."""
    vendor = client.post("/api/vendors", json={"name": "Big Co"}).json()
    for qty, rate in ((99_999_999, 999_999), (9_999_999, 9_999_999)):
        r = _po(
            client, vendor["id"], [{"description": "x", "quantity": qty, "rate": rate}]
        )
        assert r.status_code == 422, r.text
        assert "more than a line can be" in r.text


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


@pytest.fixture
def con(client):
    _construction(client)
    return client


@pytest.fixture
def po(con, seed_customer, seed_accounts):
    """A sent PO on a job: 100 sticks of conduit at $3."""
    job = _job(con, seed_customer.id)
    v = con.post(
        "/api/vendors",
        json={
            "name": "City Supply",
            "default_expense_account_id": seed_accounts["5100"].id,
        },
    ).json()
    r = con.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-06-01",
            "job_id": job["id"],
            "lines": [{"description": "Conduit", "quantity": 100, "rate": 3}],
        },
    )
    assert r.status_code == 201, r.text
    up = con.put(f"/api/purchase-orders/{r.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return job, up.json()


def test_a_po_with_receipts_does_not_go_back_to_draft(con, po):
    """PUT status draft on a PO with a delivery received on it
    was accepted."""
    job, order = po
    t = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "kind": "delivery",
            "purchase_order_id": order["id"],
            "lines": [{"po_line_id": order["lines"][0]["id"], "quantity": 10}],
        },
    ).json()
    url = f"/api/purchase-orders/{order['id']}"
    r = con.put(url, json={"status": "draft"})
    assert r.status_code == 409, r.text
    assert "cannot go back to draft" in r.json()["detail"]["message"]
    assert t["number"] in r.json()["detail"]["message"]
    assert con.get(url).json()["status"] == "partial"
    # with the ticket void, it can
    assert con.post(f"/api/field-tickets/{t['id']}/void").status_code == 200
    assert con.put(url, json={"status": "draft"}).status_code == 200


def test_a_billed_supplier_po_says_to_void_the_bill_not_a_sub_change_order(con, po):
    """A billed supplier PO refused a cut with "change it with a
    sub change order", which is for subcontracts."""
    job, order = po
    r = con.post(f"/api/purchase-orders/{order['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    line = con.get(f"/api/purchase-orders/{order['id']}").json()["lines"][0]
    r = con.put(
        f"/api/purchase-orders/{order['id']}",
        json={
            "lines": [
                {
                    "id": line["id"],
                    "description": "Conduit",
                    "quantity": 10,
                    "rate": 3,
                }
            ]
        },
    )
    assert r.status_code == 409, r.text
    message = r.json()["detail"]["message"]
    assert "void the bill" in message and "sub change order" not in message


def _today():
    # today as the code under test reads it, at the moment the test runs: a
    # module constant, taken at import, failed seven tests when a CI run
    # crossed midnight UTC (#773)
    return date.today()


def _void_later(db, co_id, on):
    """Mark a CO voided on `on` (the void route stamps now)."""
    co = db.get(ChangeOrder, co_id)
    co.voided_at = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc)
    db.commit()


def _commitment_with_voided_sub(client, db, seed_accounts, customer_id):
    job = _change_job(client, customer_id)
    vendor = _change_vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    owner = _co(client, job["id"], [_sub_line("Added", 2500)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200
    assert client.post(f"/api/change-orders/{sub['id']}/void").status_code == 200
    _void_later(db, sub["id"], _today() + timedelta(days=5))
    db.expire_all()
    return job, po


def test_a_sub_co_voided_later_still_counts_on_an_earlier_date(
    client, db_session, seed_accounts, seed_customer
):
    job, po = _commitment_with_voided_sub(
        client, db_session, seed_accounts, seed_customer.id
    )

    report = job_reports.commitment_status(db_session, as_of=_today(), job_id=job["id"])
    row = report["jobs"][0]["rows"][0]
    assert row["committed"] == 7500.0
    assert row["remaining"] == 7500.0
    lines = commitment_lines(db_session, [job["id"]], _today())
    assert sorted(float(ln["remaining"]) for ln in lines) == [2500.0, 5000.0]

    # after the void it is gone from both
    after = _today() + timedelta(days=10)
    report = job_reports.commitment_status(db_session, as_of=after, job_id=job["id"])
    assert report["jobs"][0]["rows"][0]["committed"] == 5000.0
    assert [
        float(ln["amount"]) for ln in commitment_lines(db_session, [job["id"]], after)
    ] == [5000.0]


def _closed_commitment(client, seed_accounts, customer_id):
    job = _change_job(client, customer_id)
    vendor = _change_vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    line_id = po["lines"][0]["id"]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": line_id, "amount": 5000}]},
    )
    assert r.status_code == 200, r.text
    po = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert po["status"] == "closed"
    return po, line_id


def test_a_closed_commitment_does_not_send_the_user_to_a_sub_co(
    client, seed_accounts, seed_customer
):
    po, line_id = _closed_commitment(client, seed_accounts, seed_customer.id)
    for rate, says in ((6000, "new commitment"), (4000, "void the bill")):
        r = client.put(
            f"/api/purchase-orders/{po['id']}",
            json={
                "lines": [
                    {"id": line_id, "description": "Base", "quantity": 1, "rate": rate}
                ]
            },
        )
        assert r.status_code == 409, r.text
        message = r.json()["detail"]["message"]
        assert says in message, message
        assert "sub change order" not in message.replace("no sub change order", "")
