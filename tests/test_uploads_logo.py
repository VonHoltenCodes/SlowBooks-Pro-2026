"""Regression tests for /api/uploads/logo.

Covers the bug from issue #10: the UI promised SVG support but the server
rejected it. After the fix the server accepts the same five formats the UI
advertises (PNG, JPEG, GIF, WebP, SVG) and rejects everything else with a
clear, JSON-shaped error.
"""
import io


def _post(client, content, content_type, filename="logo.png"):
    return client.post(
        "/api/uploads/logo",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_png_upload_accepted(client, seed_accounts, tmp_path, monkeypatch):
    # Redirect the upload directory at import-time UPLOAD_DIR; tests should
    # never write into the live app/static tree.
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    r = _post(client, b"\x89PNG\r\n\x1a\n", "image/png", "logo.png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].endswith("/company_logo.png")


def test_svg_upload_now_accepted(client, seed_accounts, tmp_path, monkeypatch):
    # Issue #10: SVG was promised but rejected. After the fix it's accepted.
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    svg = b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'/>"
    r = _post(client, svg, "image/svg+xml", "logo.svg")
    assert r.status_code == 200, r.text
    assert r.json()["path"].endswith("/company_logo.svg")


def test_webp_upload_accepted(client, seed_accounts, tmp_path, monkeypatch):
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    r = _post(client, b"RIFF\x00\x00\x00\x00WEBP", "image/webp", "logo.webp")
    assert r.status_code == 200, r.text
    assert r.json()["path"].endswith("/company_logo.webp")


def test_extension_derives_from_content_type_not_filename(
    client, seed_accounts, tmp_path, monkeypatch,
):
    """An attacker-renamed file (e.g. exe pretending to be .png) shouldn't
    land on disk with a misleading suffix. We trust the content-type the
    framework verified, not the user-supplied filename."""
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    r = _post(client, b"\x89PNG\r\n\x1a\n", "image/png", "logo.exe")
    assert r.status_code == 200, r.text
    # Saved as .png, not .exe
    assert r.json()["path"].endswith("/company_logo.png")
    assert (tmp_path / "company_logo.png").exists()
    assert not (tmp_path / "company_logo.exe").exists()


def test_disallowed_mime_rejected_with_clear_message(
    client, seed_accounts, tmp_path, monkeypatch,
):
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    r = _post(client, b"<html></html>", "text/html", "evil.html")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "PNG" in detail and "SVG" in detail and "WebP" in detail


def test_oversize_rejected(client, seed_accounts, tmp_path, monkeypatch):
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)
    # Just over the 5 MB cap
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1)
    r = _post(client, payload, "image/png", "huge.png")
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_empty_upload_rejected(client, seed_accounts, tmp_path, monkeypatch):
    from app.routes import uploads as uploads_route
    monkeypatch.setattr(uploads_route, "UPLOAD_DIR", tmp_path)

    r = _post(client, b"", "image/png", "empty.png")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()
