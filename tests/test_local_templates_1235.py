"""Local templates for printed documents (#1235).

The server's operator may name a folder (SLOWBOOKS_LOCAL_TEMPLATES_DIR, or
the data folder's local-templates sub-folder on a desktop install) that the
printed-document environment looks in first. A file there replaces the
built-in template of the same name; anything else is the built-in one.
"""

import ast
import logging
import os
from pathlib import Path

import pytest
from jinja2 import FileSystemLoader, TemplateNotFound

from app.services import local_templates
from app.services.local_templates import ENV_VAR, LocalTemplateError
from app.services.pdf_service import TEMPLATE_DIR, _jinja_env

LOCAL_INVOICE = """<!DOCTYPE html>
<html><head><style>body{font-family:serif}</style></head><body>
{% include '_firm_header.html' %}
<h1>FIRM INVOICE {{ inv.invoice_number }}</h1>
<p>Bill to {{ bill_to_name }}; total {{ inv.total | currency }}</p>
{% include '_print_text.html' %}
<script>window.leak = 1;</script>
</body></html>
"""


@pytest.fixture
def local_dir(tmp_path, monkeypatch):
    folder = tmp_path / "local-templates"
    folder.mkdir()
    monkeypatch.setenv(ENV_VAR, str(folder))
    _jinja_env.cache.clear()
    yield folder
    _jinja_env.cache.clear()


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    _jinja_env.cache.clear()
    yield
    _jinja_env.cache.clear()


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [{"description": "Site plan review", "quantity": 2, "rate": 150}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _print(client, inv_id):
    r = client.get(f"/api/invoices/{inv_id}/print-preview")
    return r


def _statement(client, customer_id):
    r = client.get(
        f"/api/reports/customer-statement/{customer_id}/print?as_of_date=2026-10-01"
    )
    assert r.status_code == 200, r.text[:300]
    return r.text


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---- off by default: byte-for-byte what it was ----


def test_off_the_loader_is_the_builtin_loader_exactly(off):
    builtin = FileSystemLoader(str(TEMPLATE_DIR))
    for name in builtin.list_templates():
        assert (
            _jinja_env.loader.get_source(_jinja_env, name)[:2]
            == builtin.get_source(_jinja_env, name)[:2]
        ), name
    assert local_templates.active_folder() is None


def test_off_and_an_empty_folder_print_the_same_pages(
    client, seed_accounts, seed_customer, tmp_path, monkeypatch, off
):
    inv = _invoice(client, seed_customer.id)
    page_off = _print(client, inv["id"]).text
    statement_off = _statement(client, seed_customer.id)
    assert "Content-Security-Policy" not in page_off
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(ENV_VAR, str(empty))
    _jinja_env.cache.clear()
    assert _print(client, inv["id"]).text == page_off
    assert _statement(client, seed_customer.id) == statement_off


def test_a_desktop_data_folder_without_the_sub_folder_is_off(
    off, tmp_path, monkeypatch
):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    assert local_templates.active_folder() is None
    (tmp_path / "local-templates").mkdir()
    assert local_templates.active_folder() == (tmp_path / "local-templates").resolve()
    assert local_templates.status(str(TEMPLATE_DIR))["source"] == "data folder"


# ---- override, fallback, includes ----


def test_a_local_invoice_replaces_the_builtin_and_includes_resolve_both_ways(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", LOCAL_INVOICE)
    _write(local_dir, "_firm_header.html", "<div>FIRM HEADER PARTIAL</div>")
    inv = _invoice(client, seed_customer.id)
    r = _print(client, inv["id"])
    assert r.status_code == 200, r.text[:300]
    page = r.text
    assert f"FIRM INVOICE {inv['invoice_number']}" in page
    assert "FIRM HEADER PARTIAL" in page  # the local partial
    assert ".long-word {" in page  # the built-in _print_text.html partial
    assert "$300.00" in page
    # the PDF is drawn from it too
    pdf = client.get(f"/api/invoices/{inv['id']}/pdf")
    if pdf.status_code == 200:
        assert pdf.content[:5] == b"%PDF-"


def test_a_local_partial_replaces_the_builtin_one_inside_a_builtin_page(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "_print_text.html", "<style>.firm-rule{}</style>")
    page = _statement(client, seed_customer.id)
    assert ".firm-rule{}" in page
    assert ".long-word {" not in page


def test_templates_not_in_the_folder_fall_back_to_the_builtin(
    client, seed_accounts, seed_customer, local_dir, monkeypatch
):
    monkeypatch.delenv(ENV_VAR)
    _jinja_env.cache.clear()
    statement_off = _statement(client, seed_customer.id)
    monkeypatch.setenv(ENV_VAR, str(local_dir))
    _write(local_dir, "invoice_pdf.html", LOCAL_INVOICE)
    _write(local_dir, "_firm_header.html", "")
    _jinja_env.cache.clear()
    assert _statement(client, seed_customer.id) == statement_off


def test_an_edited_added_or_removed_local_file_is_seen_without_a_restart(
    client, seed_accounts, seed_customer, local_dir
):
    inv = _invoice(client, seed_customer.id)
    builtin_page = _print(client, inv["id"]).text
    path = _write(local_dir, "invoice_pdf.html", "<p>ONE</p>")
    assert "ONE" in _print(client, inv["id"]).text  # added after a built-in load
    path.write_text("<p>TWO</p>", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert "TWO" in _print(client, inv["id"]).text  # edited
    path.unlink()
    assert _print(client, inv["id"]).text == builtin_page  # removed


# ---- a local page's scripts never run with the session ----


def test_a_page_drawn_by_a_local_template_allows_only_the_print_script(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", LOCAL_INVOICE)
    _write(local_dir, "_firm_header.html", "")
    inv = _invoice(client, seed_customer.id)
    page = _print(client, inv["id"]).text
    assert page.startswith(
        '<!DOCTYPE html><meta http-equiv="Content-Security-Policy" content="script-src'
        " 'sha256-"
    )
    assert "connect-src 'none'" in page and "form-action 'none'" in page
    assert "'unsafe-inline'" not in page.split(">", 2)[1]


# ---- a broken local template is refused, by name ----


def test_a_local_template_that_does_not_parse_is_refused_by_name(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", "<p>{% if inv %}never closed</p>")
    inv = _invoice(client, seed_customer.id)
    r = _print(client, inv["id"])
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert 'local template "invoice_pdf.html"' in detail
    assert str(local_dir) in detail and "line 1" in detail
    assert "Traceback" not in r.text


def test_a_broken_local_partial_is_refused_by_name(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", LOCAL_INVOICE)
    _write(local_dir, "_firm_header.html", "<p>{{ inv.total + }}</p>")
    inv = _invoice(client, seed_customer.id)
    r = _print(client, inv["id"])
    assert r.status_code == 500
    assert 'local template "_firm_header.html"' in r.json()["detail"]


def test_a_local_template_that_fails_while_rendering_is_refused_by_name(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", "<p>ok</p>\n<p>{{ 1 // 0 }}</p>\n")
    inv = _invoice(client, seed_customer.id)
    r = _print(client, inv["id"])
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert 'local template "invoice_pdf.html"' in detail
    assert "line 2" in detail and "ZeroDivisionError" in detail
    # and the PDF route refuses the same way (it never falls back)
    pdf = client.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 500
    assert 'local template "invoice_pdf.html"' in pdf.json()["detail"]


# ---- names cannot leave the folder ----


@pytest.mark.parametrize(
    "name", ["../outside.html", "/etc/hostname", "a\\..\\b.html", "C:/x.html", "a//b"]
)
def test_a_name_that_could_leave_the_folder_is_refused(local_dir, name):
    (local_dir.parent / "outside.html").write_text("OUTSIDE")
    with pytest.raises(TemplateNotFound):
        _jinja_env.get_template(name)


def test_an_include_that_climbs_out_is_refused(local_dir):
    (local_dir.parent / "outside.html").write_text("OUTSIDE")
    _write(local_dir, "labels_5160_pdf.html", "{% include '../outside.html' %}")
    with pytest.raises(LocalTemplateError):
        _jinja_env.get_template("labels_5160_pdf.html").render(pages=[])


def test_a_symlink_is_followed_only_inside_the_folder(local_dir):
    (local_dir.parent / "outside.html").write_text("OUTSIDE")
    _write(local_dir, "parts/inside.html", "INSIDE")
    try:
        (local_dir / "labels_5160_pdf.html").symlink_to(local_dir / "parts/inside.html")
        (local_dir / "check_pdf.html").symlink_to(local_dir.parent / "outside.html")
    except OSError:
        pytest.skip("no symbolic links here")
    assert "INSIDE" in _jinja_env.get_template("labels_5160_pdf.html").render()
    with pytest.raises(LocalTemplateError, match="outside the local templates folder"):
        _jinja_env.get_template("check_pdf.html")


# ---- only the operator chooses the folder ----


def test_the_folder_is_read_from_the_environment_only():
    app_dir = Path(local_templates.__file__).resolve().parent.parent
    readers = [
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if ENV_VAR in p.read_text(encoding="utf-8")
        or "DATA_SUBFOLDER" in p.read_text(encoding="utf-8")
    ]
    assert readers == ["services/local_templates.py"]
    # and it reads no company, setting or request: it imports nothing of the app
    tree = ast.parse(Path(local_templates.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [m for m in imported if m.startswith("app")]


def test_a_setting_of_that_name_in_the_company_changes_nothing(
    client, local_dir, monkeypatch
):
    monkeypatch.delenv(ENV_VAR)
    client.put("/api/settings", json={"local_templates_dir": str(local_dir)})
    assert local_templates.active_folder() is None


# ---- diagnostics and startup ----


def test_diagnostics_list_the_replaced_builtin_templates(client, local_dir):
    _write(local_dir, "invoice_pdf.html", LOCAL_INVOICE)
    _write(local_dir, "statement_pdf.html", "<p></p>")
    _write(local_dir, "_firm_header.html", "")
    body = client.get("/api/support/diagnostics").json()
    lt = body["local_templates"]
    assert lt["enabled"] and lt["source"] == "environment" and not lt["problem"]
    assert lt["replaced"] == ["invoice_pdf.html", "statement_pdf.html"]
    assert lt["added"] == ["_firm_header.html"]
    assert (
        "Local templates: on (environment); replaces: invoice_pdf.html,"
        " statement_pdf.html; added files: 1"
    ) in body["environment"]
    assert str(local_dir) not in body["environment"]


def test_diagnostics_off_say_nothing(client, off):
    body = client.get("/api/support/diagnostics").json()
    assert "Local templates" not in body["environment"]
    assert body["local_templates"]["enabled"] is False


def test_a_missing_folder_is_reported_not_a_crash(
    client, seed_accounts, seed_customer, tmp_path, monkeypatch, caplog, off
):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "not-there"))
    body = client.get("/api/support/diagnostics").json()
    assert body["local_templates"]["problem"].endswith(
        "does not exist or is not a folder"
    )
    assert "Local templates: off (" in body["environment"]
    with caplog.at_level(logging.WARNING):
        local_templates.report_at_startup(str(TEMPLATE_DIR))
    assert "local templates are off" in caplog.text
    inv = _invoice(client, seed_customer.id)
    assert _print(client, inv["id"]).status_code == 200  # built-in, as before
