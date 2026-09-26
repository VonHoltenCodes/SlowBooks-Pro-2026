"""A court-ordered garnishment is ended, not deleted: the Deductions page's
Remove button deleted the order outright, losing its case number and terms.
End order stops it being withheld from future pay runs and keeps the record;
DELETE is refused with where to go instead."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _employee(client):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Jonah",
            "last_name": "Pike",
            "pay_type": "salary",
            "pay_rate": 52000,
            "work_state": "IL",
            "residence_state": "IL",
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _order(client, emp):
    r = client.post(
        "/api/deductions/garnishments",
        json={
            "employee_id": emp,
            "garnishment_type": "creditor",
            "calc_method": "fixed",
            "amount": 150,
            "priority": 1,
            "case_number": "2026-CV-0419",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ending_an_order_keeps_its_record_and_stops_withholding(client, seed_accounts):
    emp = _employee(client)
    oid = _order(client, emp)
    r = client.post(f"/api/deductions/garnishments/{oid}/end")
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    kept = client.get(f"/api/deductions/garnishments?employee_id={emp}").json()
    assert [(g["id"], g["case_number"], g["is_active"]) for g in kept] == [
        (oid, "2026-CV-0419", False)
    ]
    again = client.post(f"/api/deductions/garnishments/{oid}/end")
    assert again.status_code == 400 and "already ended" in again.json()["detail"]


def test_an_order_cannot_be_deleted(client, seed_accounts):
    emp = _employee(client)
    oid = _order(client, emp)
    r = client.delete(f"/api/deductions/garnishments/{oid}")
    assert r.status_code == 405
    assert f"/api/deductions/garnishments/{oid}/end" in r.json()["detail"]
    kept = client.get(f"/api/deductions/garnishments?employee_id={emp}").json()
    assert [g["id"] for g in kept] == [oid] and kept[0]["is_active"] is True


def test_the_page_offers_end_order_not_remove():
    js = (ROOT / "app/static/js/deductions.js").read_text(encoding="utf-8")
    assert "API.post(`/deductions/garnishments/${id}/end`" in js
    assert "API.del(`/deductions/garnishments/" not in js
    assert ">End order</button>" in js and ">Remove</button>" not in js
