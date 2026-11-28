"""The company's own logo in SlowBooks Pro (2.32.0).

One setting, "Show the company logo in SlowBooks Pro" (company_logo_in_app,
off by default, an administrator's, offered once a logo is uploaded). On, the
logo the company uploaded in Settings shows in the top bar where the product's
mark sat before #978, on the sign-in screen, on Company Files and in the
desktop app's company picker. Before a sign-in it comes from
GET /api/auth/company-logo, which serves the image and nothing else, only for
a company that opted in, and never opens a company file to find it. Off,
every page and the settings answer read as they did.
"""

import base64
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.database import Base
from app.main import app
from app.models.settings import DEFAULT_SETTINGS
from app.services import company_logo, file_store, served_companies
from tests.test_served_companies import (  # noqa: F401  (the fixture and helpers)
    A_PW,
    _sign_in,
    served as served_host,
)

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
CSS = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
DARK = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
SHELL = (ROOT / "index.html").read_text(encoding="utf-8")
FIT = (JS / "topbar_fit.js").read_text(encoding="utf-8")
APP_JS = (JS / "app.js").read_text(encoding="utf-8")
AUTH_JS = (JS / "auth.js").read_text(encoding="utf-8")
COMPANIES_JS = (JS / "companies.js").read_text(encoding="utf-8")
SETTINGS_JS = (JS / "settings.js").read_text(encoding="utf-8")

# a whole 1x1 PNG (an upload verifies a raster logo before keeping it)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>'
NO_LOGO = {"detail": "No logo is set."}
# the headers a "no logo" answer and a logo share, by value
SAME_HEADERS = ("cache-control", "referrer-policy", "x-content-type-options")


def _upload(c, data=PNG, content_type="image/png"):
    r = c.post(
        "/api/uploads/logo",
        files={"file": ("logo.png", io.BytesIO(data), content_type)},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["path"]


def _turn(c, on: bool):
    r = c.put("/api/settings", json={"company_logo_in_app": "true" if on else "false"})
    assert r.status_code == 200, r.text


def _visitor():
    """A browser with no session at all."""
    return TestClient(app)


def _no_logo_answer(r):
    assert r.status_code == 404, r.text
    assert r.json() == NO_LOGO
    return {h: r.headers.get(h) for h in SAME_HEADERS}


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_the_setting_is_off_by_default(client):
    assert DEFAULT_SETTINGS["company_logo_in_app"] == "false"
    assert client.get("/api/settings").json()["company_logo_in_app"] == "false"


def test_the_setting_takes_true_or_false_and_nothing_else(client):
    _turn(client, True)
    assert client.get("/api/settings").json()["company_logo_in_app"] == "true"
    _turn(client, False)
    for bad in ("yes", "1", "TRUE", ""):
        r = client.put("/api/settings", json={"company_logo_in_app": bad})
        assert r.status_code == 422, (bad, r.text)
    assert client.get("/api/settings").json()["company_logo_in_app"] == "false"


def test_only_an_administrator_changes_it():
    from app.main import _ADMIN_WRITE_PREFIXES

    # every settings write is an administrator's (the logo's own rule)
    assert "/api/settings" in _ADMIN_WRITE_PREFIXES


def test_removing_the_logo_turns_the_setting_off(client):
    """A logo uploaded later (say, for invoices alone) never shows in the
    app, on the sign-in screen or to a visitor until it is turned on again."""
    _upload(client)
    _turn(client, True)
    assert client.delete("/api/uploads/logo").status_code == 200
    assert client.get("/api/settings").json()["company_logo_in_app"] == "false"
    _upload(client)
    _no_logo_answer(_visitor().get("/api/auth/company-logo"))
    assert "company_logo_url" not in _visitor().get("/api/auth/status").json()


def test_removing_a_logo_never_shown_writes_no_setting(client, TestSession):
    from app.services.settings_service import get_setting_raw

    _upload(client)
    assert client.delete("/api/uploads/logo").status_code == 200
    db = TestSession()
    try:
        assert get_setting_raw(db, "company_logo_in_app") is None
    finally:
        db.close()


def test_settings_reads_as_it_did_with_the_setting_off(client, monkeypatch):
    """The one new key, at "false", is all that differs."""
    with_it = client.get("/api/settings").json()
    trimmed = {k: v for k, v in DEFAULT_SETTINGS.items() if k != "company_logo_in_app"}
    monkeypatch.setattr("app.services.settings_service.DEFAULT_SETTINGS", trimmed)
    without = client.get("/api/settings").json()
    assert "company_logo_in_app" not in without
    assert with_it == {**without, "company_logo_in_app": "false"}


def test_settings_offers_it_to_an_administrator_once_a_logo_is_set():
    start = SETTINGS_JS.index(
        '${SettingsPage._isAdmin() ? `<label for="company-logo-in-app"'
    )
    block = SETTINGS_JS[start:]
    block = block[: block.index("` : ''}")]
    assert 'name="company_logo_in_app"' in block and "data-bool" in block
    assert "data-admin" in block and "data-write" in block
    assert "${s.company_logo_in_app === 'true' ? 'checked' : ''}" in block
    assert "Show the company logo in SlowBooks Pro" in block
    # inside the block drawn only when a logo is set, after Logo only
    logo_block = SETTINGS_JS.split(
        '${s.company_logo_path ? `<div style="margin-top:8px;">'
    )[1]
    logo_block = logo_block.split("</div>` : ''}")[0]
    assert 'id="company-logo-in-app"' in logo_block
    assert logo_block.index("invoice-logo-only") < logo_block.index(
        "company-logo-in-app"
    )


# ---------------------------------------------------------------------------
# Before a sign-in: GET /api/auth/company-logo and the sign-in status
# ---------------------------------------------------------------------------


def test_a_company_that_opted_in_has_its_logo_served_without_a_session(client):
    _upload(client)
    _turn(client, True)
    r = _visitor().get("/api/auth/company-logo")
    assert r.status_code == 200, r.text
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-security-policy"] == file_store.SERVED_FILE_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store, max-age=0"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in r.headers


def test_not_opted_in_reads_exactly_as_no_logo(client):
    visitor = _visitor()
    # no logo, not opted in
    none_at_all = _no_logo_answer(visitor.get("/api/auth/company-logo"))
    # no logo, opted in
    _turn(client, True)
    opted_no_logo = _no_logo_answer(visitor.get("/api/auth/company-logo"))
    # a logo, not opted in
    _turn(client, False)
    _upload(client)
    logo_not_opted = _no_logo_answer(visitor.get("/api/auth/company-logo"))
    assert none_at_all == opted_no_logo == logo_not_opted
    assert logo_not_opted["cache-control"] == "no-store, max-age=0"


def test_a_signed_in_logo_address_is_not_one_the_route_takes(client):
    path = _upload(client)
    _turn(client, True)
    logo_id = path.rsplit("/", 1)[1]
    visitor = _visitor()
    # an id or a path in the request changes nothing: the image is the one
    # the company's own settings name
    assert visitor.get(f"/api/auth/company-logo?v={logo_id}").content == PNG
    assert visitor.get("/api/auth/company-logo?v=999999").content == PNG
    assert visitor.get(path).status_code == 401
    # no address of its own per id: nothing is routed there
    assert visitor.get("/api/auth/company-logo/" + logo_id).status_code == 404


def test_a_single_company_server_names_no_other(client):
    _upload(client)
    _turn(client, True)
    visitor = _visitor()
    for other in ("zulu.db", "../bookkeeper.db", "", "alpha.db"):
        _no_logo_answer(visitor.get("/api/auth/company-logo", params={"file": other}))


def test_the_sign_in_status_gives_the_address_only_when_opted_in(client):
    visitor = _visitor()
    off = visitor.get("/api/auth/status").json()
    assert "company_logo_url" not in off
    _upload(client)
    still_off = visitor.get("/api/auth/status").json()
    assert still_off == off  # a logo alone changes nothing
    _turn(client, True)
    on = visitor.get("/api/auth/status").json()
    url = on.pop("company_logo_url")
    assert on == off
    assert re.fullmatch(r"/api/auth/company-logo\?v=\d+", url), url
    assert visitor.get(url).content == PNG


def test_a_program_is_not_given_the_logo_address(client):
    _upload(client)
    _turn(client, True)
    r = client.post("/api/tokens", json={"label": "reader", "role": "readonly"})
    program = _visitor()
    program.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert "company_logo_url" not in program.get("/api/auth/status").json()


def test_the_route_is_rate_limited_like_its_neighbors():
    source = (ROOT / "app/routes/auth.py").read_text(encoding="utf-8")
    head = source.split("def company_logo_image(")[0].rsplit("@router.get", 1)[1]
    assert head.startswith('("/company-logo")')
    assert '@limiter.limit("60/minute")' in head
    # and it is public only as the auth routes are: under /api/auth/
    from app.main import _AUTH_EXEMPT_PREFIXES

    assert company_logo.PUBLIC_PATH.startswith("/api/auth/")
    assert "/api/auth/" in _AUTH_EXEMPT_PREFIXES


# ---------------------------------------------------------------------------
# Several companies on one server
# ---------------------------------------------------------------------------


@pytest.fixture
def host(served_host):  # noqa: F811
    """The Server Edition host with two companies (tests/test_served_companies.py)."""
    return served_host


def _give_logo(folder, filename, data, content_type, on):
    engine = db_module.create_engine("sqlite:///" + (folder / filename).as_posix())
    with sessionmaker(bind=engine)() as db:
        from app.services.settings_service import set_setting

        file_store.replace_logo(db, data, content_type)
        set_setting(db, "company_logo_in_app", "true" if on else "false")
        db.commit()
    engine.dispose()


def test_each_served_company_shows_its_own_logo(
    host,
):
    folder = host / "companies"
    _give_logo(folder, "alpha.db", PNG, "image/png", True)
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", True)
    visitor = _visitor()
    status = visitor.get("/api/auth/status").json()
    assert "file=alpha.db" in status["company_logo_url"]
    assert visitor.get(status["company_logo_url"]).content == PNG
    # the sign-in screen's Company choice: that company's own logo
    assert (
        visitor.post("/api/auth/company", json={"file": "bravo.db"}).status_code == 200
    )
    status = visitor.get("/api/auth/status").json()
    assert "file=bravo.db" in status["company_logo_url"]
    r = visitor.get(status["company_logo_url"])
    assert r.content == SVG and r.headers["content-type"] == "image/svg+xml"
    assert r.headers["content-security-policy"] == file_store.SERVED_FILE_CSP
    # one company's logo never for another, whatever the session chose
    assert visitor.get("/api/auth/company-logo?file=alpha.db").content == PNG
    assert visitor.get("/api/auth/company-logo").content == SVG
    # the same logo id in both files is still two logos
    assert status["company_logo_url"].endswith("v=1")


def test_a_served_company_that_did_not_opt_in_reads_as_no_logo(
    host,
):
    folder = host / "companies"
    _give_logo(folder, "alpha.db", PNG, "image/png", True)
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", False)
    visitor = _visitor()
    visitor.post("/api/auth/company", json={"file": "bravo.db"})
    assert "company_logo_url" not in visitor.get("/api/auth/status").json()
    bravo = _no_logo_answer(visitor.get("/api/auth/company-logo?file=bravo.db"))
    unknown = _no_logo_answer(visitor.get("/api/auth/company-logo?file=zulu.db"))
    assert bravo == unknown


def test_a_company_file_not_open_is_never_opened_for_its_logo(host, monkeypatch):
    folder = host / "companies"
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", True)
    opened = []
    real = served_companies.open_engine
    monkeypatch.setattr(
        served_companies, "open_engine", lambda f: opened.append(f) or real(f)
    )
    visitor = _visitor()
    _no_logo_answer(visitor.get("/api/auth/company-logo?file=bravo.db"))
    assert opened == []
    # once the sign-in screen has it open, it shows
    visitor.post("/api/auth/company", json={"file": "bravo.db"})
    visitor.get("/api/auth/status")
    assert opened == ["bravo.db"]
    assert visitor.get("/api/auth/company-logo?file=bravo.db").content == SVG


def test_a_locked_company_shows_no_logo(host, monkeypatch):
    from app.services import company_file

    folder = host / "companies"
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", True)
    tries = []

    def locked(filename):
        tries.append(filename)
        raise served_companies.CompanyUnavailable(company_file.WRONG_KEY_MESSAGE)

    monkeypatch.setattr(served_companies, "open_engine", locked)
    visitor = _visitor()
    assert (
        visitor.post("/api/auth/company", json={"file": "bravo.db"}).status_code == 200
    )
    status = visitor.get("/api/auth/status").json()
    assert status["company_unavailable"]
    assert "company_logo_url" not in status
    seen = len(tries)
    _no_logo_answer(visitor.get("/api/auth/company-logo"))
    _no_logo_answer(visitor.get("/api/auth/company-logo?file=bravo.db"))
    assert len(tries) == seen  # the logo opened nothing


def test_company_files_gives_each_served_company_its_logo(
    host,
):
    folder = host / "companies"
    _give_logo(folder, "alpha.db", PNG, "image/png", True)
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", True)
    # bravo's file is open once its sign-in screen has been shown
    opener = _visitor()
    opener.post("/api/auth/company", json={"file": "bravo.db"})
    opener.get("/api/auth/status")
    assert db_module.company_engine_is_open("bravo.db")
    c = _visitor()
    assert _sign_in(c, "pat", A_PW).status_code == 200
    rows = {r["file"]: r for r in c.get("/api/companies").json()}
    assert "file=alpha.db" in rows["alpha.db"]["logo_url"]
    assert "file=bravo.db" in rows["bravo.db"]["logo_url"]
    assert c.get(rows["bravo.db"]["logo_url"]).content == SVG
    assert c.get(rows["alpha.db"]["logo_url"]).content == PNG


def test_company_files_reads_as_it_did_with_the_setting_off(
    host,
):
    folder = host / "companies"
    _give_logo(folder, "alpha.db", PNG, "image/png", False)
    c = _visitor()
    assert _sign_in(c, "pat", A_PW).status_code == 200
    for row in c.get("/api/companies").json():
        assert "logo_url" not in row


@pytest.mark.parametrize("on", [False, True])
def test_company_files_never_opens_a_company_file(host, monkeypatch, on):
    """Company Files reads the list, as before the setting: a served company
    whose file is not open is never opened, unlocked or upgraded to find its
    logo (setting off or on), and shows none."""
    folder = host / "companies"
    _give_logo(folder, "alpha.db", PNG, "image/png", on)
    _give_logo(folder, "bravo.db", SVG, "image/svg+xml", on)
    c = _visitor()
    assert _sign_in(c, "pat", A_PW).status_code == 200
    # bravo is not open (nobody has used it since the server started)
    found = db_module._company_engines.pop("bravo.db", None)
    if found is not None:
        found.dispose()
    assert not db_module.company_engine_is_open("bravo.db")
    opened = []
    real = served_companies.open_engine
    monkeypatch.setattr(
        served_companies, "open_engine", lambda f: opened.append(f) or real(f)
    )
    rows = {r["file"]: r for r in c.get("/api/companies").json()}
    assert opened == []
    assert "logo_url" not in rows["bravo.db"]
    assert ("logo_url" in rows["alpha.db"]) is on


# ---------------------------------------------------------------------------
# The desktop app's company picker
# ---------------------------------------------------------------------------


def _company_file(path, on=True, data=PNG, content_type="image/png"):
    engine = db_module.create_engine("sqlite:///" + path.as_posix())
    from app.models.settings import Settings
    from app.models.stored_files import StoredFile

    Base.metadata.create_all(
        bind=engine, tables=[Settings.__table__, StoredFile.__table__]
    )
    with sessionmaker(bind=engine)() as db:
        from app.services.settings_service import set_setting

        if data is not None:
            file_store.replace_logo(db, data, content_type)
        set_setting(db, "company_logo_in_app", "true" if on else "false")
        db.commit()
    engine.dispose()
    return path


def test_the_picker_reads_an_opted_in_companys_logo(tmp_path):
    path = _company_file(tmp_path / "alpha.db")
    uri = company_logo.logo_data_uri_from_file(path)
    assert uri == "data:image/png;base64," + base64.b64encode(PNG).decode()
    # read as it lies: nothing written beside it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alpha.db"]


def test_the_picker_shows_none_for_a_company_not_opted_in_or_with_no_logo(tmp_path):
    assert (
        company_logo.logo_data_uri_from_file(_company_file(tmp_path / "a.db", on=False))
        == ""
    )
    assert (
        company_logo.logo_data_uri_from_file(
            _company_file(tmp_path / "b.db", data=None)
        )
        == ""
    )
    assert company_logo.logo_data_uri_from_file(tmp_path / "gone.db") == ""


def test_the_picker_leaves_out_a_logo_too_large_to_carry(tmp_path, monkeypatch):
    """Each refresh carries every logo through the window's bridge, so one
    over the picker's cap shows none there (the company's pages still do)."""
    assert company_logo.MAX_PICKER_LOGO_BYTES == 512 * 1024
    big = PNG + b"\0" * (company_logo.MAX_PICKER_LOGO_BYTES - len(PNG) + 1)
    path = _company_file(tmp_path / "big.db", data=big)
    assert company_logo.logo_data_uri_from_file(path) == ""
    at_cap = big[: company_logo.MAX_PICKER_LOGO_BYTES]
    path = _company_file(tmp_path / "cap.db", data=at_cap)
    assert company_logo.logo_data_uri_from_file(path).startswith(
        "data:image/png;base64,"
    )


def test_the_picker_never_opens_an_encrypted_company(tmp_path, monkeypatch):
    from app.services import company_file

    path = tmp_path / "locked.db"
    path.write_bytes(b"\x8a\x91 not plain SQLite " * 64)
    assert company_file.is_encrypted_file(path)

    def refuse():
        raise AssertionError("an encrypted company file was opened")

    monkeypatch.setattr(company_file, "sqlite_module", refuse)
    assert company_logo.logo_data_uri_from_file(path) == ""


def test_the_picker_lists_the_logo_only_for_a_company_that_has_one(
    tmp_path, monkeypatch
):
    import desktop_launcher
    from app.services import company_service

    folder = tmp_path / "companies"
    folder.mkdir()
    _company_file(folder / "alpha.db")
    _company_file(folder / "bravo.db", on=False)
    monkeypatch.setattr(company_service, "companies_dir", lambda: folder)
    monkeypatch.setattr(
        company_service,
        "manifest_list_companies",
        lambda: [
            {"name": "Alpha", "file": "alpha.db", "missing": False},
            {"name": "Bravo", "file": "bravo.db", "missing": False},
            {"name": "Gone", "file": "gone.db", "missing": True},
        ],
    )
    monkeypatch.setattr(company_service, "get_last_opened", lambda: "alpha.db")
    api = desktop_launcher.PickerApi.__new__(desktop_launcher.PickerApi)
    api._auto_open = False
    rows = {c["file"]: c for c in api.list_companies()["companies"]}
    assert rows["alpha.db"]["logo"].startswith("data:image/png;base64,")
    assert "logo" not in rows["bravo.db"] and "logo" not in rows["gone.db"]


def _node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    ).stdout


def _picker_row_function():
    from desktop_launcher import PICKER_HTML

    body = re.search(
        r"list\.innerHTML = info\.companies\.map\(function \(c\) \{\n(.*?)\n  \}\)\.join\(''\);",
        PICKER_HTML,
        re.S,
    )
    assert body, "the picker's list"
    uri = re.search(r"var LOGO_URI = (new RegExp\('.*?'\));", PICKER_HTML)
    assert uri
    return (
        "function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;')"
        ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); }\n"
        f"var LOGO_URI = {uri.group(1)};\n"
        f"function row(c, info) {{\n{body.group(1)}\n}}\n"
    )


def test_the_picker_draws_the_logo_as_an_image_and_nothing_else_changes():
    data_uri = "data:image/png;base64," + base64.b64encode(PNG).decode()
    cases = [
        {"name": "Alpha & Co", "file": "alpha.db"},
        {"name": "Alpha & Co", "file": "alpha.db", "logo": data_uri},
        {"name": "Bad", "file": "bad.db", "logo": 'data:text/html;base64,PGI+"x'},
        {"name": "Bad", "file": "bad.db"},
    ]
    out = _node(
        _picker_row_function() + f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map(c => row(c, {last_opened: ''}))));"
    )
    plain, with_logo, bad, bad_plain = json.loads(out)
    assert "<img" not in plain
    assert with_logo == plain.replace(
        '<div class="company" data-file="alpha.db">',
        '<div class="company" data-file="alpha.db">'
        f'<img class="logo" src="{data_uri}" alt="Alpha &amp; Co">',
    )
    assert bad == bad_plain  # not a logo's image: drawn as a company with none


# ---------------------------------------------------------------------------
# The top bar (signed in)
# ---------------------------------------------------------------------------


def _method(source: str, name: str) -> str:
    m = re.search(rf"\n    {name}\(s\) \{{.*?\n    \}},", source, re.S)
    assert m, name
    return m.group(0).strip().rstrip(",")


FAKE_DOM = r"""
function El(tag, id) { this.tagName = tag; this.id = id || ''; this.attrs = {}; this.hidden = false;
  this.children = []; this.parentNode = null; this.listeners = {}; this.className = ''; this.alt = ''; }
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.addEventListener = function (k, f) { this.listeners[k] = f; };
El.prototype.insertBefore = function (n, ref) { n.parentNode = this;
  this.children.splice(this.children.indexOf(ref), 0, n); };
El.prototype.remove = function () { var p = this.parentNode; if (!p) return;
  p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; };
var bar = new El('div', 'topbar');
['skip-link', 'sidebar-toggle', 'back-btn', 'topbar-company'].forEach(function (id) {
  var e = new El('x', id); e.parentNode = bar; bar.children.push(e); });
var document = {
  getElementById: function (id) { function find(n) { if (n.id === id) return n;
    for (var i = 0; i < n.children.length; i++) { var f = find(n.children[i]); if (f) return f; } return null; }
    return find(bar); },
  createElement: function (tag) { return new El(tag); },
};
function ids() { return bar.children.map(function (c) { return c.id; }); }
"""


def _top_bar(steps):
    methods = ",\n".join(
        _method(APP_JS, n) for n in ("companyLogoSrc", "showCompanyLogo")
    )
    script = (
        FAKE_DOM
        + f"var App = {{\n{methods}\n}};\nvar out = [];\n"
        + steps
        + "\nconsole.log(JSON.stringify(out));"
    )
    return json.loads(_node(script))


def test_with_the_setting_off_the_bar_is_as_index_html_draws_it():
    off, no_logo, bad_path = _top_bar(
        "App.showCompanyLogo({company_name: 'Acme', company_logo_path: '/api/uploads/logo/4'});"
        " out.push(ids());"
        "App.showCompanyLogo({company_name: 'Acme', company_logo_in_app: 'true', company_logo_path: ''});"
        " out.push(ids());"
        "App.showCompanyLogo({company_logo_in_app: 'true', company_logo_path: 'https://elsewhere/x.svg'});"
        " out.push(ids());"
    )
    plain = ["skip-link", "sidebar-toggle", "back-btn", "topbar-company"]
    assert off == no_logo == bad_path == plain
    # and index.html carries no logo of its own: the image is only ever added
    assert "topbar-logo" not in SHELL
    assert "company-logo" not in SHELL


def test_on_the_logo_sits_where_the_mark_was_named_by_the_company():
    on, again, off = _top_bar(
        "var s = {company_name: 'Acme Plumbing', company_logo_in_app: 'true', company_logo_path: '/api/uploads/logo/7'};"
        "App.showCompanyLogo(s); var img = document.getElementById('topbar-logo');"
        "out.push({ids: ids(), tag: img.tagName, src: img.getAttribute('src'), alt: img.alt,"
        " cls: img.className, err: typeof img.listeners.error});"
        "img.listeners.error(); out.push({hidden: img.hidden});"
        "App.showCompanyLogo(Object.assign({}, s, {company_logo_in_app: 'false'})); out.push(ids());"
    )
    assert on == {
        "ids": [
            "skip-link",
            "sidebar-toggle",
            "topbar-logo",
            "back-btn",
            "topbar-company",
        ],
        "tag": "img",
        "src": "/api/uploads/logo/7",
        "alt": "Acme Plumbing",
        "cls": "topbar-logo",
        "err": "function",
    }
    assert again == {"hidden": True}  # a logo that fails to load is put away
    assert off == ["skip-link", "sidebar-toggle", "back-btn", "topbar-company"]


def test_show_company_puts_the_logo_up_or_away_on_every_settings_read():
    show = _method(APP_JS, "showCompany")
    # before the early return for an unnamed company, so turning it off
    # always takes it away
    assert show.index("App.showCompanyLogo(App.settings);") < show.index("if (!name")


def test_the_logo_is_a_fixed_box_on_a_white_plate():
    rule = CSS.split("#topbar .topbar-logo {")[1].split("}")[0]
    for part in (
        "width: 120px",
        "height: 32px",
        "object-fit: contain",
        "background: #ffffff",
        "flex: 0 0 auto",
    ):
        assert part in rule, part
    assert not re.search(r"\d(vw|vh|vmin|vmax)\b", rule)
    assert "#topbar .topbar-logo[hidden]" in CSS
    assert "#topbar.tb-fold-logo .topbar-logo { display: none; }" in CSS
    # the dark bar keeps the plate (no dark background over it)
    dark = DARK.split("#topbar .topbar-logo {")[1].split("}")[0]
    assert "background" not in dark


def test_the_bar_folds_the_logo_before_the_theme_and_only_when_it_has_one():
    assert "return logo && bar.contains(logo) && !logo.hidden ? 1 : 0;" in FIT
    assert "return 1 + rackButtons().length + 2 + logoSteps();" in FIT
    assert "bar.classList.toggle('tb-fold-who', n >= r + 2);" in FIT
    assert "bar.classList.toggle('tb-fold-logo', l > 0 && n >= r + 3);" in FIT
    assert "bar.classList.toggle('tb-fold-theme', n >= r + 3 + l);" in FIT
    # the logo's coming and going is a change in the bar the fit watches
    assert "childList: true, subtree: true" in FIT
    assert "attributeFilter: ['class', 'hidden', 'style']" in FIT


def test_the_fold_steps_with_and_without_a_logo():
    """setLevel, run: with no logo the steps are as they were since #978
    (1 + rack + 2); with one, the logo goes after the sign-in name and
    before the theme switch."""
    set_level = re.search(
        r"    function setLevel\(n\) \{.*?\n    \}\n", FIT, re.S
    ).group(0)
    logo_steps = re.search(
        r"    function logoSteps\(\) \{.*?\n    \}\n", FIT, re.S
    ).group(0)
    steps = re.search(r"    function steps\(\) \{.*?\}\n", FIT).group(0)
    script = (
        r"""
function Cls() { this.s = {}; }
Cls.prototype.toggle = function (k, on) { if (on) this.s[k] = 1; else delete this.s[k]; };
var logo = null, more = {hidden: true};
var bar = {classList: new Cls(), contains: function (n) { return n === logo; }};
var rack = {classList: new Cls()};
var buttons = [1, 2, 3].map(function () { return {classList: new Cls()}; });
function rackButtons() { return buttons; }
var document = {getElementById: function () { return logo; }};
"""
        + logo_steps
        + steps
        + set_level
        + r"""
function run() { var out = []; for (var n = 0; n <= steps(); n++) { setLevel(n);
  out.push(Object.keys(bar.classList.s).sort().join(' ')); } return out; }
var without = run();
logo = {hidden: false}; var withLogo = run();
logo.hidden = true; var failed = run();
console.log(JSON.stringify([without, withLogo, failed]));
"""
    )
    without, with_logo, failed = json.loads(_node(script))
    assert len(without) == 1 + 1 + 3 + 2  # level 0, 1, three rack buttons, who, theme
    assert without[-2:] == [
        "tb-fold-1 tb-fold-who",
        "tb-fold-1 tb-fold-theme tb-fold-who",
    ]
    assert "tb-fold-logo" not in " ".join(without)
    assert len(with_logo) == len(without) + 1
    assert with_logo[-3:] == [
        "tb-fold-1 tb-fold-who",
        "tb-fold-1 tb-fold-logo tb-fold-who",
        "tb-fold-1 tb-fold-logo tb-fold-theme tb-fold-who",
    ]
    assert failed == without  # a logo put away takes no step


# ---------------------------------------------------------------------------
# The sign-in screen and Company Files
# ---------------------------------------------------------------------------


def _auth_function(name: str) -> str:
    m = re.search(rf"\n    function {name}\(\) \{{.*?\n    \}}\n", AUTH_JS, re.S)
    assert m, name
    return m.group(0)


def test_the_sign_in_screen_shows_the_logo_the_status_gave():
    script = (
        "function escapeText(t) { return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
        ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); }\n"
        "var companyLogo = '', existingCompany = {name: 'Acme & Sons'};\n"
        + _auth_function("companyLogoHTML")
        + "var out = [companyLogoHTML()];\n"
        "companyLogo = '/api/auth/company-logo?file=alpha.db&v=3'; out.push(companyLogoHTML());\n"
        "companyLogo = 'https://elsewhere.example/x.png'; out.push(companyLogoHTML());\n"
        "companyLogo = '/api/auth/company-logo?v=3\" onload=\"x'; out.push(companyLogoHTML());\n"
        "console.log(JSON.stringify(out));"
    )
    none, shown, foreign, quoted = json.loads(_node(script))
    assert none == "" and foreign == "" and quoted == ""
    assert shown.startswith(
        '<img id="auth-company-logo" src="/api/auth/company-logo?file=alpha.db&amp;v=3"'
        ' alt="Acme &amp; Sons" '
    )
    assert "object-fit:contain" in shown and "height:64px" in shown


def test_the_sign_in_form_takes_the_logo_from_the_status_only():
    login = _auth_function("loginViewHTML")
    assert login.index("companyLogoHTML()") < login.index('id="auth-title"')
    assert (
        'companyLogo = typeof status.company_logo_url === "string" ? status.company_logo_url : "";'
        in AUTH_JS
    )
    wire = AUTH_JS.split("function wireLogin(overlay, onSuccess) {")[1].split(
        "form.addEventListener"
    )[0]
    assert 'logo.addEventListener("error", function () { logo.remove(); });' in wire
    # the setup view is unchanged
    setup = AUTH_JS.split("function setupViewHTML(")[1].split("\n    function ")[0]
    assert "companyLogoHTML" not in setup


def test_company_files_cards_show_a_logo_only_when_the_list_gives_one():
    method = re.search(
        r"\n    _logoHTML\(c\) \{.*?\n    \},", COMPANIES_JS, re.S
    ).group(0)
    script = (
        "function escapeHtml(t) { return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
        ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); }\n"
        f"var page = {{ {method.strip().rstrip(',')} }};\n"
        "console.log(JSON.stringify([page._logoHTML({name: 'Acme'}),"
        " page._logoHTML({name: 'Acme', logo_url: '/api/auth/company-logo?file=a.db&v=2'}),"
        " page._logoHTML({name: 'Acme', logo_url: '/api/uploads/../x'})]));"
    )
    none, shown, other = json.loads(_node(script))
    assert none == "" and other == ""
    assert shown == (
        '<img class="company-card-logo" src="/api/auth/company-logo?file=a.db&amp;v=2"'
        ' alt="Acme" onerror="this.remove()">'
    )
    assert '${CompaniesPage._logoHTML(c)}<div class="card-header">' in COMPANIES_JS
