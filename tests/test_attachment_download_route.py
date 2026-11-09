"""The download route must actually be reachable.

Found by macbase1 and reproduced by skytech during the 2.10.3 gate. FastAPI
matches routes in declaration order, and

    @router.get("/{entity_type}/{entity_id}")   # entity_id: int
    @router.get("/download/{attachment_id}")

means GET /api/attachments/download/2 binds the FIRST one — entity_type
"download", entity_id 2 — and answers `[]`. The download route was never
reached. You could attach a file and never get it back.

That shipped for as long as both routes have existed, and no test caught it
because every test called the handler's logic rather than its URL. These
tests go through the URL a user's browser goes through.
"""

import io


def _upload(client, customer_id):
    r = client.post(
        f"/api/attachments/customer/{customer_id}",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_download_route_returns_the_file_not_a_list(
    client, seed_accounts, seed_customer
):
    """The regression. Before the reorder this answered 200 with `[]`."""
    att = _upload(client, seed_customer.id)
    r = client.get(f"/api/attachments/download/{att['id']}")
    assert r.status_code == 200, r.text
    ctype = r.headers.get("content-type", "")
    assert "application/json" not in ctype, (
        f"the download route is shadowed again — it answered JSON ({ctype}), "
        "which means /{entity_type}/{entity_id} matched first"
    )
    assert r.content.startswith(b"%PDF-"), "the response was not the stored file"


def test_listing_by_entity_still_works(client, seed_accounts, seed_customer):
    """The reorder must not cost the route it was moved above."""
    att = _upload(client, seed_customer.id)
    r = client.get(f"/api/attachments/customer/{seed_customer.id}")
    assert r.status_code == 200, r.text
    assert [a["id"] for a in r.json()] == [att["id"]]


def test_an_entity_type_called_download_is_no_longer_ambiguous(client, seed_accounts):
    """`/download/<int>` belongs to the download route. Listing attachments
    for a literal entity type named "download" is not a real case, but the
    ordering must be deliberate rather than accidental."""
    r = client.get("/api/attachments/download/999999")
    # not a list: either the file is missing (404) or it is a file response
    assert r.status_code in (404, 200)
    if r.status_code == 200:
        assert "application/json" not in r.headers.get("content-type", "")


def test_download_of_a_missing_attachment_is_404_not_an_empty_list(
    client, seed_accounts
):
    r = client.get("/api/attachments/download/424242")
    assert r.status_code == 404, r.text


def test_no_route_anywhere_is_shadowed_by_an_earlier_catch_all():
    """The tripwire for the whole class.

    FastAPI matches in declaration order, so a literal route declared after a
    catch-all in the same router is dead — silently, answering the catch-all's
    payload. That is exactly how the attachment download route shipped
    unreachable. Rather than fix one instance and hope, walk every mounted
    route and assert that no concrete URL of a later route is fully matched by
    an earlier one.
    """
    import re

    from starlette.routing import Match

    from app.main import app

    seen = []
    shadowed = []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            scope = {
                "type": "http",
                "method": method,
                "path": concrete,
                "headers": [],
                "query_string": b"",
                "root_path": "",
            }
            for prev_path, prev_methods, prev in seen:
                if method not in prev_methods:
                    continue
                if prev.matches(scope)[0] == Match.FULL and prev_path != path:
                    shadowed.append(f"{method} {path} is shadowed by {prev_path}")
                    break
        seen.append((path, methods, route))

    assert shadowed == [], "unreachable route(s):\n  " + "\n  ".join(shadowed)
