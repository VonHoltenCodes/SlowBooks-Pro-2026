"""Regression tests for /api/uploads/logo.

Covers the bug from issue #10: the UI promised SVG support but the server
rejected it. After the fix the server accepts the same five formats the UI
advertises (PNG, JPEG, GIF, WebP, SVG) and rejects everything else with a
clear, JSON-shaped error.

Since 2.18.0 the logo is kept in the company's own database, not as
uploads/company_logo.<ext> in a folder every company shared (2.18.0 gate,
macbase1 NEW-14): its address is /api/uploads/logo/<id>.
"""

import base64
import io
import os

from app.services import storage

# whole images: a raster logo is decoded before it is kept (NEW-201), so
# the bare PNG signature no longer passes
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
PNG = base64.b64decode(PNG_B64)
WEBP = base64.b64decode("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAUAmJaQAA3AA/v02aAA=")


def test_the_pdf_logo_is_the_logo_kept_in_the_company_database(client):
    from app.services import pdf_service

    path = _post(client, PNG, "image/png").json()["path"]

    result = pdf_service._company_logo_data_uri({"company_logo_path": path})

    assert result == f"data:image/png;base64,{PNG_B64}"


def _nothing_written_to_disk():
    uploads = storage.uploads_root()
    return not uploads.exists() or not any(
        p.name.startswith("company_logo") for p in uploads.iterdir()
    )


def _post(client, content, content_type, filename="logo.png"):
    return client.post(
        "/api/uploads/logo",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_png_upload_accepted(client, seed_accounts):
    r = _post(client, PNG, "image/png", "logo.png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].startswith("/api/uploads/logo/")
    served = client.get(body["path"])
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG
    assert _nothing_written_to_disk()


def test_svg_upload_now_accepted(client, seed_accounts):
    # Issue #10: SVG was promised but rejected. After the fix it's accepted.
    svg = b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'/>"
    r = _post(client, svg, "image/svg+xml", "logo.svg")
    assert r.status_code == 200, r.text
    served = client.get(r.json()["path"])
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert served.content == svg


def test_webp_upload_accepted(client, seed_accounts):
    r = _post(client, WEBP, "image/webp", "logo.webp")
    assert r.status_code == 200, r.text
    assert client.get(r.json()["path"]).headers["content-type"] == "image/webp"


def test_extension_derives_from_content_type_not_filename(
    client, seed_accounts, db_session
):
    """An attacker-renamed file (e.g. exe pretending to be .png) is never
    kept or served under a misleading name or type. We trust the
    content-type the framework verified, not the user-supplied filename."""
    from app.models.stored_files import StoredFile

    r = _post(client, PNG, "image/png", "logo.exe")
    assert r.status_code == 200, r.text
    file_id = int(r.json()["path"].rsplit("/", 1)[1])
    stored = db_session.get(StoredFile, file_id)
    # Kept as a .png, not .exe
    assert stored.original_name == "company_logo.png"
    assert stored.content_type == "image/png"
    assert client.get(r.json()["path"]).headers["content-type"] == "image/png"
    assert _nothing_written_to_disk()


def test_disallowed_mime_rejected_with_clear_message(client, seed_accounts):
    r = _post(client, b"<html></html>", "text/html", "evil.html")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "PNG" in detail and "SVG" in detail and "WebP" in detail


def test_oversize_rejected(client, seed_accounts):
    # Just over the 5 MB cap
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1)
    r = _post(client, payload, "image/png", "huge.png")
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_empty_upload_rejected(client, seed_accounts):
    r = _post(client, b"", "image/png", "empty.png")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def _noise_png(side=64):
    """A PNG big enough that cutting it short loses image data."""
    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, "PNG")
    return out.getvalue()


DAMAGED = "The image file is cut short or damaged; export it again and upload that."


def test_a_png_cut_short_is_refused_and_nothing_is_kept(client, seed_accounts):
    """NEW-201: a truncated PNG passed the type, empty and size checks, and
    every PDF that carries the logo then answered 500 (WeasyPrint's
    "Truncated File Read")."""
    from PIL import ImageFile

    import weasyprint.images  # noqa: F401  (turns LOAD_TRUNCATED_IMAGES on)

    assert ImageFile.LOAD_TRUNCATED_IMAGES  # as in a running app
    whole = _noise_png()
    for cut in (whole[: len(whole) // 2], whole[:-12]):  # data lost; no end chunk
        r = _post(client, cut, "image/png", "cut.png")
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == DAMAGED
    assert client.get("/api/uploads/logo").json().get("path") in (None, "")
    # the same PNG whole is kept
    r = _post(client, whole, "image/png", "whole.png")
    assert r.status_code == 200, r.text
    assert client.get(r.json()["path"]).content == whole


def test_bytes_that_are_no_image_are_refused(client, seed_accounts):
    for ctype in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        r = _post(client, b"\x89PNG\r\n\x1a\n", ctype, "sig-only")
        assert r.status_code == 400, (ctype, r.text)
        assert r.json()["detail"] == DAMAGED
