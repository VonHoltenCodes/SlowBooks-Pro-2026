"""A final pay application releases the retainage in one document (#1228).

The last application on a job bills the period's work and releases everything
the job is holding — the retainage held through the prior applications *and*
what this application itself holds on the work it bills. The save guard used
to compare the release against the prior applications' held-to-date only, so
the exact release the preview showed (held-to-date lands on zero) was refused
with "Release 9524.54 is more than the 8060.54 of retainage held to date.",
and the job could not close in one application. The guard is now the one line
both preview and save read: held-to-date net of this release cannot go below
zero (#576) — and it says the amounts as money.
"""

from decimal import Decimal

from tests.test_pay_applications import (
    _approve,
    _edit,
    _gl,
    _new_app,
    _two_line_job,
)


def _job_at_8060_held(client, customer_id):
    """SOV 100,000 at 10%: applications 1 bills 80,605.40 completed, leaving
    8,060.54 held — the issue's job."""
    job, sov = _two_line_job(client, customer_id)
    a1 = _new_app(client, job["id"])
    r = _edit(
        client,
        a1["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 50000.00, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 30605.40, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    # totals ride out as floats, so compare them as money, not exact Decimals
    assert round(r.json()["totals"]["retainage_to_date"], 2) == 8060.54
    assert _approve(client, a1["id"]).status_code == 200, "app 1 must approve"
    return job, sov


def _final(client, app_id, sov, release):
    """Edit the final application: 14,640.00 billed this period (which holds
    1,464.00 of its own) and `release` returned."""
    return _edit(
        client,
        app_id,
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 10000.00, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 4640.00, "stored": 0},
        ],
        release_retainage=release,
    )


def test_a_final_application_bills_and_releases_everything_at_once(
    client, db_session, seed_accounts, seed_customer
):
    """The issue's figures: 8,060.54 held to date, 14,640.00 billed this
    period (1,464.00 held), release 9,524.54 — Save must accept the release
    the preview shows, and approve must post what the preview promised."""
    job, sov = _job_at_8060_held(client, seed_customer.id)
    final = _new_app(client, job["id"])
    r = _final(client, final["id"], sov, 9524.54)
    assert r.status_code == 200, r.text
    preview = r.json()["totals"]
    assert round(preview["retainage_to_date"], 2) == 0.00
    assert round(preview["current_due"], 2) == 22700.54

    assert client.post(f"/api/pay-applications/{final['id']}/submit").status_code == 200
    approved = _approve(client, final["id"])
    assert approved.status_code == 200, approved.text
    saved = approved.json()["totals"]
    # the preview and the save are the same arithmetic, in both directions
    assert (
        round(saved["retainage_to_date"], 2)
        == round(preview["retainage_to_date"], 2)
        == 0.00
    )
    assert round(saved["current_due"], 2) == round(preview["current_due"], 2)
    # the job closes with nothing held: 8,060.54 + 1,464.00 debited to 1150
    # across the job, 9,524.54 released back on this application
    assert _gl(db_session, "1150") == Decimal("0.00")
    invoice = client.get(f"/api/invoices/{approved.json()['invoice_id']}").json()
    assert Decimal(invoice["retainage_released"]) == Decimal("9524.54")


def test_releasing_past_the_hold_is_still_refused_and_says_it_as_money(
    client, seed_accounts, seed_customer
):
    """A release over the ceiling (held-to-date would go below zero — the
    line the preview already draws) stays refused, and the refusal formats
    the amounts as money: $10,524.54, not 10524.54."""
    job, sov = _job_at_8060_held(client, seed_customer.id)
    final = _new_app(client, job["id"])
    r = _final(client, final["id"], sov, 10524.54)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Retainage to date would be -$1,000.00" in detail
    assert "$10,524.54" in detail  # the release, thousands separator and all
