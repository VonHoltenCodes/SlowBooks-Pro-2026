"""A class id that is no class of this company is refused on every write,
in a sentence (W-199).

Class 99999 on an invoice line, an invoice header or a bill line was taken
(201) and posted under an "Unknown" class on P&L by Class. Every request
body whose schema declares a class (`class_id`, `source_class_id`, …) now
has each class id in it checked once, before the route runs, by the app's
own dependency (dimensions.class_ids_checked): one that is not a class of
this company is refused (404) and nothing is written. That a class is
archived is each path's own matter, as before: a document still takes and
keeps an archived class."""

from fastapi.routing import APIRoute

from app.models.invoices import Invoice
from app.services.dimensions import (
    _route_declares_a_class,
    class_ids_checked,
    unknown_class_sentence,
)
from tests.test_feed_receive_pay_1056 import books  # noqa: F401  (the fixture)

GONE = 99999


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _refused(r):
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == unknown_class_sentence(GONE)
    assert r.json()["detail"] == (
        "Class 99999 is not a class of this company; pick one from the list, "
        "or leave it blank."
    )


def _bodies(seed_accounts, customer_id, vendor_id, cls):
    """(method, path, body) for each write, with `cls` as its class."""
    line = {"description": "Cake", "quantity": 1, "rate": 10}
    return [
        (
            "post",
            "/api/invoices",
            {
                "customer_id": customer_id,
                "date": "2026-08-01",
                "class_id": cls,
                "lines": [line],
            },
        ),
        (
            "post",
            "/api/invoices",
            {
                "customer_id": customer_id,
                "date": "2026-08-01",
                "lines": [{**line, "class_id": cls}],
            },
        ),
        (
            "post",
            "/api/bills",
            {
                "vendor_id": vendor_id,
                "bill_number": "U-1",
                "date": "2026-08-01",
                "lines": [{**line, "class_id": cls}],
            },
        ),
        (
            "post",
            "/api/credit-memos",
            {
                "customer_id": customer_id,
                "date": "2026-08-01",
                "lines": [{**line, "class_id": cls}],
            },
        ),
        (
            "post",
            "/api/journal",
            {
                "date": "2026-08-01",
                "description": "Moved",
                "lines": [
                    {
                        "account_id": seed_accounts["6000"].id,
                        "debit": 5,
                        "credit": 0,
                        "class_id": cls,
                    },
                    {"account_id": seed_accounts["1000"].id, "debit": 0, "credit": 5},
                ],
            },
        ),
        (
            "post",
            "/api/bank-rules",
            {"name": "Phone", "pattern": "PHONE", "class_id": cls},
        ),
    ]


def test_an_unknown_class_is_refused_on_documents_lines_journal_and_rules(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _ok(
        client.post(
            "/api/vendors",
            json={
                "name": "Flour Mill",
                "default_expense_account_id": seed_accounts["6000"].id,
            },
        )
    )
    before = db_session.query(Invoice).count()
    for method, path, body in _bodies(
        seed_accounts, seed_customer.id, vendor["id"], GONE
    ):
        _refused(getattr(client, method)(path, json=body))
    db_session.expire_all()
    assert db_session.query(Invoice).count() == before  # nothing was written


def test_an_archived_class_is_still_taken_where_it_was(
    client, seed_accounts, seed_customer
):
    old = _ok(client.post("/api/classes", json={"name": "Old Shop"}))
    _ok(client.put(f"/api/classes/{old['id']}", json={"is_archived": True}))
    vendor = _ok(
        client.post(
            "/api/vendors",
            json={
                "name": "Flour Mill",
                "default_expense_account_id": seed_accounts["6000"].id,
            },
        )
    )
    for method, path, body in _bodies(
        seed_accounts, seed_customer.id, vendor["id"], old["id"]
    )[:5]:
        _ok(getattr(client, method)(path, json=body))


def test_the_feed_refuses_an_unknown_class_on_add_add_selected_and_a_pick(
    client, books  # noqa: F811
):
    line = books["lines"]["ACH FLOUR MILL"]
    _refused(
        client.patch(f"/api/banking/transactions/{line['id']}", json={"class_id": GONE})
    )
    _refused(
        client.post(
            f"/api/banking/transactions/{line['id']}/add",
            json={"category_account_id": None, "class_id": GONE},
        )
    )
    _refused(
        client.post(
            f"/api/banking/accounts/{books['feed']['id']}/feed/add-all",
            json={
                "line_ids": [line["id"]],
                "lines": [{"id": line["id"], "class_id": GONE}],
            },
        )
    )
    after = _ok(
        client.get(f"/api/banking/transactions?bank_account_id={books['feed']['id']}")
    )
    assert (
        next(t for t in after if t["id"] == line["id"])["match_status"] == "unmatched"
    )


def test_every_write_whose_schema_declares_a_class_is_checked():
    from app.main import app

    assert any(d.dependency is class_ids_checked for d in app.router.dependencies)

    def routes(container):
        for r in getattr(container, "routes", []):
            if isinstance(r, APIRoute):
                yield r
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from routes(inner)

    checked = {
        (m, r.path)
        for r in routes(app.router)
        for m in r.methods
        if _route_declares_a_class(r)
    }
    for want in [
        ("POST", "/api/invoices"),
        ("PUT", "/api/invoices/{invoice_id}"),
        ("POST", "/api/bills"),
        ("POST", "/api/journal"),
        ("POST", "/api/bank-rules"),
        ("PATCH", "/api/banking/transactions/{txn_id}"),
        ("POST", "/api/banking/accounts/{account_id}/feed/add-all"),
    ]:
        assert want in checked, want
    # a body that names no class is not read (a saved report keeps the
    # parameters of a class since deleted)
    assert ("POST", "/api/saved-reports") not in checked
