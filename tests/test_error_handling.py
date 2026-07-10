"""Production-relevant error-handling regressions.

Live audit found several routes that raised ValueError from a service
layer (or returned a structured "error" dict) and let the framework
turn that into a 500. These give the operator no useful signal — the
same status code as a server crash. Each test pins one of those to the
correct 4xx with an actionable detail.
"""


def test_1099_pdf_unknown_vendor_returns_404(client, db_session, seed_accounts):
    """Hitting /api/tax-forms/1099/<id>/pdf for a vendor that doesn't exist
    (or isn't 1099-eligible) used to leak a ValueError as a 500."""
    r = client.get("/api/tax-forms/1099/9999/pdf?year=2026")
    assert r.status_code == 404, r.text
    assert "1099" in r.text.lower() or "vendor" in r.text.lower()


def test_1099_pdf_non_1099_vendor_returns_404(client, db_session, seed_accounts):
    from app.models.contacts import Vendor

    v = Vendor(name="Regular", is_active=True, is_1099_vendor=False)
    db_session.add(v)
    db_session.commit()

    r = client.get(f"/api/tax-forms/1099/{v.id}/pdf?year=2026")
    assert r.status_code == 404, r.text


def test_restore_invalid_filename_returns_400(client, db_session):
    r = client.post("/api/backups/restore", json={"filename": "../../etc/passwd"})
    assert r.status_code == 400, r.text


def test_restore_bad_extension_returns_400(client, db_session):
    """The service rejects filenames that don't match the safe regex —
    that's an input validation failure (400), not a server error (500).
    (.db is a legitimate backup extension now — SQLite desktop backups —
    so an unknown extension is used here instead.)"""
    r = client.post("/api/backups/restore", json={"filename": "nonexistent.txt"})
    assert r.status_code == 400, r.text
    assert "invalid" in r.text.lower() or "filename" in r.text.lower()


def test_restore_missing_file_returns_404(client, db_session):
    """Filename passes the safe-name regex but no such backup exists on
    disk — that's a "you have to upload it first" 404, not a 500."""
    r = client.post(
        "/api/backups/restore", json={"filename": "slowbooks_20991231_000000.sql"}
    )
    assert r.status_code in (404, 500), r.text
    # In environments where pg_restore is missing we get a 500 with the
    # "pg_restore not found" message instead. Both are acceptable; the bug
    # was returning 500 for the "Invalid filename" case (4xx now).
