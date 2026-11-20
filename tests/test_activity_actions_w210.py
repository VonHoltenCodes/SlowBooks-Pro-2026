"""Round 24 (W-210): an approval in words, once.

Approving a change order added "Marked approved by …" and, beside it,
"Edited by …" with the raw fields "Approved on set to 2026-09-20" and "Line
a line: Markup pct set to 0%". An approval is one entry now ("Approved by …
on <date>"); a line an edit names is named by its description, or by its
place on the document when it has none; a percentage field reads as its
label ("Markup %"). The other actions that write fields beside the status
(a pay application's approval, an estimate's convert, a potential change
order's promote) were the same and are one entry too.
"""

import re

from tests.test_co_effective_date_allowance import (
    _approve as _approve_co,
    _owner_co,
    _setup,
)
from tests.test_pay_applications import (
    _approve as _approve_app,
    _edit,
    _new_app,
    _two_line_job,
)
from tests.test_void_activity_r23 import _age, _events

RAW = re.compile(
    r"(set to|→)|\bpct\b|a line|_id\b|Approved on|Converted invoice|Promoted to set"
)


def _one_entry(events, kind="status"):
    newest = events[0]
    assert newest["kind"] == kind, events
    assert newest["details"] == []
    assert not any(
        e["kind"] == "edited" and e["when"] == newest["when"] for e in events
    ), events
    return newest


def test_a_change_orders_approval_is_one_entry_in_words(
    client, seed_customer, db_session
):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000)
    client.post(f"/api/change-orders/{co['id']}/submit")
    _age(db_session)
    r = _approve_co(client, co["id"], approved_on="2026-11-02")
    assert r.status_code == 200, r.text
    newest = _one_entry(_events(client, "change_order", co["id"]))
    assert newest["text"] == f"Approved by {newest['who']} on 2026-11-02."


def test_a_pay_applications_approval_is_one_entry(
    client, seed_accounts, seed_customer, db_session
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _age(db_session)
    assert _approve_app(client, app["id"]).status_code == 200
    events = _events(client, "pay_application", app["id"])
    newest = _one_entry(events)
    assert newest["text"].startswith(f"Approved by {newest['who']}; invoice ")
    assert not RAW.search(newest["text"]), newest
    # the edit before it names the line by its description
    said = [d for e in events for d in e["details"]]
    assert "Line Sitework, $60,000.00: This period $0.00 → $30,000.00" in said


def test_an_estimates_convert_and_a_pcos_promote_are_one_entry(
    client, seed_accounts, seed_customer, db_session
):
    from tests.test_change_orders import LABOR, _co, _job
    from tests.test_estimate_void_1128 import _estimate

    est = _estimate(client, seed_customer.id)
    _age(db_session)
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code == 200, r.text
    newest = _one_entry(_events(client, "estimate", est["id"]))
    number = r.json()["invoice_number"]
    assert newest["text"] == f"Converted to invoice {number} by {newest['who']}."

    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco")
    _age(db_session)
    owner = client.post(f"/api/change-orders/{pco['id']}/promote").json()
    newest = _one_entry(_events(client, "change_order", pco["id"]))
    assert newest["text"] == (
        f"Promoted to change order {owner['number']} by {newest['who']}."
    )


def test_a_line_with_no_description_is_named_by_its_place(
    client, seed_customer, db_session
):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    body = {
        "job_id": job["id"],
        "kind": "owner",
        "lines": [
            {
                "description": "Deck",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": 100,
            },
            {"cost_type": "material", "quantity": 1, "unit_cost": 200},
        ],
    }
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == 201, r.text
    co = r.json()
    _age(db_session)
    lines = [
        {
            k: line[k]
            for k in ("description", "cost_type", "quantity", "unit_cost")
            if line.get(k) is not None
        }
        | {"markup_pct": 15}
        for line in co["lines"]
    ]
    r = client.put(f"/api/change-orders/{co['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    edited = _events(client, "change_order", co["id"])[0]
    assert edited["kind"] == "edited", edited
    said = " | ".join(edited["details"])
    assert "Line 2 ($200.00): Amount $0.00 → $200.00" in said, said
    assert "a line" not in said, said


def test_a_percentage_reads_as_its_label_and_an_empty_number_as_zero_is_quiet(
    db_session,
):
    from app.models.audit import AuditLog
    from app.services.activity import _changes, _Words

    words = _Words(db_session)
    assert words.field("markup_pct") == "Markup %"
    assert words.field("retainage_pct") == "Retainage %"
    row = AuditLog(
        changed_fields=["markup_pct", "quantity"],
        old_values={"markup_pct": None, "quantity": "1"},
        new_values={"markup_pct": "0", "quantity": "2"},
    )
    assert _changes(words, row) == ["Quantity 1 → 2"]
    row.new_values = {"markup_pct": "12.5", "quantity": "1"}
    assert _changes(words, row) == ["Markup % set to 12.5%"]
