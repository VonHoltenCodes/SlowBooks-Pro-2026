# ============================================================================
# Past Customers (#436) — a mailing list built from the books.
#
# One row per customer who has at least one non-void invoice or a job: the
# service address (the job's site_address, else ship-to, else bill-to), the
# first and last job dates, the job count, lifetime sales, and the last
# job's type and name. The same filtered set drives the JSON view, a
# mail-merge CSV, and an Avery 5160 labels PDF.
#
# "Past customer" is what is already in the books — no new column, no
# migration. Do Not Contact is read from the customer's notes (#436 §4):
# the customer record has no opt-out field of its own.
# ============================================================================

import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.jobs import Job
from app.routes.csv import _csv_response
from app.services.accounting import _q
from app.services.addresses import city_line
from app.services.contact_balances import ZERO, home_amount
from app.services.csv_export import _SafeWriter

router = APIRouter(prefix="/api/customers", tags=["customers"])

# The state and ZIP that end a free-text address: "TX 78701", "TX, 78701",
# "tx 78701-1234". What comes before them is the city, after the last comma
# when the street is on the same line ("123 Main St, Austin, TX 78701").
_STATE_ZIP = re.compile(
    r"(?:^|[\s,])(?P<state>[A-Za-z]{2})\.?,?\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$"
)

# A note that opts the customer out of mail: "do not contact", "don't
# contact", "do-not-contact", "do not mail", or DNC as a word. "No contact"
# is not one: "No contact person on site" is a note about the job.
_DO_NOT_CONTACT = re.compile(
    r"\bdo(?:[\s-]+not|n['\u2019]?t)[\s-]+(?:contact|mail)\b|\bDNC\b",
    re.IGNORECASE,
)


def _parse_site_address(text: str) -> dict:
    """A job's free-text site_address split for a mailing label.

    The state and ZIP are read off the end of the last line; the city is
    what precedes them on that line, after its last comma, so a one-line
    "123 Main St, Austin, TX 78701" gives Address 1 "123 Main St" and city
    Austin as the three-line form does. The street lines are every line
    and comma part before the city: Address 1 is the first, Address 2 the
    rest joined with commas, so a third line is kept rather than dropped.
    A ZIP+4 is kept whole. A last line with no state and ZIP is a street
    line, and the city, state and ZIP come back blank."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    service = ", ".join(lines)
    city = state = zip_code = ""
    street = lines[:]
    if lines:
        m = _STATE_ZIP.search(lines[-1])
        if m:
            head = lines[-1][: m.start()].strip().rstrip(",").strip()
            parts = [p.strip() for p in head.split(",") if p.strip()]
            if len(parts) == 1 and len(lines) == 1 and parts[0][:1].isdigit():
                # "123 Main St Austin TX 78701": no comma says where the
                # street ends, so the city is not guessed at
                parts = [parts[0], ""]
            city = parts.pop() if parts else ""
            state = m.group("state").upper()
            zip_code = m.group("zip")
            street = lines[:-1] + [p for p in parts if p]
    return {
        "service_address": service,
        "address1": street[0] if street else "",
        "address2": ", ".join(street[1:]),
        "city": city,
        "state": state,
        "zip": zip_code,
        "source": "job",
    }


def _address_from_customer(c: Customer, ship: bool) -> dict:
    """Ship-to or bill-to fields assembled into the same row shape."""
    if ship:
        a1, a2 = c.ship_address1, c.ship_address2
        city, state, zc = c.ship_city, c.ship_state, c.ship_zip
        source = "ship"
    else:
        a1, a2 = c.bill_address1, c.bill_address2
        city, state, zc = c.bill_city, c.bill_state, c.bill_zip
        source = "bill"
    parts = [p for p in (a1, a2, city_line(city, state, zc)) if p]
    return {
        "service_address": ", ".join(parts),
        "address1": a1 or "",
        "address2": a2 or "",
        "city": city or "",
        "state": state or "",
        "zip": zc or "",
        "source": source,
    }


def _ship_set(c: Customer) -> bool:
    return any(
        (
            c.ship_address1,
            c.ship_address2,
            c.ship_city,
            c.ship_state,
            c.ship_zip,
        )
    )


def _service_address(c: Customer, jobs: list[Job]) -> dict:
    """The job's site_address wins (the latest job that has one), else
    ship-to if any ship field is set, else bill-to (#436 §2)."""
    with_addr = [j for j in jobs if (j.site_address or "").strip()]
    if with_addr:
        # the latest by end_date; a job with no end_date sorts oldest
        latest = max(with_addr, key=lambda j: (j.end_date or date.min, j.id))
        return _parse_site_address(latest.site_address)
    if _ship_set(c):
        return _address_from_customer(c, ship=True)
    return _address_from_customer(c, ship=False)


def _split_multi(values: list[str] | None) -> list[str]:
    """A repeatable query param that also accepts a comma-joined value."""
    out = []
    for v in values or []:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out


def _for_customers(query, active: str):
    """``query`` (already joined to Customer) cut to the customers asked
    for: all, active or inactive."""
    if active == "active":
        return query.filter(Customer.is_active)
    if active == "inactive":
        return query.filter(Customer.is_active.is_(False))
    return query


def _build_rows(db: Session, *, active: str) -> list[dict]:
    """Every customer with a non-void invoice or a job, with the figures the
    view, CSV and labels all share. Filters are applied by the caller so the
    three endpoints agree on the set.

    The invoices and credit memos are summed in SQL, one row per customer
    (and booked rate), and every query is joined to the customers rather
    than given a list of their ids: an IN list with one parameter per
    customer runs past SQLite's limit on a large customer list."""
    customers = _for_customers(db.query(Customer), active)
    customers = customers.order_by(Customer.name).all()

    jobs_by_customer: dict[int, list[Job]] = {}
    jobs = _for_customers(
        db.query(Job).join(Customer, Job.customer_id == Customer.id), active
    )
    for j in jobs.all():
        jobs_by_customer.setdefault(j.customer_id, []).append(j)

    # count, first and last date, and sales in home currency per customer;
    # grouped by booked rate too, so each foreign total converts at its own
    invoice_figures: dict[int, dict] = {}
    inv = _for_customers(
        db.query(
            Invoice.customer_id,
            Invoice.exchange_rate,
            func.count(Invoice.id),
            func.min(Invoice.date),
            func.max(Invoice.date),
            func.sum(Invoice.subtotal),
        )
        .join(Customer, Invoice.customer_id == Customer.id)
        .filter(Invoice.status != InvoiceStatus.VOID),
        active,
    ).group_by(Invoice.customer_id, Invoice.exchange_rate)
    for cid, rate, count, first, last, subtotal in inv.all():
        f = invoice_figures.setdefault(
            cid, {"count": 0, "first": None, "last": None, "sales": ZERO}
        )
        f["count"] += count
        f["first"] = min(d for d in (f["first"], first) if d)
        f["last"] = max(d for d in (f["last"], last) if d)
        f["sales"] += home_amount(subtotal, rate)

    # Lifetime sales are net of the credit memos given back (a return or an
    # allowance); a write-off forgives a balance to Bad Debt and is not a
    # sale reversed, as the Sales Tax report has it. Credit memos are in
    # the company's currency.
    credits: dict[int, Decimal] = {}
    cm = _for_customers(
        db.query(CreditMemo.customer_id, func.sum(CreditMemo.subtotal))
        .join(Customer, CreditMemo.customer_id == Customer.id)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.is_write_off.isnot(True))
        # nor is an early-payment discount (#664)
        .filter(CreditMemo.discount_payment_id.is_(None)),
        active,
    ).group_by(CreditMemo.customer_id)
    for cid, subtotal in cm.all():
        credits[cid] = _q(Decimal(str(subtotal or 0)))

    rows = []
    for c in customers:
        jobs = jobs_by_customer.get(c.id, [])
        figures = invoice_figures.get(c.id)
        if not jobs and not figures:
            continue  # not a past customer: nothing is in the books for them

        dates = [figures["first"], figures["last"]] if figures else []
        dates += [j.end_date for j in jobs if j.end_date]
        first_job = min(dates) if dates else None
        last_job = max(dates) if dates else None

        # distinct jobs; with no job records, each invoice counts as one job
        # so a service company that only invoices still shows its work (§3)
        job_count = len(jobs) if jobs else figures["count"]

        lifetime = (figures["sales"] if figures else ZERO) - credits.get(c.id, ZERO)

        last_type = last_name = ""
        if jobs:
            latest_job = max(jobs, key=lambda j: (j.end_date or date.min, j.id))
            last_type = latest_job.job_type or ""
            last_name = latest_job.name or ""

        addr = _service_address(c, jobs)
        rows.append(
            {
                "customer_id": c.id,
                "name": c.name,
                "company": c.company or "",
                "phone": c.phone or "",
                "email": c.email or "",
                "service_address": addr["service_address"],
                "address1": addr["address1"],
                "address2": addr["address2"],
                "city": addr["city"],
                "state": addr["state"],
                "zip": addr["zip"],
                "address_source": addr["source"],
                "first_job": first_job.isoformat() if first_job else None,
                "last_job": last_job.isoformat() if last_job else None,
                "_last_job_date": last_job,
                "job_count": job_count,
                "lifetime_sales": lifetime,
                "last_job_type": last_type,
                "last_job_name": last_name,
                "is_active": c.is_active,
                "do_not_contact": bool(_DO_NOT_CONTACT.search(c.notes or "")),
                "donor_type": (c.donor_type or ""),
            }
        )
    return rows


def _filtered(
    rows: list[dict],
    *,
    last_job_from: date | None,
    last_job_to: date | None,
    cities: list[str],
    zips: list[str],
    state: str | None,
    job_type: str | None,
    customer_type: str | None,
    min_sales: Decimal | None,
) -> list[dict]:
    cities_l = {c.lower() for c in cities}
    state_l = (state or "").strip().lower()
    job_type_l = (job_type or "").strip().lower()
    cust_type_l = (customer_type or "").strip().lower()
    out = []
    for r in rows:
        ljd = r["_last_job_date"]
        if last_job_from and (not ljd or ljd < last_job_from):
            continue
        if last_job_to and (not ljd or ljd > last_job_to):
            continue
        if cities_l and r["city"].lower() not in cities_l:
            continue
        if zips and not any(r["zip"].startswith(z) for z in zips):
            continue
        if state_l and r["state"].lower() != state_l:
            continue
        if job_type_l and r["last_job_type"].lower() != job_type_l:
            continue
        if cust_type_l and r["donor_type"].lower() != cust_type_l:
            continue
        if min_sales is not None and r["lifetime_sales"] < min_sales:
            continue
        out.append(r)
    return out


def _collect(
    db: Session,
    *,
    last_job_from,
    last_job_to,
    city,
    zip,
    state,
    job_type,
    customer_type,
    min_sales,
    active,
) -> list[dict]:
    rows = _build_rows(db, active=active)
    return _filtered(
        rows,
        last_job_from=last_job_from,
        last_job_to=last_job_to,
        cities=_split_multi(city),
        zips=_split_multi(zip),
        state=state,
        job_type=job_type,
        customer_type=customer_type,
        min_sales=min_sales,
    )


# Shared query parameters for all three endpoints, so the CSV and labels
# export exactly what the view shows.
_P_FROM = Query(default=None, description="last job on/after this date")
_P_TO = Query(default=None, description="last job on/before this date")
_P_CITY = Query(default=None, description="service city; repeatable or comma-joined")
_P_ZIP = Query(default=None, description="service zip; prefix match, repeatable")
_P_STATE = Query(default=None)
_P_JOB_TYPE = Query(default=None)
_P_CUST_TYPE = Query(default=None, description="customer type (donor_type)")
_P_MIN_SALES = Query(default=None, description="lifetime sales at least")
_P_ACTIVE = Query(default="all", pattern="^(all|active|inactive)$")


def _public(row: dict) -> dict:
    """The row without the internal sort key, money as a float for JSON."""
    out = {k: v for k, v in row.items() if not k.startswith("_") and k != "donor_type"}
    out["lifetime_sales"] = float(row["lifetime_sales"])
    return out


@router.get("/past")
def past_customers(
    last_job_from: date = _P_FROM,
    last_job_to: date = _P_TO,
    city: list[str] = _P_CITY,
    zip: list[str] = _P_ZIP,
    state: str = _P_STATE,
    job_type: str = _P_JOB_TYPE,
    customer_type: str = _P_CUST_TYPE,
    min_sales: Decimal = _P_MIN_SALES,
    active: str = _P_ACTIVE,
    db: Session = Depends(get_db),
):
    """Past Customers (#436): one row per customer the books know, with the
    service address and last job date a mailing list is built from. Rows
    marked Do Not Contact (from the notes) are returned with the flag set;
    the CSV and labels leave them out unless asked."""
    rows = _collect(
        db,
        last_job_from=last_job_from,
        last_job_to=last_job_to,
        city=city,
        zip=zip,
        state=state,
        job_type=job_type,
        customer_type=customer_type,
        min_sales=min_sales,
        active=active,
    )
    total_sales = float(sum((r["lifetime_sales"] for r in rows), ZERO))
    return {
        "rows": [_public(r) for r in rows],
        "total_rows": len(rows),
        "total_sales": total_sales,
        "filters": {
            "last_job_from": last_job_from.isoformat() if last_job_from else None,
            "last_job_to": last_job_to.isoformat() if last_job_to else None,
            "city": _split_multi(city),
            "zip": _split_multi(zip),
            "state": state,
            "job_type": job_type,
            "customer_type": customer_type,
            "min_sales": float(min_sales) if min_sales is not None else None,
            "active": active,
            "include_do_not_contact": False,
        },
    }


_CSV_HEADER = [
    "Name",
    "Company",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "Zip",
    "Service address",
    "Phone",
    "Email",
    "First job",
    "Last job",
    "Jobs",
    "Lifetime sales",
    "Last job type",
    "Last job name",
]


def _mailable(rows: list[dict], include_do_not_contact: bool) -> list[dict]:
    if include_do_not_contact:
        return rows
    return [r for r in rows if not r["do_not_contact"]]


def _csv_filename(last_job_from: date | None, last_job_to: date | None) -> str:
    frm = last_job_from.isoformat() if last_job_from else "all"
    to = last_job_to.isoformat() if last_job_to else "all"
    return f"Past-Customers_{frm}_{to}.csv"


@router.get("/past.csv")
def past_customers_csv(
    request: Request,
    last_job_from: date = _P_FROM,
    last_job_to: date = _P_TO,
    city: list[str] = _P_CITY,
    zip: list[str] = _P_ZIP,
    state: str = _P_STATE,
    job_type: str = _P_JOB_TYPE,
    customer_type: str = _P_CUST_TYPE,
    min_sales: Decimal = _P_MIN_SALES,
    active: str = _P_ACTIVE,
    include_do_not_contact: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """The same set in a mail-merge shape (address parts in their own
    columns). Do Not Contact rows are left out unless asked (#436 §5)."""
    import io

    rows = _mailable(
        _collect(
            db,
            last_job_from=last_job_from,
            last_job_to=last_job_to,
            city=city,
            zip=zip,
            state=state,
            job_type=job_type,
            customer_type=customer_type,
            min_sales=min_sales,
            active=active,
        ),
        include_do_not_contact,
    )
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow(
            [
                r["name"],
                r["company"],
                r["address1"],
                r["address2"],
                r["city"],
                r["state"],
                r["zip"],
                r["service_address"],
                r["phone"],
                r["email"],
                r["first_job"] or "",
                r["last_job"] or "",
                r["job_count"],
                r["lifetime_sales"].quantize(Decimal("0.01")),
                r["last_job_type"],
                r["last_job_name"],
            ]
        )
    return _csv_response(
        output.getvalue(), _csv_filename(last_job_from, last_job_to), request
    )


@router.get("/past/labels.pdf")
def past_customers_labels(
    last_job_from: date = _P_FROM,
    last_job_to: date = _P_TO,
    city: list[str] = _P_CITY,
    zip: list[str] = _P_ZIP,
    state: str = _P_STATE,
    job_type: str = _P_JOB_TYPE,
    customer_type: str = _P_CUST_TYPE,
    min_sales: Decimal = _P_MIN_SALES,
    active: str = _P_ACTIVE,
    include_do_not_contact: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Avery 5160 labels (30 up) with the service address (#436 §6). Do Not
    Contact rows are left out unless asked."""
    from app.services import pdf_service

    rows = _mailable(
        _collect(
            db,
            last_job_from=last_job_from,
            last_job_to=last_job_to,
            city=city,
            zip=zip,
            state=state,
            job_type=job_type,
            customer_type=customer_type,
            min_sales=min_sales,
            active=active,
        ),
        include_do_not_contact,
    )
    labels = []
    for r in rows:
        company = r["company"]
        name = r["name"]
        labels.append(
            {
                "line1": company or name,
                "line2": name if company else "",
                "address1": r["address1"],
                "address2": r["address2"],
                "city_line": city_line(r["city"], r["state"], r["zip"]),
            }
        )
    pdf = pdf_service.generate_labels_pdf(labels)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Past-Customers-labels.pdf"'},
    )
