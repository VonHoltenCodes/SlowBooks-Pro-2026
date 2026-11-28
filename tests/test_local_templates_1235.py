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


def _link(monkeypatch, link: Path, target: Path) -> None:
    """A symbolic link at ``link`` to ``target``. Where the system will not
    make one (Windows without the privilege), the loader is shown one: the
    link's path reports itself a link and resolves to the target, which is
    all the loader asks of a link."""
    try:
        link.symlink_to(target)
        return
    except OSError:
        pass
    real_resolve, real_is_symlink = Path.resolve, Path.is_symlink
    key = real_resolve(link.parent) / link.name
    to = real_resolve(target)

    def resolve(self, *args, **kwargs):
        if real_resolve(self.parent) / self.name == key:
            return to
        return real_resolve(self, *args, **kwargs)

    def is_symlink(self):
        if real_resolve(self.parent) / self.name == key:
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_symlink", is_symlink)


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
    assert "line 1" in detail
    # the server's path stays in the log, never in what the user sees
    assert str(local_dir) not in detail and str(local_dir.parent) not in detail
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
    # the exception's own text (which may carry data or SQL) is not shown
    assert "division" not in detail and str(local_dir) not in detail
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


def test_a_symlink_is_followed_only_inside_the_folder(local_dir, monkeypatch):
    (local_dir.parent / "outside.html").write_text("OUTSIDE")
    _write(local_dir, "parts/inside.html", "INSIDE")
    _link(
        monkeypatch, local_dir / "labels_5160_pdf.html", local_dir / "parts/inside.html"
    )
    _link(monkeypatch, local_dir / "check_pdf.html", local_dir.parent / "outside.html")
    assert "INSIDE" in _jinja_env.get_template("labels_5160_pdf.html").render()
    with pytest.raises(LocalTemplateError, match="outside the local templates folder"):
        _jinja_env.get_template("check_pdf.html")


# ---- only the operator chooses the folder ----


def test_the_folder_is_read_from_the_environment_only():
    app_dir = Path(local_templates.__file__).resolve().parent.parent
    readers = [
        p.relative_to(app_dir).as_posix()
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


# ---- review fixes (#1235) ----


def test_off_prints_what_the_plain_builtin_loader_prints(
    client, seed_accounts, seed_customer, off
):
    """Byte-for-byte against the loader before this change: the plain
    FileSystemLoader(app/templates) swapped in, for the invoice and the
    statement Print pages."""
    inv = _invoice(client, seed_customer.id)
    page_new = _print(client, inv["id"]).text
    statement_new = _statement(client, seed_customer.id)
    loader = _jinja_env.loader
    try:
        _jinja_env.loader = FileSystemLoader(str(TEMPLATE_DIR))
        _jinja_env.cache.clear()
        assert _print(client, inv["id"]).text == page_new
        assert _statement(client, seed_customer.id) == statement_new
    finally:
        _jinja_env.loader = loader
        _jinja_env.cache.clear()


def test_print_all_keeps_the_script_guard_in_the_head(
    client, seed_accounts, seed_customer, local_dir
):
    # a local invoice with no <head> or <body> of its own
    _write(local_dir, "invoice_pdf.html", "<p>X {{ inv.invoice_number }}</p>")
    a = _invoice(client, seed_customer.id)
    b = _invoice(client, seed_customer.id)
    r = client.get(f"/api/invoices/print-preview?ids={a['id']},{b['id']}")
    assert r.status_code == 200, r.text[:300]
    page = r.text
    guard = '<meta http-equiv="Content-Security-Policy"'
    assert page.count(guard) == 1
    assert page.index(guard) < page.lower().index("<body")


def test_print_all_lifts_a_guard_from_a_later_page():
    from app.routes.invoices.print_all import combine_documents
    from app.services.pdf_service import _guard_local_page, guard_meta

    meta = guard_meta()
    plain = "<!DOCTYPE html><html><head></head><body><p>one</p></body></html>"
    local = _guard_local_page(
        "<!DOCTYPE html><html><head></head><body><p>two</p></body></html>"
    )
    page = combine_documents([plain, local])
    assert page.startswith("<!DOCTYPE html>" + meta)
    assert page.count(meta) == 1
    # and a run of built-in pages is unchanged by the guard code
    assert "Content-Security-Policy" not in combine_documents([plain, plain])


def _active_policies(page):
    """The script-policy meta tags a browser would read in ``page``: the
    ones outside comments."""
    from app.services.pdf_service import guard_tags

    return guard_tags(page)


_FORGED = (
    '<!-- <meta http-equiv="Content-Security-Policy" '
    "content=\"script-src * 'unsafe-inline'\"> -->"
)


def _forged_local(text):
    """A page as a local template draws it: through the renderer, so it
    carries the renderer's own record (GuardedPage) and our guard."""
    from jinja2 import DictLoader

    from app.services.pdf_service import _jinja_env

    loader = _jinja_env.loader
    _jinja_env.loader = DictLoader({"t.html": "{{ _sb_local_template() }}" + text})
    _jinja_env.cache.clear()
    try:
        return _jinja_env.get_template("t.html").render()
    finally:
        _jinja_env.loader = loader
        _jinja_env.cache.clear()


def test_print_all_never_takes_a_guard_from_a_page_comment():
    # the verifier's case: the author's policy in a comment before the
    # doctype must not become the run's guard; ours must
    from app.routes.invoices.print_all import combine_documents
    from app.services.pdf_service import guard_meta, is_guarded

    body = "<!DOCTYPE html><html><head></head><body><p>{n}</p><script>x()</script>"
    pages = [
        _forged_local(_FORGED + body.format(n="one") + "</body></html>"),
        _forged_local(_FORGED + body.format(n="two") + "</body></html>"),
    ]
    assert all(map(is_guarded, pages))
    page = combine_documents(pages)
    assert is_guarded(page)
    live = _active_policies(page)
    assert live == [guard_meta()], live
    assert page.startswith(_FORGED + "<!DOCTYPE html>" + guard_meta())
    assert page.count("Content-Security-Policy") == 2  # ours + the comment's
    assert "<p>one</p>" in page and "<p>two</p>" in page


def test_a_local_page_without_its_guard_still_gets_ours():
    from app.routes.invoices.print_all import combine_documents
    from app.services.pdf_service import GuardedPage, guard_meta

    plain = "<!DOCTYPE html><html><head></head><body><p>one</p></body></html>"
    local = _forged_local("<!DOCTYPE html><html><body><p>two</p></body></html>")
    meta = guard_meta()
    removed = GuardedPage(local.replace(meta, ""))
    renamed = GuardedPage(
        local.replace(meta, meta.replace("Content-Security-Policy", "X-Not-It"))
    )
    authored = GuardedPage(
        local.replace(
            meta, '<META HTTP-EQUIV=content-security-policy CONTENT="script-src *">'
        )
    )
    for page in (removed, renamed, authored):
        for run in ([plain, page], [page, plain], [page, page]):
            combined = combine_documents(run)
            assert _active_policies(combined) == [meta], combined[:400]
            assert combined.startswith("<!DOCTYPE html>" + meta)


def test_a_built_in_run_gains_no_guard_from_a_documents_data(
    client, seed_accounts, seed_customer, off
):
    meta = '<meta http-equiv="Content-Security-Policy" content="script-src *">'
    a = _invoice(client, seed_customer.id)
    b = _invoice(client, seed_customer.id)
    r = client.put(f"/api/invoices/{a['id']}", json={"notes": meta})
    assert r.status_code == 200, r.text[:300]
    r = client.get(f"/api/invoices/print-preview?ids={a['id']},{b['id']}")
    assert r.status_code == 200, r.text[:300]
    page = r.text
    assert "Content-Security-Policy" in page  # the memo is printed, escaped
    assert meta not in page
    assert _active_policies(page) == []
    assert "<meta http-equiv" not in page.lower().replace(
        '<meta http-equiv="content-type"', ""
    )


def test_the_short_comment_forms_close_the_comment():
    from app.routes.invoices.print_all import combine_documents
    from app.services.pdf_service import doctype_end, drop_guards, guard_meta

    for short in ("<!-->", "<!--->"):
        # HTML: the short form is a whole comment, so this doctype is real
        page = short + "<!DOCTYPE html>--><!DOCTYPE html><p>"
        assert doctype_end(page) == len(short + "<!DOCTYPE html>"), short
        # and the next tag after it is a tag, not comment text
        live = '<meta http-equiv="Content-Security-Policy" content="x">'
        assert drop_guards(short + live + "-->") == short + "-->", short
        local = _forged_local(page.replace("<p>", "<body><p>") + "</body>")
        combined = combine_documents([local, local])
        assert combined.startswith(short + "<!DOCTYPE html>" + guard_meta())
        assert _active_policies(combined) == [guard_meta()]
    # --!> also ends a comment, as HTML reads it
    assert doctype_end("<!-- a --!><!DOCTYPE html>") == len(
        "<!-- a --!><!DOCTYPE html>"
    )
    # text inside a comment is never a doctype nor a guard
    hidden = '<!-- <!DOCTYPE html><meta http-equiv="Content-Security-Policy"> -->'
    assert doctype_end(hidden + "<p>") == 0
    assert drop_guards(hidden) == hidden


def test_a_comment_before_the_doctype_keeps_the_guard_after_it(
    client, seed_accounts, seed_customer, local_dir
):
    _write(
        local_dir,
        "invoice_pdf.html",
        "<!-- firm layout -->\n<!DOCTYPE html><html><body>ok</body></html>",
    )
    inv = _invoice(client, seed_customer.id)
    page = _print(client, inv["id"]).text
    assert page.startswith(
        '<!-- firm layout -->\n<!DOCTYPE html><meta http-equiv="Content-Security'
    )


def test_a_missing_name_in_a_local_template_shows_jinjas_own_words(
    client, seed_accounts, seed_customer, local_dir
):
    _write(local_dir, "invoice_pdf.html", "<p>{{ gone_value.amount }}</p>")
    inv = _invoice(client, seed_customer.id)
    r = _print(client, inv["id"])
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert 'local template "invoice_pdf.html"' in detail
    assert "UndefinedError: 'gone_value' is undefined" in detail


def test_a_broken_link_or_a_folder_is_refused_not_the_builtin(local_dir, monkeypatch):
    _link(monkeypatch, local_dir / "labels_5160_pdf.html", local_dir / "nothing.html")
    (local_dir / "check_pdf.html").mkdir()
    for name in ("labels_5160_pdf.html", "check_pdf.html"):
        with pytest.raises(LocalTemplateError, match="not a readable file"):
            _jinja_env.get_template(name)
    replaced = local_templates.status(str(TEMPLATE_DIR))["replaced"]
    assert "labels_5160_pdf.html" in replaced and "check_pdf.html" in replaced


def test_diagnostics_list_what_the_loader_draws_on_a_case_insensitive_disk(
    local_dir, monkeypatch
):
    """On Windows or macOS a local Invoice_PDF.html replaces
    invoice_pdf.html at print; diagnostics must say so, by the built-in
    name, and not count it as an added file."""
    _write(local_dir, "Invoice_PDF.html", "<p>firm</p>")
    real = local_templates._local_file

    def folded(folder, name):
        for p in folder.iterdir():
            if p.name.casefold() == name.casefold():
                return real(folder, p.name)
        return real(folder, name)

    monkeypatch.setattr(local_templates, "_local_file", folded)
    s = local_templates.status(str(TEMPLATE_DIR))
    assert s["replaced"] == ["invoice_pdf.html"]
    assert s["added"] == []


def test_an_unreadable_data_folder_names_the_data_folder_not_the_setting(
    off, tmp_path, monkeypatch
):
    folder = tmp_path / "local-templates"
    folder.mkdir()
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    # the folder cannot be read: shown to the code by the check it makes,
    # not by permissions (root and Windows read through a chmod)
    real_access = os.access

    def access(path, mode, *args, **kwargs):
        if Path(path) == folder:
            return False
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(local_templates.os, "access", access)
    problem = local_templates.status(str(TEMPLATE_DIR))["problem"]
    assert problem == (
        "the data folder's local-templates sub-folder is a folder this server"
        " cannot read"
    )
    assert ENV_VAR not in problem


def test_emailing_an_invoice_with_a_broken_local_template_names_it(
    client, seed_accounts, seed_customer, local_dir, caplog
):
    _write(local_dir, "invoice_pdf.html", "<p>{{ 1 // 0 }}</p>")
    inv = _invoice(client, seed_customer.id)
    with caplog.at_level(logging.WARNING):
        r = client.post(
            f"/api/invoices/{inv['id']}/email", json={"recipient": "a@example.test"}
        )
    # the path and the error's own text are in the server log
    assert str(local_dir) in caplog.text and "modulo by zero" in caplog.text
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail.startswith("Email failed: ")
    assert 'local template "invoice_pdf.html"' in detail
    assert "UnboundLocalError" not in r.text


def test_batch_sends_name_a_broken_local_template(
    client, db_session, seed_accounts, local_dir
):
    from datetime import date, timedelta
    from decimal import Decimal

    from app.models.contacts import Customer
    from app.models.invoices import Invoice, InvoiceStatus
    from app.services import collections as svc
    from app.services.settings_service import set_setting

    # a mail server is named, but nothing reaches it: the PDF fails first
    set_setting(db_session, "smtp_host", "mail.invalid")
    db_session.commit()
    c = Customer(
        name="Probe Customer",
        email="probe@example.test",
        is_active=True,
        monthly_statement=True,
    )
    db_session.add(c)
    db_session.commit()
    db_session.add(
        Invoice(
            invoice_number=f"LT-{c.id}",
            customer_id=c.id,
            status=InvoiceStatus.SENT,
            date=date.today() - timedelta(days=90),
            due_date=date.today() - timedelta(days=45),
            total=Decimal(100),
            balance_due=Decimal(100),
        )
    )
    db_session.commit()
    _write(local_dir, "statement_pdf.html", "<p>{{ 1 // 0 }}</p>")
    _write(local_dir, "invoice_pdf.html", "<p>{{ 1 // 0 }}</p>")
    answers = []
    r = client.post("/api/collections/statements/run", json={})
    answers += r.json()["errors"]
    r = client.post("/api/reports/batch-email-statements")
    answers += r.json()["errors"]
    svc.seed_default_rules(db_session)
    r = client.post("/api/collections/reminders/run", json={})
    answers += r.json()["errors"]
    assert len(answers) == 3, answers
    assert 'local template "statement_pdf.html"' in answers[0]
    assert 'local template "statement_pdf.html"' in answers[1]
    assert 'local template "invoice_pdf.html"' in answers[2]
    assert all(str(local_dir) not in a for a in answers)


# ---- the page scans are linear in its length (code scanning py/redos) ----


@pytest.mark.parametrize(
    "start",
    [
        "<!--",  # a comment never closed
        "--><!--",  # closes and opens over and over
        "<!-- -->",  # many whole comments, then no doctype
        "<!-- --> ",
    ],
)
def test_finding_the_doctype_after_many_comments_takes_no_time(start):
    from time import perf_counter

    from app.services.pdf_service import doctype_end

    for tail in ("", "x", "<!doctype html>", "<!--"):
        page = "<!--" + start * 50_000 + tail
        began = perf_counter()
        doctype_end(page)
        assert perf_counter() - began < 2, (start, tail)


def test_the_doctype_is_found_after_any_comments_and_spacing():
    from app.services.pdf_service import doctype_end

    for page, before in [
        ("<!DOCTYPE html><html>", "<!DOCTYPE html>"),
        ("<!doctype HTML><p>", "<!doctype HTML>"),
        ("  \n\t<!DocType html >x", "  \n\t<!DocType html >"),
        ("<!-- one -->\n<!DOCTYPE html><html>", "<!-- one -->\n<!DOCTYPE html>"),
        (
            "<!--a--> <!-- b\n-- c -->\r\n<!---->\n<!doctype html><p>",
            "<!--a--> <!-- b\n-- c -->\r\n<!---->\n<!doctype html>",
        ),
        (
            "<!--x--><!--y-->\n\n<!--z--><!DOCTYPE html>.",
            "<!--x--><!--y-->\n\n<!--z--><!DOCTYPE html>",
        ),
    ]:
        assert doctype_end(page) == len(before), page
    # no doctype, or one hidden in a comment or behind text: 0, as before
    for page in [
        "",
        "<html><body>",
        "<p>x</p><!DOCTYPE html>",
        "<!-- <!DOCTYPE html> --><html>",
        "<!-- never closed <!DOCTYPE html>",
        "<!DOCTYPE html",
        "<!-- a --> text <!-- b --><!DOCTYPE html>",
    ]:
        assert doctype_end(page) == 0, page


def test_the_guard_tag_is_found_and_dropped_in_one_pass():
    from time import perf_counter

    from app.routes.invoices.print_all import _body_inner, combine_documents
    from app.services.pdf_service import GuardedPage, drop_guards

    meta = '<meta http-equiv="Content-Security-Policy" content="x">'
    assert drop_guards("a" + meta + "b" + meta + "c") == "abc"
    assert drop_guards("a<meta http-equiv='content-security-policy'/>b") == "ab"
    assert drop_guards('a<meta content="a>b" http-equiv=Content-Security-Policy>b') == (
        "ab"
    )
    other = '<meta charset="utf-8"><meta name="x" content="Content-Security-Policy">'
    assert drop_guards(other) == other
    unclosed = '<meta http-equiv="Content-Security-Policy" content="x"'
    assert drop_guards(unclosed) == unclosed
    assert _body_inner("<html><BODY class=x>in</body>a</Body></html>") == "in</body>a"
    assert _body_inner("<html><body>never closed") is None
    assert _body_inner("</body><body>") is None
    for page in (
        unclosed * 50_000,
        "<body" * 50_000,
        "<body>" * 50_000,
        "<meta " * 50_000,
        "<meta '" * 50_000,
        "<!-->" * 50_000,
        "<!-- --!" * 50_000,
    ):
        began = perf_counter()
        drop_guards(page), _body_inner(page)
        combine_documents([GuardedPage(page), page])
        assert perf_counter() - began < 2, page[:20]
