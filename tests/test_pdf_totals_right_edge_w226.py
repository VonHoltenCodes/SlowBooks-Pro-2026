"""Round 26 (W-226): a totals table keeps its right edge, whatever the figures.

At six figures the giving statement's totals ended 8.5 pt further right
than at $12.34 (553.0 against 544.5): its summary sits in a cell spanning
the gift table, and the gift table's seven columns, figures and headings
that never wrap, grew past the page's width. The pay application's line
detail did the same by about 3 pt. Every document #1158 and #1172 widened a
totals block on is drawn here at $12.34, at six figures and at seven, and
its totals table must end at the same x each time, inside the page.
"""

import pytest

AMOUNTS = ("12.34", "4567.89", "45678.90", "345678.90", "3456789.01", "4999999.99")


def _customer(client, name):
    r = client.post("/api/customers?force=true", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _vendor(client):
    found = [v for v in client.get("/api/vendors").json() if v["name"] == "Edge Supply"]
    return (
        found or [client.post("/api/vendors", json={"name": "Edge Supply"}).json()]
    )[0]["id"]


def _lines(a, **extra):
    return [
        {"description": "Framing", "quantity": 1, "rate": a, **extra},
        {"description": "Trim", "quantity": 1, "rate": a, **extra},
    ]


def _made(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def invoice(client, accounts, a, n):
    c = _customer(client, f"Edge {n}")
    doc = _made(
        client.post(
            "/api/invoices",
            json={
                "customer_id": c,
                "date": "2026-06-01",
                "tax_rate": 0,
                "lines": _lines(a),
            },
        )
    )
    return f"/api/invoices/{doc['id']}/pdf"


def estimate(client, accounts, a, n):
    c = _customer(client, f"Edge {n}")
    doc = _made(
        client.post(
            "/api/estimates",
            json={"customer_id": c, "date": "2026-06-01", "lines": _lines(a)},
        )
    )
    return f"/api/estimates/{doc['id']}/pdf"


def credit_memo(client, accounts, a, n):
    c = _customer(client, f"Edge {n}")
    doc = _made(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": c,
                "date": "2026-06-01",
                "tax_rate": 0,
                "lines": _lines(a),
            },
        )
    )
    return f"/api/credit-memos/{doc['id']}/pdf"


def purchase_order(client, accounts, a, n):
    doc = _made(
        client.post(
            "/api/purchase-orders",
            json={
                "vendor_id": _vendor(client),
                "date": "2026-06-01",
                "lines": _lines(a),
            },
        )
    )
    return f"/api/purchase-orders/{doc['id']}/pdf"


def bill(client, accounts, a, n):
    lines = [ln | {"amount": a} for ln in _lines(a, account_id=accounts["6000"].id)]
    doc = _made(
        client.post(
            "/api/bills",
            json={
                "vendor_id": _vendor(client),
                "bill_number": f"E-{n}",
                "date": "2026-06-01",
                "lines": lines,
            },
        )
    )
    return f"/api/bills/{doc['id']}/pdf"


def change_order(client, accounts, a, n):
    c = _customer(client, f"Owner {n}")
    job = _made(client.post("/api/jobs", json={"customer_id": c, "name": f"Tower {n}"}))
    lines = [
        {
            "description": "Framing",
            "cost_type": "material",
            "quantity": 1,
            "unit_cost": a,
        },
        {"description": "Crew", "cost_type": "labor", "quantity": 1, "unit_cost": a},
    ]
    doc = _made(
        client.post("/api/change-orders", json={"job_id": job["id"], "lines": lines})
    )
    return f"/api/change-orders/{doc['id']}/pdf"


def deposit(client, accounts, a, n):
    others = [
        {
            "received_from": f"Payer {i}",
            "account_id": accounts["4000"].id,
            "check_number": str(i),
            "method": "Check",
            "amount": a,
        }
        for i in (1, 2)
    ]
    total = str(round(float(a) * 2, 2))
    doc = _made(
        client.post(
            "/api/deposits",
            json={
                "deposit_to_account_id": accounts["1000"].id,
                "date": "2026-06-12",
                "reference": f"E-{n}",
                "line_ids": [],
                "other_lines": others,
                "total": total,
            },
        )
    )
    return f"/api/deposits/{doc['transaction_id']}/pdf"


def statement(client, accounts, a, n):
    c = _customer(client, f"Edge {n}")
    for _ in (1, 2):
        _made(
            client.post(
                "/api/invoices",
                json={
                    "customer_id": c,
                    "date": "2026-01-01",
                    "tax_rate": 0,
                    "lines": [{"description": "Sign", "quantity": 1, "rate": a}],
                },
            )
        )
    return f"/api/reports/customer-statement/{c}/pdf?as_of_date=2026-10-01"


def pay_application(client, accounts, a, n):
    c = _customer(client, f"Builder {n}")
    job = _made(client.post("/api/jobs", json={"customer_id": c, "name": f"Job {n}"}))[
        "id"
    ]
    total = round(float(a) * 2, 2)
    _made(
        client.put(
            f"/api/jobs/{job}/contract",
            json={
                "contract_type": "lump_sum",
                "contract_amount": total,
                "retainage_pct": 10,
            },
        )
    )
    sov = [
        {"description": f"Line {i}", "kind": "work", "scheduled_value": a}
        for i in (1, 2)
    ]
    _made(client.put(f"/api/jobs/{job}/sov", json={"lines": sov}))
    app = _made(
        client.post("/api/pay-applications", json={"job_id": job, "date": "2026-06-01"})
    )
    # every column of the line detail carries a figure of that size
    ids = [ln["id"] for ln in client.get(f"/api/jobs/{job}/contract").json()["sov"]]
    half, quarter = round(float(a) / 2, 2), round(float(a) / 4, 2)
    lines = [{"sov_line_id": i, "this_period": half, "stored": quarter} for i in ids]
    _made(client.put(f"/api/pay-applications/{app['id']}", json={"lines": lines}))
    return f"/api/pay-applications/{app['id']}/pdf"


def giving_statement(client, accounts, a, n):
    from tests.test_donor_documents import _receipt

    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    client.post("/api/nonprofit/setup-accounts")
    donor = _customer(client, f"Donor {n}")
    _made(
        _receipt(
            client,
            donor,
            lines=[{"description": "Gala", "quantity": 1, "rate": a}],
            fair_value_amount=str(round(float(a) / 3, 2)),
            fair_value_description="dinner",
        )
    )
    _made(
        client.post(
            "/api/payments",
            json={"customer_id": donor, "date": "2026-03-01", "amount": a},
        )
    )
    return f"/api/donors/{donor}/giving-statement/pdf?year=2026"


MAKERS = {
    "invoice": invoice,
    "estimate": estimate,
    "credit memo": credit_memo,
    "purchase order": purchase_order,
    "bill": bill,
    "change order": change_order,
    "deposit slip": deposit,
    "customer statement": statement,
    "pay application": pay_application,
    "giving statement": giving_statement,
}
# the block the totals sit in; the pay application's line detail ends in its
# totals row too
BLOCKS = ("totals", "summary", "cert", "cont")


def _edges(html):
    """{block class: the right edge of its table}, and the page content's
    right edge, laid out as the PDF is."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    edges, right = {}, None
    for page in HTML(string=html, url_fetcher=_get_fetcher()).render().pages:
        box = page._page_box
        right = box.content_box_x() + box.width
        for d in box.descendants():
            el = getattr(d, "element", None)
            if el is None or not isinstance(d, (wb.BlockBox, wb.TableBox)):
                continue
            classes = (el.get("class") or "").split()
            for cls in BLOCKS:
                if cls not in classes or el.tag == "tr":
                    continue
                tables = (
                    [d]
                    if isinstance(d, wb.TableBox)
                    else [t for t in d.descendants() if isinstance(t, wb.TableBox)]
                )
                for t in tables[:1]:
                    edge = round(t.position_x + t.border_width(), 1)
                    edges[cls] = max(edges.get(cls, 0), edge)
    return edges, right


@pytest.fixture
def made_html(monkeypatch):
    from app.services import pdf_service

    seen = []
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: seen.append(html) or b"%PDF-"
    )
    return seen


@pytest.mark.parametrize("kind", list(MAKERS))
def test_a_totals_table_ends_at_one_place_whatever_the_figures(
    client, seed_accounts, made_html, kind
):
    pytest.importorskip("weasyprint")
    seen = {}
    for n, a in enumerate(AMOUNTS):
        made_html.clear()
        url = MAKERS[kind](client, seed_accounts, a, n)
        r = client.get(url)
        assert r.status_code == 200, r.text[:300]
        assert made_html, url
        seen[a] = _edges(made_html[-1])
    base, right = seen[AMOUNTS[0]]
    assert base, kind
    for a, (edges, _right) in seen.items():
        assert edges == base, (kind, a, edges, base)
        assert all(e <= right + 0.5 for e in edges.values()), (kind, a, edges, right)
