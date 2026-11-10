"""Every WIP Schedule column prints on a letter or A4 page (#760).

The WIP Schedule has more columns than fit across a landscape page. With a
dozen jobs under long names the right-hand columns, Loss provision first,
ran past the page's edge. The grid now goes over pages of columns, the
Customer and Job columns repeated on each, like the P&L grids.
"""

import io
import re
from datetime import date
from decimal import Decimal

import pytest

from app.services import job_reports
from tests.test_grid_pdf_long_headings_753 import NAMES
from tests.test_job_reports import _cogs, _construction, _contract_job, _customer

AS_OF = "as_of=2026-06-01"


def _bill(db, job, amount):
    """Owner billing by posted revenue (no pay application), on the job."""
    from app.services.accounting import create_journal_entry
    from app.models.accounts import Account

    ar = db.query(Account).filter(Account.account_number == "1100").first()
    income = db.query(Account).filter(Account.account_number == "4000").first()
    create_journal_entry(
        db,
        date(2026, 4, 1),
        "owner billing",
        [
            {"account_id": ar.id, "debit": Decimal(amount)},
            {"account_id": income.id, "credit": Decimal(amount), "job_id": job.id},
        ],
    )


@pytest.fixture
def twelve_jobs(client, db_session, seed_accounts):
    """Twelve long-named jobs in the millions, every third one at a loss, so
    each WIP column carries a wide figure."""
    _construction(client)
    assert _cogs(db_session) is not None
    for n, name in enumerate(NAMES, start=1):
        customer = _customer(db_session, f"Lakeside Commercial Owner {n}")
        contract = 1_200_000 + n * 11_111
        budget = contract + 150_000 if n % 3 == 0 else contract - 250_000
        job = _contract_job(
            db_session, customer, name, contract, budget, 600_000 + n * 1_234
        )
        _bill(db_session, job, str(500_000 + n * 7_777))
    db_session.commit()


def _runs(pdf_bytes, pypdf):
    """Every text run of the PDF as (page index, text, x, page width, size),
    x the run's origin through the full transform (the pages are not
    rotated) and size its font size in points."""
    pages = pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages
    assert all(not page.get("/Rotate") for page in pages)
    out = []
    for i, page in enumerate(pages):
        width = float(page.mediabox.width)
        page.extract_text(
            visitor_text=lambda text, cm, tm, _font, size, i=i, width=width: (
                out.append(
                    (
                        i,
                        text.strip(),
                        cm[0] * tm[4] + cm[2] * tm[5] + cm[4],
                        width,
                        size * tm[0] * cm[0],
                    )
                )
                if text.strip()
                else None
            )
        )
    return out


def _expected_cells(data):
    """What the grid prints for every job and for the totals row, once each."""
    from app.routes.reports.construction import _pdf_cell

    cols = data["columns"]
    cells = [_pdf_cell(r[c["key"]], c["kind"]) for r in data["rows"] for c in cols[2:]]
    cells += [
        _pdf_cell(data["totals"][c["key"]], c["kind"])
        for c in cols[2:]
        if c["key"] in data["totals"]
    ]
    return [c for c in cells if c]


def test_the_grid_goes_over_pages_of_columns(db_session, twelve_jobs):
    """Checked on the section builder, which runs without WeasyPrint: every
    column on some page, Customer and Job and the totals on each, the
    period line counting the pages."""
    from app.routes.reports import construction

    data = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))
    grid = construction._wip_sections(data)
    assert len(grid) == 3
    labels = [c["label"] for c in data["columns"]]
    printed = {label for s in grid for label in s["columns"]}
    assert printed == set(labels)
    for n, s in enumerate(grid, start=1):
        assert s["title"] == "WIP Schedule"
        assert s["columns"][:2] == ["Customer", "Job"]
        assert s["text_columns"] == 2
        assert f"(page {n} of 3)" in s["period"]
        # a row per job, then the totals
        assert len(s["rows"]) == len(NAMES) + 1
        assert s["rows"][-1]["style"] == "grand-total"
        assert s["rows"][-1]["cells"][0] == "Total"
        # the revised contract on every page
        assert "Revised" in s["columns"]
    # each figure once, in the report's order, apart from the repeated Revised
    own = [
        label
        for s in grid
        for i, label in enumerate(s["columns"][2:])
        if not (label == "Revised" and s is not grid[0])
    ]
    assert own == labels[2:]
    assert "columns 1–7 of 19" in grid[0]["period"]
    assert "columns 15–19 of 19" in grid[-1]["period"]
    assert grid[-1]["columns"][-1] == "Loss provision"


@pytest.mark.parametrize("size", ["letter", "a4"])
def test_every_wip_column_prints_on_the_page(client, db_session, twelve_jobs, size):
    pypdf = pytest.importorskip("pypdf")
    from collections import Counter

    from app.services.settings_service import set_setting

    set_setting(db_session, "pdf_paper_size", size)
    db_session.commit()
    r = client.get(f"/api/reports/wip-schedule/pdf?{AS_OF}")
    assert r.status_code == 200, r.text[:200]
    runs = _runs(r.content, pypdf)
    # no run starts past the paper's edge, and no figure runs past it (its
    # width taken at 0.6 em a character; Helvetica's digits are 0.556)
    figure = re.compile(r"^-?\$[\d,]+\.\d\d$|^-?[\d.]+%$")
    off = [
        (page, text, round(x))
        for page, text, x, width, size_pt in runs
        if not 0 <= x < width
        or (figure.match(text) and x + 0.6 * size_pt * len(text) > width)
    ]
    assert off == [], off

    on_page = [text for _page, text, *_ in runs]
    words = {w for text in on_page for w in text.split()}
    # every heading, word for word (headings print in capitals and wrap)
    for c in job_reports.WIP_COLUMNS:
        for word in c["label"].upper().split():
            assert word in words, (c["label"], word)
    # every job's figures and the totals row's
    data = client.get(f"/api/reports/wip-schedule?{AS_OF}").json()
    missing = Counter(_expected_cells(data)) - Counter(on_page)
    assert not missing, missing

    # each of the three grid pages heads Customer and Job and prints Total
    by_page = {}
    for page, text, *_ in runs:
        by_page.setdefault(page, []).append(text)
    for n in (1, 2, 3):
        pages = [
            p
            for p, texts in by_page.items()
            if any(f"page {n} of 3" in t for t in texts)
        ]
        assert len(pages) == 1, (n, pages)
        texts = by_page[pages[0]]
        assert "CUSTOMER" in texts and "JOB" in texts, (n, texts[:20])
        assert "Total" in texts, (n, texts[-20:])
    # the posted-entry section still prints after the grid
    assert any("WIP Adjusting Entry" in t for t in on_page)
