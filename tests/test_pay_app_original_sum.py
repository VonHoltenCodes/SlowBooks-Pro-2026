"""#723 part 1: the pay application's original contract sum and net change by
change orders. A job given its contract sum on the plain job form, with no
contract type saved, printed "Original contract sum 0.00" and the whole
contract as "Net change by change orders". The net change is the approved
change orders as of the application; the original is the sum less them."""

import re
from decimal import Decimal

from app.models.jobs import Job
from app.models.pay_applications import PayApplication


def _job_from_form(client, customer_id, amount=36000, name="Maple Street Remodel"):
    """A job whose contract sum was typed on the job form (no contract type)."""
    r = client.post(
        "/api/jobs",
        json={"customer_id": customer_id, "name": name, "contract_amount": amount},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sov(client, job_id, value):
    r = client.put(
        f"/api/jobs/{job_id}/sov",
        json={
            "lines": [
                {"description": "Framing", "kind": "work", "scheduled_value": value}
            ]
        },
    )
    assert r.status_code == 200, r.text
    return client.get(f"/api/jobs/{job_id}/contract").json()["sov"]


def _co(client, job_id, amount):
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job_id,
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Added deck",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": amount,
                }
            ],
        },
    )
    assert co.status_code in (200, 201), co.text
    co = co.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/approve")
    assert r.status_code == 200, r.text
    return co


def _app(client, job_id, sov_line_id, this_period, approve=True):
    r = client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": "2026-06-01"}
    )
    assert r.status_code == 201, r.text
    app = r.json()
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={
            "lines": [
                {"sov_line_id": sov_line_id, "this_period": this_period, "stored": 0}
            ]
        },
    )
    assert r.status_code == 200, r.text
    if approve:
        r = client.post(f"/api/pay-applications/{app['id']}/approve")
        assert r.status_code == 200, r.text
    return app


def _header(db_session, app_id) -> dict:
    """The three contract rows of the printed header, as Decimals."""
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    db_session.expire_all()
    pa = db_session.get(PayApplication, app_id)
    job = db_session.get(Job, pa.job_id)
    html = render_pay_application_html(
        pa, _out(db_session, pa), job, get_all_settings(db_session)
    )
    out = {}
    for label in (
        "Original contract sum",
        "Net change by change orders",
        "Contract sum to date",
    ):
        m = re.search(re.escape(label) + r'</td><td class="r">\$?([-\d,.]+)</td>', html)
        assert m, label
        out[label] = Decimal(m.group(1).replace(",", ""))
    return out


def _figures(original, net, to_date):
    return {
        "Original contract sum": Decimal(original),
        "Net change by change orders": Decimal(net),
        "Contract sum to date": Decimal(to_date),
    }


def test_a_sum_from_the_job_form_prints_as_the_original(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    assert _header(db_session, app["id"]) == _figures("36000", "0", "36000")


def test_a_draft_reads_the_job_the_same_way(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    app = _app(client, job["id"], sov[0]["id"], 12000, approve=False)
    assert _header(db_session, app["id"]) == _figures("36000", "0", "36000")


def test_change_orders_are_the_net_change_on_an_untyped_job(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    _co(client, job["id"], 5000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")


def test_a_schedule_of_values_settles_the_type_and_stamps_the_original(
    client, seed_accounts, seed_customer
):
    """The choice made for #723: the sum becomes a lump sum once it has a
    schedule of values to bill, as building from an estimate does."""
    job = _job_from_form(client, seed_customer.id)
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["contract_type"] is None  # the job form alone makes no contract
    _sov(client, job["id"], 36000)
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["contract_type"] == "lump_sum"
    assert c["original_contract_amount"] == 36000.0
    assert c["changes"] == 0.0


def _unstamp(db_session, job_id, app_id=None):
    """Put the job (and an approved application) back as 2.31.7 left them: a
    sum with no type and no original, a snapshot with no original."""
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    job.contract_type = None
    job.original_contract_amount = None
    if app_id is not None:
        pa = db_session.get(PayApplication, app_id)
        pa.contract_sum_original = None
    db_session.commit()


def test_an_application_approved_before_the_fix_prints_right(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    _co(client, job["id"], 5000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    _unstamp(db_session, job["id"], app["id"])
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")
    # a change order approved after the application does not reach its reprint
    _co(client, job["id"], 2500)
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")


def test_an_application_approved_before_the_fix_with_no_change_orders(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    _unstamp(db_session, job["id"], app["id"])
    assert _header(db_session, app["id"]) == _figures("36000", "0", "36000")


def test_a_draft_on_an_unstamped_job_prints_right(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    _co(client, job["id"], 5000)
    app = _app(client, job["id"], sov[0]["id"], 12000, approve=False)
    _unstamp(db_session, job["id"])
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")


def test_approving_on_an_unstamped_job_snapshots_the_original(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    _co(client, job["id"], 5000)
    app = _app(client, job["id"], sov[0]["id"], 12000, approve=False)
    _unstamp(db_session, job["id"])
    r = client.post(f"/api/pay-applications/{app['id']}/approve")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    pa = db_session.get(PayApplication, app["id"])
    assert pa.contract_sum_original == Decimal("36000.00")
    assert pa.contract_sum_to_date == Decimal("41000.00")
    jb = db_session.get(Job, job["id"])
    assert jb.contract_type == "lump_sum"
    assert jb.original_contract_amount == Decimal("36000.00")


def _reports_agree(client, db_session, job_id, original, changes, revised):
    from datetime import date

    from app.services.job_reports import change_order_log, wip_row

    c = client.get(f"/api/jobs/{job_id}/contract").json()["contract"]
    assert c["changes"] == float(changes)
    assert c["contract_amount"] == float(revised)
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    row = wip_row(db_session, job, date.today())
    assert row["contract_original"] == float(original)
    assert row["approved_change_orders"] == float(changes)
    assert row["revised_contract"] == float(revised)
    log = change_order_log(db_session, job_id=job_id)["jobs"][0]["contract"]
    assert log == {
        "original": float(original),
        "approved_changes": float(changes),
        "revised": float(revised),
    }


def test_the_contract_tab_and_job_reports_agree_with_the_pdf(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    _reports_agree(client, db_session, job["id"], 36000, 0, 36000)
    _unstamp(db_session, job["id"], app["id"])
    _reports_agree(client, db_session, job["id"], 36000, 0, 36000)


def test_the_contract_tab_and_job_reports_agree_with_change_orders(
    client, db_session, seed_accounts, seed_customer
):
    job = _job_from_form(client, seed_customer.id)
    sov = _sov(client, job["id"], 36000)
    _co(client, job["id"], 5000)
    app = _app(client, job["id"], sov[0]["id"], 12000)
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")
    _reports_agree(client, db_session, job["id"], 36000, 5000, 41000)
    # the same job as 2.31.7 left it: nothing stamped
    _unstamp(db_session, job["id"], app["id"])
    assert _header(db_session, app["id"]) == _figures("36000", "5000", "41000")
    _reports_agree(client, db_session, job["id"], 36000, 5000, 41000)
