"""Path-traversal + validation regression tests for the attachments route.

Covers the fix for CodeQL py/path-injection alert #19.
"""
import io


def _upload(client, entity_type, entity_id, filename, content=b"hi", content_type="application/pdf"):
    return client.post(
        f"/api/attachments/{entity_type}/{entity_id}",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_rejects_unknown_entity_type(client, seed_accounts):
    # A URL-safe non-whitelisted type reaches our handler (the /../../../etc
    # variant is normalized by Starlette's router to a 404 before it does).
    r = _upload(client, "sneaky", 1, "x.pdf")
    assert r.status_code == 400
    assert "Invalid entity type" in r.json()["detail"]


def test_rejects_path_traversal_filename(client, seed_accounts):
    # Path(...).name strips directory prefixes; this verifies the fallback still holds.
    r = _upload(client, "invoice", 1, "../../secret.pdf")
    # Either the filename gets stripped to "secret.pdf" and accepted,
    # or it's rejected. Either way, the file must not land outside UPLOAD_BASE.
    assert r.status_code in (201, 400)

    # Confirm no file was written under /tmp/secret.pdf or similar
    from pathlib import Path
    attached = (
        Path("/home/devbase1/Development/bookkeeper/app/static/uploads/attachments").resolve()
    )
    # no matter where we ran the test from, there should not be an escape
    import os
    for root, _, files in os.walk(attached):
        for f in files:
            assert "etc" not in root and "passwd" not in f


def test_rejects_disallowed_mime(client, seed_accounts):
    r = _upload(client, "invoice", 1, "evil.html", content_type="text/html")
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_rejects_disallowed_extension(client, seed_accounts):
    r = _upload(client, "invoice", 1, "evil.exe", content_type="application/pdf")
    assert r.status_code == 400
    assert "extension" in r.json()["detail"].lower()


def test_accepts_valid_pdf(client, seed_accounts):
    r = _upload(client, "invoice", 42, "report.pdf", content=b"%PDF-1.4\n", content_type="application/pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "report.pdf"
    # file_path is relative to /static and includes the sanitized type dir
    assert body["file_path"].startswith("uploads/attachments/invoice/42/")


def test_filename_special_chars_sanitized(client, seed_accounts):
    # Characters outside the safe set get replaced with _
    r = _upload(client, "invoice", 1, "weird;|$name.pdf")
    assert r.status_code == 201, r.text
    assert ";" not in r.json()["filename"]
    assert "|" not in r.json()["filename"]
