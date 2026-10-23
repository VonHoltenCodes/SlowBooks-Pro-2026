"""The Accountant's Package (#343): one zip for a period, for the owner
who sends the books out instead of giving a login.

What a reviewer asks for one report at a time comes in one download: the
Financial Statements Pack, the General Ledger and Trial Balance as CSV,
the A/R and A/P agings, each bank reconciliation completed in the period,
the Adjusting Journal Entries and Closing Date Exceptions reports, and the
audit log since the closing date. Each file is the same report run on its
own, byte for byte, with a README that says what is inside and when it was
made.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime, timezone

from fastapi import Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.models.audit import AuditLog
from app.models.banking import Reconciliation, ReconciliationStatus
from app.routes.reports._router import router
from app.services.csv_export import _SafeWriter
from app.services.terminology import terms_from_db


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text or "").strip("-") or "company"


def _money(v):
    from app.services.ledger_exports import _money as m

    return m(v)


def _preamble(w, company: str, report: str, period: str) -> None:
    w.writerow(["Company", company])
    w.writerow(["Report", report])
    w.writerow(["Period", period])
    w.writerow([])


def aging_csv(data: dict, company: str, side: str) -> str:
    """The A/R or A/P aging as the screen shows it: a row per customer or
    vendor with the buckets and the total, then the totals row."""
    out = io.StringIO()
    w = _SafeWriter(out)
    name_key = "customer_name" if side == "ar" else "vendor_name"
    title = "Accounts Receivable Aging" if side == "ar" else "Accounts Payable Aging"
    _preamble(w, company, title, f"as of {data['as_of_date']}")
    head = [
        "Customer" if side == "ar" else "Vendor",
        "Current",
        "1-30",
        "31-60",
        "61-90+",
        "Total",
    ]
    if side == "ar":
        head.append("Unapplied credits")
    w.writerow(head)
    for i in data.get("items", []):
        row = [
            i.get(name_key) or "",
            _money(i.get("current")),
            _money(i.get("over_30")),
            _money(i.get("over_60")),
            _money(i.get("over_90")),
            _money(i.get("total")),
        ]
        if side == "ar":
            row.append(_money(i.get("unapplied_credits")))
        w.writerow(row)
    t = data.get("totals") or {}
    row = ["Total"] + [
        _money(t.get(k)) for k in ("current", "over_30", "over_60", "over_90", "total")
    ]
    if side == "ar":
        row.append(_money(t.get("unapplied_credits")))
    w.writerow(row)
    return out.getvalue()


def sections_csv(sections: list[dict], company: str) -> str:
    """Any report's PDF sections as a CSV: each section's title and period,
    its columns, its rows (the reconciliation report's)."""
    out = io.StringIO()
    w = _SafeWriter(out)
    w.writerow(["Company", company])
    for s in sections:
        w.writerow(["Report", s.get("title") or ""])
        w.writerow(["Period", s.get("period") or ""])
        w.writerow([])
        w.writerow(s.get("columns") or [])
        for r in s.get("rows") or []:
            w.writerow(r.get("cells") or [])
        w.writerow([])
    return out.getvalue()


def audit_csv(
    rows: list,
    company: str,
    since: str,
    until: str,
    names: dict | None = None,
    tz_offset: int = 0,
) -> str:
    """The audit log's rows since the closing date: when (on the person's
    clock), who, the record, the action and the fields it moved, before and
    after; secrets redacted, accounts and classes by name."""
    from app.services.closing_date_exceptions import (
        fields_of,
        label_of,
        local_words,
        record_word,
        zone_words,
    )

    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Audit log", f"{since} to {until}")
    w.writerow(["Times in", zone_words(tz_offset)])
    w.writerow(["When", "Who", "Record", "Action", "Field", "Before", "After"])
    for row in rows:
        values = row.new_values if row.action != "DELETE" else row.old_values
        record = f"{record_word(row.table_name)} {row.record_id}"
        label = label_of(values)
        if label:
            record += f" ({label})"
        when = local_words(row.timestamp, tz_offset)
        fields = fields_of(row, names)
        if not fields:
            w.writerow(
                [when, row.username or "", record, row.action.lower(), "", "", ""]
            )
        for f in fields:
            w.writerow(
                [
                    when,
                    row.username or "",
                    record,
                    row.action.lower(),
                    f["field"],
                    f["before"],
                    f["after"],
                ]
            )
    return out.getvalue()


def build_package(
    db: Session, start_date: date, end_date: date, tz_offset: int = 0
) -> tuple[bytes, dict]:
    """The zip and its manifest (what went in, what did not and why)."""
    from app.routes.reports.financial import (
        _bs_section,
        _company_name,
        _pl_section,
        _tb_section,
        adjusting_entries,
        balance_sheet,
        general_ledger,
        profit_loss,
        trial_balance,
    )
    from app.routes.reports.payables_tax import ap_aging_report
    from app.routes.reports.receivables import ar_aging_report
    from app.services import reconciliation as rc
    from app.services.closing_date import get_closing_date
    from app.services.closing_date_exceptions import (
        closing_date_exceptions,
        exceptions_csv,
    )
    from app.services.ledger_exports import (
        adjusting_entries_csv,
        general_ledger_csv,
        trial_balance_csv,
    )

    company = _company_name(db)
    t = terms_from_db(db)
    s, e = start_date.isoformat(), end_date.isoformat()
    files: list[tuple[str, bytes | str, str]] = []  # name, content, one line about it
    notes: list[str] = []

    # 1. the statements pack
    pl = profit_loss(start_date, end_date, db)
    bs = balance_sheet(end_date, db)
    tb = trial_balance(start_date, end_date, db)
    try:
        from app.services.pdf_service import generate_report_pdf
        from app.services.settings_service import get_all_settings

        if t.is_nonprofit:
            from app.routes.reports.nonprofit import nonprofit_statement_sections

            sections = nonprofit_statement_sections(db, start_date, end_date) + [
                _tb_section(tb)
            ]
        else:
            sections = [_pl_section(pl, t), _bs_section(bs, t), _tb_section(tb)]
        pdf = generate_report_pdf(sections, get_all_settings(db))
        files.append(
            (
                f"financial-statements_{s}_{e}.pdf",
                pdf,
                "the Financial Statements Pack: Profit & Loss, Balance Sheet and Trial Balance",
            )
        )
    except Exception as exc:  # no PDF renderer on this install
        notes.append(
            "The Financial Statements Pack PDF could not be made on this install "
            f"({type(exc).__name__}); the Trial Balance CSV carries the same figures."
        )

    # 2. the ledger
    gl = general_ledger(start_date, end_date, None, db)
    files.append(
        (
            f"general-ledger_{s}_{e}.csv",
            general_ledger_csv(gl, company, t),
            "the General Ledger: every line, by account, with running balances",
        )
    )
    files.append(
        (
            f"trial-balance_{s}_{e}.csv",
            trial_balance_csv(tb, company),
            "the Trial Balance for the period",
        )
    )

    # 3. the agings, as of the period's last day
    files.append(
        (
            f"ar-aging_{e}.csv",
            aging_csv(ar_aging_report(db, end_date), company, "ar"),
            "Accounts Receivable Aging as of the period's last day",
        )
    )
    files.append(
        (
            f"ap-aging_{e}.csv",
            aging_csv(ap_aging_report(db, end_date), company, "ap"),
            "Accounts Payable Aging as of the period's last day",
        )
    )

    # 4. each bank reconciliation completed in the period
    recons = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.status == ReconciliationStatus.COMPLETED,
            Reconciliation.statement_date >= start_date,
            Reconciliation.statement_date <= end_date,
        )
        .order_by(Reconciliation.statement_date, Reconciliation.id)
        .all()
    )
    for recon in recons:
        try:
            data = rc.report(db, recon)
        except Exception:  # a reconciliation from before 2.10, no ledger account
            continue
        label = _slug(data.get("account_number") or "") or str(data.get("account_id"))
        files.append(
            (
                f"reconciliation_{label}_{data['statement_date']}.csv",
                sections_csv(rc.report_sections(data), company),
                f"the bank reconciliation of {data.get('account_name') or label} "
                f"for the statement ending {data['statement_date']}",
            )
        )
    if not recons:
        notes.append("No bank reconciliation was completed in the period.")

    # 5. the accountant's own reports
    adj = adjusting_entries(start_date, end_date, db)
    files.append(
        (
            f"adjusting-entries_{s}_{e}.csv",
            adjusting_entries_csv(adj, company),
            f"Adjusting Journal Entries: {adj['count']} in the period",
        )
    )
    exc_data = closing_date_exceptions(db, start_date, end_date, tz_offset)
    files.append(
        (
            f"closing-date-exceptions_{s}_{e}.csv",
            exceptions_csv(exc_data, company),
            f"Closing Date Exceptions: {exc_data['count']} changes made into a closed period with the password",
        )
    )

    # 6. the audit log since the closing date (or the period, with none set)
    closing = get_closing_date(db)
    # from the closing date when it falls inside the period, else the
    # period itself (a closing date after the period named an empty,
    # reversed range: 2.24.0 gate, NEW-154)
    since = closing if closing and start_date <= closing <= end_date else start_date
    from app.services.closing_date_exceptions import record_names, utc_bounds

    since_utc, until_utc = utc_bounds(since, end_date, tz_offset)
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.timestamp >= since_utc, AuditLog.timestamp < until_utc)
        .order_by(AuditLog.id)
        .all()
    )
    files.append(
        (
            f"audit-log_{since.isoformat()}_{e}.csv",
            audit_csv(rows, company, since.isoformat(), e, record_names(db), tz_offset),
            f"the audit log from {since.isoformat()} ({len(rows)} changes): who changed what, and when"
            + (" (the closing date)" if closing and since == closing else ""),
        )
    )

    made = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    readme = [
        f"{company or 'SlowBooks Pro'} — Accountant's Package",
        f"Period {s} to {e}. Made {made} by SlowBooks Pro {__version__}.",
        "",
        "Each file is the same report run on its own in SlowBooks Pro, for the same dates.",
        "Amounts are in home currency with two decimals; CSVs open in any spreadsheet.",
        "",
        "Files:",
    ]
    for name, _, about in files:
        readme.append(f"  {name}: {about}")
    if closing:
        readme += ["", f"Closing date in force: {closing.isoformat()}."]
    if notes:
        readme += ["", "Notes:"] + [f"  {n}" for n in notes]
    readme_text = "\n".join(readme) + "\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme_text)
        for name, content, _ in files:
            z.writestr(
                name,
                content if isinstance(content, bytes) else content.encode("utf-8-sig"),
            )
    manifest = {
        "company": company,
        "start_date": s,
        "end_date": e,
        "files": ["README.txt"] + [name for name, _, _ in files],
        "notes": notes,
        "closing_date": closing.isoformat() if closing else "",
    }
    return buf.getvalue(), manifest


@router.get("/accountants-package")
def accountants_package(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    tz_offset: int = Query(default=0, ge=-900, le=900),
    db: Session = Depends(get_db),
):
    """One zip for the period (#343): the statements pack, the General
    Ledger and Trial Balance, the agings, the reconciliations, the
    Adjusting Journal Entries and Closing Date Exceptions reports and the
    audit log since the closing date, with a README. Saved like every
    other download."""
    from app.routes.csv import _DESKTOP_HEADER
    from app.routes.reports.financial import _company_name, _range_or_year

    start_date, end_date = _range_or_year(db, start_date, end_date)
    content, manifest = build_package(db, start_date, end_date, tz_offset)
    name = f"accountants-package_{_slug(_company_name(db))}_{manifest['start_date']}_{manifest['end_date']}.zip"
    disposition = "inline" if request.headers.get(_DESKTOP_HEADER) else "attachment"
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"{disposition}; filename={name}",
            "X-Package-Files": json.dumps(manifest["files"]),
        },
    )


@router.get("/accountants-package/manifest")
def accountants_package_manifest(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    tz_offset: int = Query(default=0, ge=-900, le=900),
    db: Session = Depends(get_db),
):
    """What the package for these dates would hold, for the page to say
    before the download (the zip is built once, to list it)."""
    from app.routes.reports.financial import _range_or_year

    start_date, end_date = _range_or_year(db, start_date, end_date)
    _, manifest = build_package(db, start_date, end_date, tz_offset)
    return manifest
