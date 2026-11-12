"""Adjusting entries, marked and reported (#341): a journal entry carries
is_adjusting and who posted it; the list filters on the flag; the Adjusting
Journal Entries report lists the flagged ones with totals that tie to the
ledger; and the Profit & Loss and Trial Balance run without them on
request, the difference being exactly their amounts."""

from pathlib import Path

from tests.conftest import WEASYPRINT_AVAILABLE

ROOT = Path(__file__).resolve().parents[1]


def _post(client, accounts, debit_no, credit_no, amount, text, **extra):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-02-10",
            "description": text,
            "lines": [
                {"account_id": accounts[debit_no].id, "debit": amount, "credit": "0"},
                {"account_id": accounts[credit_no].id, "debit": "0", "credit": amount},
            ],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _tb(client, exclude=False):
    qs = "start_date=2026-01-01&end_date=2026-12-31"
    if exclude:
        qs += "&exclude_adjusting=true"
    return client.get(f"/api/reports/trial-balance?{qs}").json()


def test_the_flag_defaults_off_for_the_owner_and_is_kept(client, seed_accounts):
    plain = _post(client, seed_accounts, "6300", "1000", "40.00", "Insurance paid")
    assert plain["is_adjusting"] is False and plain["posted_by"] == "admin"
    adj = _post(
        client, seed_accounts, "6300", "2500", "100.00", "Accrual", is_adjusting=True
    )
    assert adj["is_adjusting"] is True
    assert client.get(f"/api/journal/{adj['id']}").json()["is_adjusting"] is True
    # the list filters on it
    ids = {
        e["id"]
        for e in client.get("/api/journal?adjusting=true&start_date=1900-01-01").json()[
            "entries"
        ]
    }
    assert ids == {adj["id"]}
    ids = {
        e["id"]
        for e in client.get(
            "/api/journal?adjusting=false&start_date=1900-01-01"
        ).json()["entries"]
    }
    assert plain["id"] in ids and adj["id"] not in ids
    assert (
        len(
            client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
                "entries"
            ]
        )
        == 2
    )


def test_the_report_lists_the_flagged_ones_and_the_statements_can_leave_them_out(
    client, seed_accounts
):
    _post(client, seed_accounts, "6300", "1000", "40.00", "Insurance paid")
    a = _post(
        client,
        seed_accounts,
        "6300",
        "2500",
        "100.00",
        "Accrue insurance",
        is_adjusting=True,
    )
    b = _post(
        client,
        seed_accounts,
        "6000",
        "2500",
        "250.00",
        "Accrue advertising",
        is_adjusting=True,
    )

    rep = client.get(
        "/api/reports/adjusting-entries?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert [e["id"] for e in rep["entries"]] == [a["id"], b["id"]]
    assert rep["count"] == 2
    assert rep["total_debit"] == 350.0 and rep["total_credit"] == 350.0
    assert rep["entries"][0]["posted_by"] == "admin"
    assert [ln["account_number"] for ln in rep["entries"][0]["lines"]] == [
        "6300",
        "2500",
    ]

    # the Trial Balance with and without them differs by exactly their amounts
    with_, without = _tb(client), _tb(client, exclude=True)
    assert without["exclude_adjusting"] is True and with_["exclude_adjusting"] is False
    by_no = lambda tb: {i["account_number"]: i for i in tb["items"]}  # noqa: E731
    assert by_no(with_)["6300"]["total_debit"] == 140.0
    assert by_no(without)["6300"]["total_debit"] == 40.0
    assert by_no(with_)["2500"]["total_credit"] == 350.0
    assert "2500" not in by_no(without)  # nothing but adjustments touched it
    assert with_["total_debit"] - without["total_debit"] == 350.0

    # and the P&L
    pl = client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    pl_x = client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31&exclude_adjusting=true"
    ).json()
    assert pl["total_expenses"] == 390.0 and pl_x["total_expenses"] == 40.0
    assert pl_x["exclude_adjusting"] is True

    # the CSVs: the report's own, and the statements' say when they left them out
    csv = client.get(
        "/api/reports/adjusting-entries/csv?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "Accrue advertising" in csv.text and "Total (2 entries)" in csv.text
    assert ",350.00,350.00" in csv.text
    tb_csv = client.get(
        "/api/reports/trial-balance/csv?start_date=2026-01-01&end_date=2026-12-31&exclude_adjusting=true"
    ).text
    assert "(adjusting entries left out)" in tb_csv
    assert (
        "(adjusting entries left out)"
        not in client.get(
            "/api/reports/trial-balance/csv?start_date=2026-01-01&end_date=2026-12-31"
        ).text
    )


def test_the_report_saves_as_pdf_and_as_a_saved_report(client, seed_accounts):
    _post(client, seed_accounts, "6300", "2500", "100.00", "Accrual", is_adjusting=True)
    if WEASYPRINT_AVAILABLE:
        r = client.get(
            "/api/reports/adjusting-entries/pdf?start_date=2026-01-01&end_date=2026-12-31"
        )
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    r = client.post(
        "/api/saved-reports",
        json={
            "name": "Year-end adjustments",
            "report_type": "adjusting_entries",
            "parameters": {"period": "this_year_to_date"},
        },
    )
    assert r.status_code in (200, 201), r.text


def test_a_voided_adjustment_and_its_reversal_both_list_as_adjusting(
    client, seed_accounts
):
    a = _post(
        client, seed_accounts, "6300", "2500", "100.00", "Accrual", is_adjusting=True
    )
    r = client.post(f"/api/journal/{a['id']}/void")
    assert r.status_code == 200, r.text
    rev = r.json()
    assert rev["is_adjusting"] is True
    assert rev["description"] == "VOID adjusting entry: Accrual"
    rep = client.get(
        "/api/reports/adjusting-entries?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert [(e["id"], e["voided"], e["is_reversal"]) for e in rep["entries"]] == [
        (a["id"], True, False),
        (rev["id"], False, True),
    ]
    assert rep["total_debit"] == 200.0  # both sides list; the ledger nets to nothing
    assert _tb(client, exclude=True)["items"] == []


def test_the_pages_offer_the_box_the_mark_the_filter_and_the_report():
    journal_js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert 'name="is_adjusting"' in journal_js
    assert "Adjusting entry" in journal_js
    assert "adjustingMark(e)" in journal_js and ">Adjusting</span>" in journal_js
    assert "Posted by" in journal_js
    assert "/journal?adjusting=true" in journal_js
    assert (
        "is_adjusting: !!(form.is_adjusting && form.is_adjusting.checked)" in journal_js
    )
    reports_js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "'adjusting-entries':" in reports_js
    assert "card('adjusting-entries', 'Adjusting Journal Entries'" in reports_js
    assert "ReportsPage._exportButtons('adjusting-entries', qs)" in reports_js
    assert "Without adjusting entries" in reports_js
    assert "...ReportsPage._statementOpts(prefill)" in reports_js  # the P&L
    assert "...ReportsPage._adjOpts(prefill)" in reports_js  # the Trial Balance
    assert "&exclude_adjusting=true" in reports_js
