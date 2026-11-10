"""A report grid's PDF keeps every column on the page when the column names
are long (#753).

The column headings never wrapped, so eight long job names made the P&L by
Job grid wider than a landscape page, and the columns past its edge, the
Total among them, never printed. The headings wrap now.
"""

import io
from datetime import date
from decimal import Decimal

import pytest

from app.services.accounting import create_journal_entry

PERIOD = "start_date=2026-07-01&end_date=2026-07-31"
NAMES = [
    "Suite 104 Tenant Build-Out - Bean There Coffee",
    "Suite 112 Tenant Build-Out - Polished Nail Studio",
    "Covered Lanai and Outdoor Kitchen - Harper Residence",
    "Dental Office Build-Out - Clermont Family Dental",
    "Fellowship Hall Reroof and Renovation - First Baptist Umatilla",
    "Two-Car Garage Addition - Delgado Residence",
    "Wood Privacy Fence and Gates - Delgado Residence",
    "Lot Site Work and Driveway - 412 Oak St",
    "House Demolition - 412 Oak St",
    "Kitchen Remodel - Turner Residence",
    "Primary Bath Remodel - Turner Residence",
    "Suite 120 Tenant Build-Out - Lakeside Commons Yoga Studio",
]


@pytest.fixture
def long_jobs(client, db_session, seed_accounts, seed_customer):
    """Twelve jobs with long names, job n earning 1,0nn.00 in July, and an
    untagged sale for the No job column."""
    income, expense = seed_accounts["4000"], seed_accounts["6000"]
    for n, name in enumerate(NAMES, start=1):
        job = client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": name}
        ).json()
        amount = Decimal(f"10{n:02d}")
        create_journal_entry(
            db_session,
            date(2026, 7, 10),
            f"work on {name}",
            [
                {"account_id": expense.id, "debit": amount, "credit": Decimal("0")},
                {"account_id": income.id, "debit": Decimal("0"), "credit": amount},
            ],
            job_id=job["id"],
        )
    create_journal_entry(
        db_session,
        date(2026, 7, 11),
        "walk-in",
        [
            {"account_id": expense.id, "debit": Decimal("50"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("50")},
        ],
    )
    db_session.commit()


@pytest.mark.parametrize("size", ["letter", "a4"])
def test_every_job_column_and_the_total_print(client, db_session, long_jobs, size):
    pypdf = pytest.importorskip("pypdf")
    from app.services.settings_service import set_setting

    set_setting(db_session, "pdf_paper_size", size)
    db_session.commit()
    r = client.get(f"/api/reports/profit-loss-by-job/pdf?{PERIOD}")
    assert r.status_code == 200, r.text[:200]
    pages = pypdf.PdfReader(io.BytesIO(r.content)).pages
    # No job and twelve jobs: eight columns a page, then the rest
    assert len(pages) == 2
    # Text past the page's right edge is still in the PDF (extract_text
    # finds it), just not on paper, so place each amount by its x: the
    # text matrix's origin through the current transform (the pages are
    # not rotated; the test asserts so).
    assert all(not page.get("/Rotate") for page in pages)
    on_page = []
    for page in pages:
        width = float(page.mediabox.width)
        page.extract_text(
            visitor_text=lambda text, cm, tm, *_: (
                on_page.append(text.strip())
                if text.strip() and 0 <= cm[0] * tm[4] + cm[2] * tm[5] + cm[4] < width
                else None
            )
        )
    for n in range(1, 13):
        assert f"$1,0{n:02d}.00" in on_page, (n, on_page)
    # both pages print the Total column (12,128.00 of income and of expense,
    # five rows a page)
    assert on_page.count("$12,128.00") == 10, on_page


def test_a_heading_breaks_between_words_not_inside_one(
    client, seed_accounts, seed_customer
):
    """overflow-wrap: anywhere fit a crowded grid by splitting words: the
    WIP Schedule printed MARGIN as M/AR/GI/N and % COMPLETE a letter a line.
    A heading breaks at its spaces."""
    pypdf = pytest.importorskip("pypdf")
    from tests.test_pay_applications import _two_line_job

    client.put("/api/settings", json={"company_type": "construction"})
    _two_line_job(client, seed_customer.id)
    r = client.get("/api/reports/wip-schedule/pdf")
    assert r.status_code == 200, r.text[:200]
    text = " ".join(
        p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages
    )
    words = ("MARGIN", "COMPLETE", "EARNED", "OVERBILLED", "PROVISION")
    assert [w for w in words if w not in text] == [], text[:600]
