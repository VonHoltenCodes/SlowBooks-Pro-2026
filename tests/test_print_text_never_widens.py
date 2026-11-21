"""Printed text never widens a column, and nothing prints past the margin.

A deposit slip whose payer had a long name printed its Amount column and
Total deposit off the paper; a giving statement did the same on a payment
reference, a pay application on a schedule-of-values description, and an
invoice, estimate, credit memo, purchase order or bill on one long unbroken
word in a line's description (a tracking address, a hash). Each was the same
fault: a text cell with a word longer than its column took the width from
the columns beside it.

The rule every printed template now follows (app/templates/_print_text.html):
a word longer than the room it has breaks inside the word, a text cell never
widens its column, number columns keep their width, and nothing is laid out
past the page margin. The renderer sets every word of 20 characters or more
in an element that may break at any character
(pdf_service.break_long_words), so no template and no field can be missed.

Every printed template is drawn here three times from the same records:
with ordinary text, with many ordinary words, and with an unbreakable word
of 120 characters (or the most the field takes) in every field a person
types, and laid out as the PDF is. No ink may pass the right or left margin,
every figure must end at the x it ends at with ordinary text, and the
page's own words (its labels, headings, dates and figures) are never broken.
Chromium's half is tests/test_print_r24_browser.py's.
"""

import re
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
# what is not a printed page: the rule itself, an email's body and the web
# page a customer pays on (the portal's pages are in a folder of their own)
NOT_PRINTED = {"_print_text.html", "invoice_email.html", "public_pay.html"}
LONG = 120
MONEY = re.compile(r"^-?(?:[A-Z]{3} -?|\$)[\d,]+\.\d{2}$")


def _long(tag, most=LONG):
    """An unbreakable word that starts with the field's name."""
    return (tag + "X" * LONG)[:most]


def _ordinary(tag, most=LONG):
    return tag.capitalize()[:most]


WORDS = "Northwest Regional Medical Center Foundation Auxiliary of Greater Springfield"


def _wordy(tag, most=LONG):
    """Ordinary words, as many as a long name or a long memo has."""
    return (tag.capitalize() + " " + WORDS)[:most].rstrip()


def _path(body, loc):
    """The dict or list holding the value at a validation error's ``loc``,
    and its last key."""
    at = body
    for key in loc[:-1]:
        at = at[key]
    return at, loc[-1]


def send(client, method, url, body, ok=(200, 201)):
    """Send ``body``; a field the API says is too long is cut to the most it
    takes and sent again, so every field carries the longest word it can."""
    for _ in range(40):
        r = getattr(client, method)(url, json=body)
        if r.status_code != 422:
            break
        errors = r.json().get("detail")
        if not isinstance(errors, list):
            break
        cut = False
        for err in errors:
            if err.get("type") != "string_too_long":
                continue
            at, key = _path(body, [p for p in err["loc"] if p != "body"])
            at[key] = at[key][: err["ctx"]["max_length"]]
            cut = True
        if not cut:
            break
    assert r.status_code in ok, (url, r.status_code, r.text[:600])
    return r.json()


# --- the records -------------------------------------------------------------


def _settings(client, w, **more):
    body = {
        "company_name": w("coname"),
        "display_name": w("codisplay"),
        "company_address1": w("coaddrone"),
        "company_address2": w("coaddrtwo"),
        "company_city": w("cocity"),
        "company_state": w("cost", 20),
        "company_zip": w("cozip", 20),
        "company_phone": w("cophone", 40),
        "company_email": w("comail", 60) + "@example.com",
        "company_website": w("coweb"),
        "company_tax_id": w("cotaxid", 30),
        "company_license": w("colicense"),
        "invoice_footer": w("footer"),
        "estimate_terms": w("estterms"),
        "operator_name": w("operator"),
        **more,
    }
    send(client, "put", "/api/settings", body)


def _customer(client, w, tag="cust"):
    body = {
        "name": w(tag + "name"),
        "company": w(tag + "company"),
        "salutation": w(tag + "salute"),
        "phone": w(tag + "phone"),
        "bill_address1": w(tag + "billone"),
        "bill_address2": w(tag + "billtwo"),
        "bill_city": w(tag + "billcity"),
        "bill_state": w(tag + "billst"),
        "bill_zip": w(tag + "billzip"),
        "ship_address1": w(tag + "shipone"),
        "ship_address2": w(tag + "shiptwo"),
        "ship_city": w(tag + "shipcity"),
        "ship_state": w(tag + "shipst"),
        "ship_zip": w(tag + "shipzip"),
        "terms": w(tag + "terms"),
        "notes": w(tag + "notes"),
    }
    return send(client, "post", "/api/customers?force=true", body)["id"]


def _vendor(client, w, tag="vend", **more):
    body = {
        "name": w(tag + "name"),
        "company": w(tag + "company"),
        "phone": w(tag + "phone"),
        "address1": w(tag + "addrone"),
        "address2": w(tag + "addrtwo"),
        "city": w(tag + "city"),
        "state": w(tag + "st"),
        "zip": w(tag + "zip"),
        "terms": w(tag + "terms"),
        "account_number": w(tag + "acct"),
        "tax_id": w(tag + "taxid"),
        "notes": w(tag + "notes"),
        **more,
    }
    return send(client, "post", "/api/vendors?force=true", body)["id"]


def _lines(w, **extra):
    """Three lines: the long word alone, in the middle of a sentence, and
    after a typed line break."""
    return [
        {"description": w("descone"), "quantity": 1, "rate": "12.34", **extra},
        {
            "description": "Deliver " + w("desctwo") + " to the site",
            "quantity": 2,
            "rate": "345678.90",
            **extra,
        },
        {
            "description": "Scope\n" + w("descthree"),
            "quantity": 1,
            "rate": "4567.89",
            **extra,
        },
    ]


def invoice(client, acc, db, w):
    c = _customer(client, w)
    made = []
    for tax in ("0.08875", "0"):
        doc = send(
            client,
            "post",
            "/api/invoices",
            {
                "customer_id": c,
                "date": "2026-06-01",
                "tax_rate": tax,
                "po_number": w("ponumber"),
                "terms": w("terms"),
                "notes": w("notes"),
                "lines": _lines(w),
            },
        )
        made.append(f"/api/invoices/{doc['id']}/pdf")
    # one part paid: Paid and Balance Due join the totals
    send(
        client,
        "post",
        "/api/payments",
        {
            "customer_id": c,
            "date": "2026-06-02",
            "amount": "100.00",
            "reference": w("payref"),
            "notes": w("paynotes"),
            "allocations": [{"invoice_id": doc["id"], "amount": "100.00"}],
        },
    )
    return made


def sales_receipt(client, acc, db, w):
    doc = send(
        client,
        "post",
        "/api/sales-receipts",
        {
            "customer_id": _customer(client, w),
            "date": "2026-05-09",
            "tax_rate": "0.08875",
            "method": "Check",
            "check_number": w("checkno"),
            "reference": w("reference"),
            "notes": w("notes"),
            "lines": _lines(w),
        },
    )
    return [f"/api/invoices/{doc['invoice']['id']}/pdf"]


def estimate(client, acc, db, w):
    doc = send(
        client,
        "post",
        "/api/estimates",
        {
            "customer_id": _customer(client, w),
            "date": "2026-06-01",
            "tax_rate": "0.08875",
            "notes": w("notes"),
            "terms": w("terms"),
            "lines": _lines(w),
        },
    )
    return [f"/api/estimates/{doc['id']}/pdf"]


def _construction_estimate(client, w, layout):
    from tests import test_pdf_last_page_1045_1046 as c1046

    c1046._construction(client)
    lines, _last = c1046.construction_lines(client, layout, 9)
    for i, line in enumerate(lines):
        line["description"] = (
            w(f"scope{i}") if i % 2 else "Furnish " + w(f"scope{i}") + " complete"
        )
        if "unit" in line:
            line["unit"] = w("unit")
    doc = send(
        client,
        "post",
        "/api/estimates",
        {
            "customer_id": _customer(client, w),
            "date": "2026-06-01",
            "notes": w("notes"),
            "terms": w("termsone") + "\n" + w("termstwo"),
            "lines": lines,
        },
    )
    return [f"/api/estimates/{doc['id']}/pdf"]


def priced_construction_estimate(client, acc, db, w):
    return _construction_estimate(client, w, "line_markup")


def bid(client, acc, db, w):
    return _construction_estimate(client, w, "bid")


def bid_with_tables(client, acc, db, w):
    return _construction_estimate(client, w, "bid tables")


def credit_memo(client, acc, db, w):
    doc = send(
        client,
        "post",
        "/api/credit-memos",
        {
            "customer_id": _customer(client, w),
            "date": "2026-06-01",
            "tax_rate": "0.08875",
            "notes": w("notes"),
            "lines": _lines(w),
        },
    )
    return [f"/api/credit-memos/{doc['id']}/pdf"]


def purchase_order(client, acc, db, w):
    doc = send(
        client,
        "post",
        "/api/purchase-orders",
        {
            "vendor_id": _vendor(client, w),
            "date": "2026-06-01",
            "notes": w("notes"),
            "ship_to": w("shiptoone") + "\n" + w("shiptotwo"),
            "scope_notes": w("scopenotes"),
            "lines": _lines(w),
        },
    )
    return [f"/api/purchase-orders/{doc['id']}/pdf"]


def bill(client, acc, db, w):
    lines = [
        ln | {"amount": str(float(ln["rate"]) * ln["quantity"])}
        for ln in _lines(w, account_id=acc["6000"].id)
    ]
    doc = send(
        client,
        "post",
        "/api/bills",
        {
            "vendor_id": _vendor(client, w),
            "bill_number": w("billno"),
            "date": "2026-06-01",
            "terms": w("terms"),
            "ref_number": w("refno"),
            "notes": w("notes"),
            "lines": lines,
        },
    )
    return [f"/api/bills/{doc['id']}/pdf"]


def check(client, acc, db, w):
    """A bill payment's check, on each kind of stock."""
    vendor = _vendor(client, w)
    made = []
    for n in range(3):
        doc = send(
            client,
            "post",
            "/api/bills",
            {
                "vendor_id": vendor,
                "bill_number": w(f"billno{n}"),
                "date": "2026-06-01",
                "notes": w("notes"),
                "lines": [
                    {
                        "account_id": acc["6000"].id,
                        "description": w("descone"),
                        "quantity": 1,
                        "rate": "4567.89",
                        "amount": "4567.89",
                    }
                ],
            },
        )
        pay = send(
            client,
            "post",
            "/api/bill-payments",
            {
                "vendor_id": vendor,
                "date": "2026-06-05",
                "amount": doc["total"],
                "method": "check",
                "check_number": str(1001 + n),
                "pay_from_account_id": acc["1000"].id,
                "notes": w("checkmemo"),
                "allocations": [{"bill_id": doc["id"], "amount": doc["total"]}],
            },
        )
        made.append(f"/api/checks/print?bill_payment_id={pay['id']}")
    # blank stock prints the bank's own name and city too
    send(
        client,
        "put",
        f"/api/settings/check-bank-details/{acc['1000'].id}",
        {
            "bank_name": w("bankname"),
            "bank_city_state": w("bankcity"),
            "fractional_routing": "70-1234/719",
            "routing_number": "123456780",
            "account_number": "4455667788",
        },
    )
    urls = []
    for n, settings in enumerate(
        (
            {"check_position": "bottom", "check_stock": "plain"},
            {"check_position": "top"},
            {"check_stock": "preprinted"},
            {"check_stock": "plain", "check_blank_stock": "true"},
        )
    ):
        urls.append(
            (
                lambda settings=settings: send(
                    client, "put", "/api/settings", settings
                ),
                made[n % len(made)],
            )
        )
    return urls


def change_order(client, acc, db, w):
    job = send(
        client,
        "post",
        "/api/jobs",
        {
            "customer_id": _customer(client, w),
            "name": w("jobname"),
            "job_number": w("jobno"),
            "site_address": w("siteaddr"),
            "description": w("jobdesc"),
        },
    )
    lines = [
        {
            "description": w("descone"),
            "cost_type": "material",
            "quantity": 1,
            "unit_cost": "12.34",
            "markup_pct": 10,
        },
        {
            "description": "Crew " + w("desctwo") + "\n" + w("descthree"),
            "cost_type": "labor",
            "quantity": 1,
            "unit_cost": "345678.90",
            "unit": w("unit"),
        },
    ]
    doc = send(
        client,
        "post",
        "/api/change-orders",
        {
            "job_id": job["id"],
            "title": w("title"),
            "description": w("codescone") + "\n" + w("codesctwo"),
            "lines": lines,
        },
    )
    return [f"/api/change-orders/{doc['id']}/pdf"]


def _deposit(client, acc, w):
    c = _customer(client, w, "payer")
    send(
        client,
        "post",
        "/api/payments",
        {
            "customer_id": c,
            "date": "2026-06-10",
            "amount": "12.34",
            "method": "Check",
            "check_number": w("checkno"),
            "reference": w("payref"),
            "notes": w("paynotes"),
            "allocations": [],
        },
    )
    pending = client.get("/api/deposits/pending").json()
    others = [
        {
            "received_from": w(f"from{i}"),
            "account_id": acc["4000"].id,
            "check_number": w(f"ref{i}"),
            "method": w(f"method{i}"),
            "amount": amount,
            "memo": w(f"memo{i}") + "\nsecond " + w(f"memotwo{i}"),
        }
        for i, amount in enumerate(("12.34", "345678.90"))
    ]
    doc = send(
        client,
        "post",
        "/api/deposits",
        {
            "deposit_to_account_id": acc["1000"].id,
            "date": "2026-06-12",
            "reference": w("slipno"),
            "line_ids": [p["transaction_line_id"] for p in pending],
            "other_lines": others,
            "total": str(
                sum(Decimal(str(p["amount"])) for p in pending) + Decimal("345691.24")
            ),
        },
    )
    return f"/api/deposits/{doc['transaction_id']}/pdf"


def deposit_slip(client, acc, db, w):
    return [_deposit(client, acc, w)]


def deposit_slip_preprinted(client, acc, db, w):
    send(
        client,
        "put",
        "/api/settings",
        {"deposit_slip_stock": "preprinted", "deposit_slip_account": w("bankacct")},
    )
    return [_deposit(client, acc, w)]


def customer_statement(client, acc, db, w):
    c = _customer(client, w)
    for amount in ("12.34", "345678.90"):
        doc = send(
            client,
            "post",
            "/api/invoices",
            {
                "customer_id": c,
                "date": "2026-01-01",
                "tax_rate": 0,
                "po_number": w("ponumber"),
                "lines": [{"description": w("descone"), "quantity": 1, "rate": amount}],
            },
        )
    send(
        client,
        "post",
        "/api/payments",
        {
            "customer_id": c,
            "date": "2026-02-02",
            "amount": "5.00",
            "check_number": w("checkno"),
            "reference": w("payref"),
            "notes": w("paynotes"),
            "allocations": [{"invoice_id": doc["id"], "amount": "5.00"}],
        },
    )
    return [f"/api/reports/customer-statement/{c}/pdf?as_of_date=2026-10-01"]


def collection_letter(client, acc, db, w):
    c = _customer(client, w)
    for amount in ("12.34", "345678.90"):
        doc = send(
            client,
            "post",
            "/api/invoices",
            {
                "customer_id": c,
                "date": "2026-01-01",
                "due_date": "2026-01-31",
                "tax_rate": 0,
                "po_number": w("ponumber"),
                "lines": [{"description": w("descone"), "quantity": 1, "rate": amount}],
            },
        )
        assert client.post(f"/api/invoices/{doc['id']}/send").status_code == 200

    def letter():
        r = client.post(
            "/api/reports/collection-letters",
            json={"letter_type": "30", "customer_ids": [c], "send_email": False},
        )
        assert r.status_code == 200 and r.json()["generated"] == 1, r.text

    return [(letter, None)]


def _pay_application(client, w):
    c = _customer(client, w)
    job = send(
        client,
        "post",
        "/api/jobs",
        {
            "customer_id": c,
            "name": w("jobname"),
            "job_number": w("jobno"),
            "site_address": w("siteaddr"),
            "description": w("jobdesc"),
        },
    )
    send(
        client,
        "put",
        f"/api/jobs/{job['id']}/contract",
        {
            "contract_type": "lump_sum",
            "contract_amount": "345691.24",
            "retainage_pct": 10,
        },
    )
    sov = [
        {"description": w("sovone"), "kind": "work", "scheduled_value": "12.34"},
        {
            "description": "Fire " + w("sovtwo") + "\n" + w("sovthree"),
            "kind": "work",
            "scheduled_value": "345678.90",
        },
    ]
    send(client, "put", f"/api/jobs/{job['id']}/sov", {"lines": sov})
    app = send(
        client,
        "post",
        "/api/pay-applications",
        {"job_id": job["id"], "date": "2026-06-01", "notes": w("appnotes")},
    )
    ids = [
        ln["id"] for ln in client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    ]
    lines = [
        {"sov_line_id": ids[0], "this_period": "6.17", "stored": "3.08"},
        {"sov_line_id": ids[1], "this_period": "172839.45", "stored": "86419.72"},
    ]
    app = send(client, "put", f"/api/pay-applications/{app['id']}", {"lines": lines})
    return c, app


def pay_application(client, acc, db, w):
    _c, app = _pay_application(client, w)
    return [f"/api/pay-applications/{app['id']}/pdf"]


def lien_waiver(client, acc, db, w):
    c, app = _pay_application(client, w)
    app = send(client, "post", f"/api/pay-applications/{app['id']}/approve", {})
    pay = send(
        client,
        "post",
        "/api/payments",
        {
            "customer_id": c,
            "date": "2026-06-15",
            "amount": "5000.00",
            "method": "check",
            "allocations": [{"invoice_id": app["invoice_id"], "amount": "5000.00"}],
        },
    )
    urls = [f"/api/invoices/{app['invoice_id']}/pdf"]
    for kind in ("conditional_progress", "unconditional_final"):
        waiver = send(
            client,
            "post",
            "/api/lien-waivers",
            {
                "payment_id": pay["id"],
                "waiver_type": kind,
                "signed_by": w("signedby"),
                "notes": w("notes"),
            },
        )
        urls.append(f"/api/lien-waivers/{waiver['id']}/pdf")
    return urls


def _nonprofit(client):
    send(client, "put", "/api/settings", {"company_type": "nonprofit"})
    client.post("/api/nonprofit/setup-accounts")


def giving_statement(client, acc, db, w):
    from tests.test_donor_documents import _receipt

    _nonprofit(client)
    donor = _customer(client, w, "donor")
    r = _receipt(
        client,
        donor,
        lines=[{"description": w("giftdesc"), "quantity": 1, "rate": "12.34"}],
        fair_value_amount="4.11",
        fair_value_description=w("fairdesc"),
    )
    assert r.status_code in (200, 201), r.text
    receipt = r.json()
    for amount in ("12.34", "345678.90"):
        send(
            client,
            "post",
            "/api/payments",
            {
                "customer_id": donor,
                "date": "2026-03-01",
                "amount": amount,
                "check_number": w("checkno"),
                "reference": w("payref"),
                "notes": w("paynotes"),
            },
        )
    return [
        f"/api/donors/{donor}/giving-statement/pdf?year=2026",
        "/api/donors/giving-statements/pdf?year=2026",
        # the donation receipt and its acknowledgment letter
        f"/api/invoices/{receipt['invoice']['id']}/pdf",
        f"/api/donors/gifts/invoice/{receipt['invoice']['id']}/acknowledgment/pdf",
    ]


def _employee(client, w, **more):
    body = {
        "first_name": w("first"),
        "last_name": w("last"),
        "ssn_last_four": "1234",
        "pay_type": "salary",
        "pay_rate": 56333.42,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "address1": w("empaddrone"),
        "address2": w("empaddrtwo"),
        "city": w("empcity"),
        "state": w("empst"),
        "zip": w("empzip"),
        "work_state": "OR",
        "residence_state": "OR",
        "notes": w("empnotes"),
        **more,
    }
    return send(client, "post", "/api/employees", body)["id"]


def _paid_employee(client, w):
    emp = _employee(client, w)
    run = send(
        client,
        "post",
        "/api/payroll",
        {
            "period_start": "2026-04-01",
            "period_end": "2026-04-10",
            "pay_date": "2026-04-10",
            "stubs": [{"employee_id": emp}],
        },
    )
    send(client, "post", f"/api/payroll/{run['id']}/process", {})
    return emp, run


def pay_stub(client, acc, db, w):
    _emp, run = _paid_employee(client, w)
    return [f"/api/payroll/{run['id']}/paystub/{run['stubs'][0]['id']}"]


def payroll_forms(client, acc, db, w):
    emp, _run = _paid_employee(client, w)
    return [
        f"/api/tax-forms/w2/{emp}/pdf?year=2026",
        "/api/payroll/forms/w3/2026/pdf",
        "/api/tax-forms/940/pdf?year=2026",
        "/api/tax-forms/941/pdf?year=2026&quarter=2",
        f"/api/onboarding/{emp}/new-hire-report/pdf",
    ]


def forms_1099(client, acc, db, w):
    vendor = _vendor(client, w, is_1099_vendor=True, vendor_1099_type="NEC")
    doc = send(
        client,
        "post",
        "/api/bills",
        {
            "vendor_id": vendor,
            "bill_number": "N-1",
            "date": "2026-05-01",
            "lines": [
                {
                    "account_id": acc["6000"].id,
                    "description": w("descone"),
                    "quantity": 1,
                    "rate": 2700,
                }
            ],
        },
    )
    send(
        client,
        "post",
        "/api/bill-payments",
        {
            "vendor_id": vendor,
            "date": "2026-05-01",
            "amount": 2700,
            "allocations": [{"bill_id": doc["id"], "amount": 2700}],
        },
    )
    return [
        f"/api/tax-forms/1099/{vendor}/pdf?year=2026",
        "/api/tax-forms/1096/pdf?year=2026",
    ]


def labels(client, acc, db, w):
    from datetime import date
    from decimal import Decimal

    from app.models.invoices import Invoice, InvoiceStatus

    for n in range(3):
        c = _customer(client, w, f"mail{'abc'[n]}")
        db.add(
            Invoice(
                invoice_number=f"M{n}{w('x', 4)}",
                customer_id=c,
                date=date(2024, 1, 1),
                status=InvoiceStatus.PAID,
                subtotal=Decimal("10"),
                total=Decimal("10"),
                balance_due=Decimal("0"),
            )
        )
    db.commit()
    return ["/api/customers/past/labels.pdf"]


def reports(client, acc, db, w):
    """The report page (report_pdf.html, with _report_theme.html) and the
    analytics page, with long names in their rows."""
    c = _customer(client, w)
    vendor = _vendor(client, w)
    send(
        client,
        "post",
        "/api/invoices",
        {
            "customer_id": c,
            "date": "2026-01-01",
            "due_date": "2026-01-31",
            "tax_rate": 0,
            "po_number": w("ponumber"),
            "lines": [
                {"description": w("descone"), "quantity": 1, "rate": "345678.90"}
            ],
        },
    )
    send(
        client,
        "post",
        "/api/bills",
        {
            "vendor_id": vendor,
            "bill_number": w("billno"),
            "date": "2026-01-01",
            "lines": [
                {
                    "account_id": acc["6000"].id,
                    "description": w("descone"),
                    "quantity": 1,
                    "rate": "4567.89",
                    "amount": "4567.89",
                }
            ],
        },
    )
    span = "start_date=2026-01-01&end_date=2026-12-31"
    return [
        "/api/reports/ar-aging/pdf?as_of_date=2026-10-01",
        "/api/reports/ap-aging/pdf?as_of_date=2026-10-01",
        f"/api/reports/profit-loss/pdf?{span}",
        f"/api/reports/general-ledger/pdf?{span}",
        f"/api/reports/income-by-customer/pdf?{span}",
        f"/api/reports/profit-loss-detail/pdf?{span}",
        f"/api/reports/sales-by-item/pdf?{span}",
        "/api/analytics/export.pdf",
    ]


MAKERS = {
    "invoice": invoice,
    "sales receipt": sales_receipt,
    "estimate": estimate,
    "priced construction estimate": priced_construction_estimate,
    "bid": bid,
    "bid with tables": bid_with_tables,
    "credit memo": credit_memo,
    "purchase order": purchase_order,
    "bill": bill,
    "check": check,
    "change order": change_order,
    "deposit slip": deposit_slip,
    "pre-printed deposit slip": deposit_slip_preprinted,
    "customer statement": customer_statement,
    "collection letter": collection_letter,
    "pay application": pay_application,
    "lien waiver": lien_waiver,
    "giving statement": giving_statement,
    "pay stub": pay_stub,
    "payroll forms": payroll_forms,
    "1099 forms": forms_1099,
    "labels": labels,
    "reports": reports,
}


# --- the layout --------------------------------------------------------------


class _Words(HTMLParser):
    """The words of a page as its HTML has them: text nodes joined as they
    read, a <br> or the edge of a block or a cell between them."""

    BREAKS = {"br", "div", "p", "td", "th", "tr", "li", "h1", "h2", "h3", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("style", "script", "title")
        if tag in self.BREAKS:
            self.text.append(" ")

    def handle_endtag(self, tag):
        self.skip -= tag in ("style", "script", "title")
        if tag in self.BREAKS:
            self.text.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def _whole_words(html):
    """Every word of the page, lower case, and every piece a word may be
    broken into at a hyphen, a slash or an at sign, where a line may end."""
    parser = _Words()
    parser.feed(html)
    words, long = set(), []
    for word in "".join(parser.text).lower().split():
        words.add(word)
        if len(word) > 40 or "x" * 8 in word:
            long.append(word)
        pieces = re.split(r"(?<=[-/@,.:;)])|(?=[(@])", word)
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces) + 1):
                words.add("".join(pieces[i:j]))
    return words, long


def _line_text(node):
    """A line's own text: not that of a box set in the line (a label on a
    sheet of labels), whose lines are its own."""
    from weasyprint.formatting_structure import boxes as wb

    return "".join(
        child.text if isinstance(child, wb.TextBox) else _line_text(child)
        for child in node.children
        if isinstance(child, (wb.TextBox, wb.InlineBox))
    )


def survey(html, fixed=None):
    """What a page's layout says, as the PDF prints it: the text past the
    left or right edge of the page's content, every figure in a column with
    the x it ends at and the x it starts at, in order, and the pieces of
    words printed where the
    page's HTML has the word whole (of every word, or with ``fixed`` given
    of the words in it, the page's own: labels, headings, dates, figures)."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    past, figures, broken = [], [], []
    whole, long = _whole_words(html)
    typed = set() if fixed is None else whole - fixed
    for number, page in enumerate(
        HTML(string=html, url_fetcher=_get_fetcher()).render().pages, 1
    ):
        box = page._page_box
        left = box.content_box_x()
        right = left + box.width

        def walk(node, margin, cut_lo, cut_hi):
            margin = margin or isinstance(node, wb.MarginBox)
            style = getattr(node, "style", None)
            if (
                style is not None
                and not isinstance(node, (wb.TextBox, wb.InlineBox, wb.LineBox))
                and style["overflow"] != "visible"
            ):
                # what a box cuts off at its edge is not printed
                at = node.position_x + node.margin_left
                cut_lo = max(cut_lo, at)
                cut_hi = min(cut_hi, at + node.border_width())
            if isinstance(node, wb.LineBox) and not margin:
                line = _line_text(node)
                # a figure in a column: a line that is a figure and no more
                if MONEY.match(line.strip()):
                    boxes = [
                        t
                        for t in node.descendants()
                        if isinstance(t, wb.TextBox) and t.text.strip()
                    ]
                    end = max(t.position_x + t.width for t in boxes)
                    start = min(t.position_x for t in boxes)
                    figures.append((line.strip(), round(end, 1), round(start, 1)))
                broken.extend(
                    f"page {number}: {word!r} is a piece of a word"
                    for word in line.lower().split()
                    if word not in whole
                    and not any(word in w for w in long)
                    and not any(word in w for w in typed)
                )
            if isinstance(node, wb.TextBox):
                text = node.text.strip()
                if not text:
                    return
                start = max(node.position_x, cut_lo)
                end = min(node.position_x + node.width, cut_hi)
                # a page's own margin text (the running head) keeps to the paper
                lo, hi = (0, box.margin_width()) if margin else (left, right)
                if start < end and (end > hi + 0.5 or start < lo - 0.5):
                    past.append(
                        f"page {number}: {text[:28]!r} runs from {start:.1f} to"
                        f" {end:.1f} (the page is {lo:.1f} to {hi:.1f})"
                    )
                return
            for child in getattr(node, "children", ()) or ():
                walk(child, margin, cut_lo, cut_hi)

        walk(box, False, float("-inf"), float("inf"))
    return past, figures, broken


@pytest.fixture
def printed(monkeypatch):
    """Every page the app draws, with the templates it was drawn from."""
    from app.services import pdf_service

    seen = {"html": [], "templates": set()}
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: seen["html"].append(html) or b"%PDF-"
    )
    env = pdf_service._jinja_env
    real = env.get_template

    def get_template(name, *args, **kwargs):
        seen["templates"].add(str(name))
        return real(name, *args, **kwargs)

    monkeypatch.setattr(env, "get_template", get_template)
    return seen


def _draw(client, printed, maker, acc, db, w):
    """The pages a maker's records print as: ``[(what, html)]``."""
    pages = []
    for n, url in enumerate(maker(client, acc, db, w)):
        before = url[0] if isinstance(url, tuple) else None
        url = url[1] if isinstance(url, tuple) else url
        printed["html"].clear()
        if before:
            before()
        if url:
            r = client.get(url)
            assert r.status_code == 200, (url, r.status_code, r.text[:400])
        assert printed["html"], url
        what = re.sub(r"\d+", "N", (url or "letter").split("?")[0])
        pages += [(f"{what} ({n})", html) for html in printed["html"]]
    return pages


def _compare(kind, what, figures, plain_figures):
    """What is wrong with where a page's figures end, against the same
    page with ordinary text. A figure is ``(text, where it ends, where it
    starts)``."""
    if not figures or not plain_figures:
        return []
    if kind == "reports":
        # A report's columns share the page's width by what is in them, and
        # it lists every record so far: its columns are not where they were
        # with other names. Its figures are whole and inside the page.
        return []
    if [f[0] for f in figures] == [f[0] for f in plain_figures]:
        return [
            f"{kind}, {what}: {now[0]} ends at {now[1]}, {was[1]} with ordinary text"
            for now, was in zip(figures, plain_figures)
            if abs(now[1] - was[1]) > 0.5
        ]
    # A page that lists every record (all donors, the year's forms) has the
    # ordinary ones on it too, and other totals: each figure is in a column
    # of the page with ordinary text, ending where a figure set to the right
    # ends there or starting where one set to the left starts.
    ends = {f[1] for f in plain_figures}
    starts = {f[2] for f in plain_figures}
    return [
        f"{kind}, {what}: {f[0]} runs from {f[2]} to {f[1]}, in no column of"
        f" the page with ordinary text (ends {sorted(ends)}, starts {sorted(starts)})"
        for f in figures
        if not any(abs(f[1] - x) <= 0.5 for x in ends)
        and not any(abs(f[2] - x) <= 0.5 for x in starts)
    ]


def _problems(client, printed, kind, accounts, db):
    """What is wrong with one kind of document, drawn three times."""
    drawn = {}
    for name, w in (("ordinary", _ordinary), ("many words", _wordy), ("long", _long)):
        _settings(client, w)
        drawn[name] = _draw(client, printed, MAKERS[kind], accounts, db, w)
    ordinary = drawn.pop("ordinary")
    wrong, plain = [], []
    for what, html in ordinary:
        past, figures, broken = survey(html)
        wrong += [f"{kind}, {what}, ordinary text: {p}" for p in past + broken]
        plain.append((figures, _whole_words(html)[0]))
    for name, pages in drawn.items():
        assert len(pages) == len(ordinary) and pages, (kind, name)
        for (what, html), (plain_figures, fixed) in zip(pages, plain):
            if name == "long":
                assert "X" * 10 in html, (kind, what)
            # what the page prints whatever is typed (its labels, headings,
            # dates and figures) is never broken; what a person typed breaks
            # only in a column narrower than the word
            past, figures, broken = survey(html, fixed)
            wrong += [f"{kind}, {what}, {name}: {p}" for p in past + broken]
            wrong += _compare(kind, f"{what}, {name}", figures, plain_figures)
    return wrong


def test_a_long_word_never_widens_a_column_or_passes_the_margin(
    client, seed_accounts, db_session, printed
):
    """One test for every document, as the browser sweeps are one: where
    WeasyPrint is not installed it is one skip, not one for each."""
    pytest.importorskip("weasyprint")
    wrong = []
    for kind in MAKERS:
        wrong += _problems(client, printed, kind, seed_accounts, db_session)
    assert not wrong, "\n".join(wrong[:60]) + f"\n({len(wrong)} in all)"
    # a template added to app/templates is drawn above with a long word in
    # its text, or named in NOT_PRINTED
    every = {p.name for p in TEMPLATES.glob("*.html")} - NOT_PRINTED
    assert every - printed["templates"] == set(), sorted(every - printed["templates"])


def test_every_printed_template_carries_the_rule():
    """The rule is written once (_print_text.html) and every printed
    template takes it in, in its own style block."""
    missing = [
        p.name
        for p in sorted(TEMPLATES.glob("*.html"))
        if p.name not in NOT_PRINTED
        and not any(
            taken in p.read_text(encoding="utf-8")
            # a report page takes it in with the report theme
            for taken in ("{% include '_print_text.html' %}", '"_report_theme.html"')
        )
    ]
    assert missing == [], missing


# --- the long words ----------------------------------------------------------


def test_a_long_word_is_set_to_break_and_nothing_else_is_touched():
    from app.services.pdf_service import LONG_WORD, break_long_words

    span = '<span class="long-word">'
    word = "w" * LONG_WORD
    # a word of 20 characters is long; one of 19 is not
    assert break_long_words(f"<td>to {word} now</td>") == (
        f"<td>to {span}{word}</span> now</td>"
    )
    short = f"<td>to {word[1:]} now</td>"
    assert break_long_words(short) == short
    # a character reference is one character, and stays whole
    escaped = "a&amp;b" + "c" * (LONG_WORD - 3)
    assert break_long_words(f"<p>{escaped}</p>") == f"<p>{span}{escaped}</span></p>"
    assert break_long_words(f"<p>{escaped[:-1]}</p>") == f"<p>{escaped[:-1]}</p>"
    # only text: not a tag, an attribute, a style sheet, a title or a drawing
    for untouched in (
        f'<img src="data:image/png;base64,{word}" alt="{word}">',
        f"<style>.{word} {{ color: red; }}</style>",
        f"<title>{word}</title>",
        f"<svg><text>{word}</text></svg>",
        f"<!-- {word} -->",
    ):
        assert break_long_words(untouched) == untouched
    # done once: a page drawn from parts already done is not done again
    once = break_long_words(f"<td>{word} and {word}</td>")
    assert once.count(span) == 2 and break_long_words(once) == once


def test_a_word_longer_than_a_line_is_set_in_pieces_with_a_place_to_break():
    """WeasyPrint measures a word that may break anywhere one character at
    a time against all of it: in pieces, each is measured alone. A word that
    may fit a line stays one piece, and moves to the next line whole."""
    from app.services.pdf_service import LONG_WORD_WHOLE, break_long_words

    span = '<span class="long-word">'
    gap = '<span class="long-word-gap"></span>'
    whole = "x" * LONG_WORD_WHOLE
    assert break_long_words(f"<td>{whole}</td>") == f"<td>{span}{whole}</span></td>"
    long = "x" * (LONG_WORD_WHOLE + 55)
    made = break_long_words(f"<td>{long}</td>")
    assert made.count(span) == 16 and made.count(gap) == 15
    assert (
        made.replace(gap, "").replace(span, "").replace("</span>", "")
        == f"<td>{long}</td>"
    )
    # a character reference is never split between two pieces
    refs = break_long_words("<td>" + "&amp;" * 105 + "</td>")
    assert refs.count("&amp;") == 105 and refs.count(gap) == 10
    # after a label with no space, the word starts at a place to break too
    labelled = break_long_words("<div><strong>Ref:</strong>" + "x" * 30 + "</div>")
    assert f"</strong>{gap}<span" in labelled


def test_only_a_printed_page_has_its_long_words_set():
    """An email's body and the web pages are drawn by the same engine and
    are not touched: they do not take the rule in."""
    from app.services.pdf_service import _jinja_env

    word = "w" * 30
    page = _jinja_env.from_string(
        "<html><head><style>{% include '_print_text.html' %}</style></head>"
        "<body><p>{{ text }}</p></body></html>"
    ).render(text=f"<{word}>")
    assert f'<p><span class="long-word">&lt;{word}&gt;</span></p>' in page
    plain = _jinja_env.from_string("<p>{{ text }}</p>").render(text=word)
    assert plain == f"<p>{word}</p>"
