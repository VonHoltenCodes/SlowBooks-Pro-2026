"""Several companies on one Server Edition server (#358).

A bookkeeper keeping a small business's books and a nonprofit's needed two
servers, on two ports with two sets of sign-ins, or the host PC repointed
for everybody at once. Now the sign-in screen asks which company, a session
works in the company it chose, two people work in two companies at once,
and a sign-in whose password opens another company switches to it with a
click, with the role that company gives. Each company stays one file: its
users, roles, audit log and backups are its own.

These tests serve real company files from a temporary data folder through
the app's own get_db and SessionLocal, not the suite's shared transaction.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.database import Base, CompanySession
from app.main import app
from app.services import auth as auth_service
from app.services import served_companies

A_PW = "alpha-password-1"
B_PW = "bravo-password-1"


def _make_company(path, name, users):
    """A company file built by create_all, with its name, the operator
    password and its users: [(username, password, role, active)]."""
    from app.models.users import User
    from app.services.auth import hash_password, set_password
    from app.services.settings_service import set_setting

    engine = db_module.create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as db:
        set_setting(db, "company_name", name)
        set_password(db, users[0][1])
        for username, password, role, active in users:
            db.add(
                User(
                    username=username,
                    display_name=username.title(),
                    password_hash=hash_password(password),
                    role=role,
                    is_active=active,
                )
            )
        db.commit()
    engine.dispose()


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A Server Edition host with two companies, started for alpha.db."""
    folder = tmp_path / "companies"
    folder.mkdir()
    _make_company(
        folder / "alpha.db",
        "Alpha Hardware",
        [
            ("pat", A_PW, "admin", True),
            ("lee", A_PW, "bookkeeper", True),
            ("kim", A_PW, "bookkeeper", True),
        ],
    )
    _make_company(
        folder / "bravo.db",
        "Bravo Food Pantry",
        [
            ("pat", A_PW, "bookkeeper", True),
            ("lee", B_PW, "admin", True),
            ("sam", B_PW, "readonly", True),
        ],
    )
    (tmp_path / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Alpha Hardware", "file": "alpha.db"},
                    {"name": "Bravo Food Pantry", "file": "bravo.db"},
                ],
                "last_opened": "alpha.db",
            }
        ),
        encoding="utf-8",
    )
    url = "sqlite:///" + (folder / "alpha.db").as_posix()
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    for module in ("app.database", "app.services.served_companies"):
        monkeypatch.setattr(f"{module}.DATABASE_URL", url)
    from app.services import backup_service, company_service

    monkeypatch.setattr(company_service, "DATABASE_URL", url)
    monkeypatch.setattr(backup_service, "DATABASE_URL", url)
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()

    from app.services.audit import register_audit_hooks

    engine = db_module.create_engine(url)
    db_module.enable_sqlite_tuning(engine)
    factory = sessionmaker(
        class_=CompanySession, autocommit=False, autoflush=False, bind=engine
    )
    register_audit_hooks(factory)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    app.dependency_overrides.clear()
    auth_service.forget_company_ids()
    try:
        yield tmp_path
    finally:
        db_module.forget_company_engines()
        engine.dispose()
        auth_service.forget_company_ids()
        served_companies._manifest_cache.update(stamp=None, rows=[])


def _sign_in(client, username, password, company=None):
    if company:
        r = client.post("/api/auth/company", json={"file": company})
        assert r.status_code == 200, r.text
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def _company_name(client):
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text
    return r.json()["company_name"]


def test_the_sign_in_screen_names_the_companies(served):
    status = TestClient(app).get("/api/auth/status").json()
    assert [(c["file"], c["name"], c["current"]) for c in status["companies"]] == [
        ("alpha.db", "Alpha Hardware", True),
        ("bravo.db", "Bravo Food Pantry", False),
    ]
    assert not any(c["opens"] for c in status["companies"])
    assert status["company_name"] == "Alpha Hardware"


def test_choosing_a_company_before_signing_in_lists_its_people(served):
    client = TestClient(app)
    assert client.post("/api/auth/company", json={"file": "bravo.db"}).json() == {
        "status": "ok",
        "authenticated": False,
        "username": "",
    }
    status = client.get("/api/auth/status").json()
    assert status["company_name"] == "Bravo Food Pantry"
    assert status["usernames"] == ["lee", "pat", "sam"]
    assert [c["current"] for c in status["companies"]] == [False, True]


def test_signing_in_to_the_chosen_company(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", B_PW, company="bravo.db").status_code == 200
    assert _company_name(client) == "Bravo Food Pantry"
    assert client.get("/api/auth/status").json()["user"]["role"] == "admin"


def test_a_password_that_opens_both_switches_with_a_click(served):
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    status = client.get("/api/auth/status").json()
    assert [c["opens"] for c in status["companies"]] == [True, True]
    assert status["user"]["role"] == "admin"

    r = client.post("/api/auth/company", json={"file": "bravo.db"})
    assert r.json()["authenticated"] is True
    assert _company_name(client) == "Bravo Food Pantry"
    # the role is the company's own: pat keeps Bravo's books, no more
    assert client.get("/api/auth/status").json()["user"]["role"] == "bookkeeper"
    assert client.get("/api/users").status_code == 403

    r = client.post("/api/auth/company", json={"file": "alpha.db"})
    assert r.json()["authenticated"] is True
    assert _company_name(client) == "Alpha Hardware"
    assert client.get("/api/users").status_code == 200


def test_another_password_is_asked_for(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", A_PW).status_code == 200
    status = client.get("/api/auth/status").json()
    assert [c["opens"] for c in status["companies"]] == [True, False]
    r = client.post("/api/auth/company", json={"file": "bravo.db"})
    assert r.json() == {"status": "ok", "authenticated": False, "username": "lee"}
    assert client.get("/api/settings").status_code == 401
    assert _sign_in(client, "lee", B_PW).status_code == 200
    assert _company_name(client) == "Bravo Food Pantry"


def test_a_company_without_the_person_is_not_opened(served):
    client = TestClient(app)
    assert _sign_in(client, "kim", A_PW).status_code == 200
    r = client.post("/api/auth/company", json={"file": "bravo.db"})
    assert r.json()["authenticated"] is False
    assert _sign_in(client, "kim", A_PW).status_code == 401


def test_a_sign_in_ended_in_the_other_company_is_not_switched_to(served):
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    # Bravo's administrator deactivates pat after pat signed in to Alpha
    from app.models.users import User

    with db_module.use_company("bravo.db"):
        with db_module.SessionLocal() as db:
            db.query(User).filter(User.username == "pat").update({"is_active": False})
            db.commit()
    r = client.post("/api/auth/company", json={"file": "bravo.db"})
    assert r.json()["authenticated"] is False
    assert client.get("/api/settings").status_code == 401


def test_two_people_in_two_companies_at_once(served):
    alpha, bravo = TestClient(app), TestClient(app)
    assert _sign_in(alpha, "pat", A_PW).status_code == 200
    assert _sign_in(bravo, "sam", B_PW, company="bravo.db").status_code == 200
    r = alpha.post("/api/customers", json={"name": "Alpha's Customer"})
    assert r.status_code in (200, 201), r.text
    assert [c["name"] for c in alpha.get("/api/customers").json()] == [
        "Alpha's Customer"
    ]
    assert bravo.get("/api/customers").json() == []
    assert _company_name(alpha) == "Alpha Hardware"
    assert _company_name(bravo) == "Bravo Food Pantry"
    # a read-only sign-in in Bravo stays read-only there
    assert bravo.post("/api/customers", json={"name": "x"}).status_code == 403


def test_the_audit_log_is_the_company_s_own(served):
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    client.post("/api/auth/company", json={"file": "bravo.db"})
    assert client.post("/api/customers", json={"name": "Pantry Donor"}).is_success
    from app.models.audit import AuditLog

    for company, expected in (("bravo.db", ["pat"]), ("alpha.db", [])):
        with db_module.use_company(company):
            with db_module.SessionLocal() as db:
                who = [
                    row.username
                    for row in db.query(AuditLog)
                    .filter(AuditLog.table_name == "customers")
                    .all()
                ]
        assert who == expected, company


def test_backups_are_the_company_s_own(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", B_PW, company="bravo.db").status_code == 200
    r = client.post("/api/backups", json={"notes": "before the drive"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["filename"].startswith("bravo_")
    listed = [b["filename"] for b in client.get("/api/backups").json()]
    assert listed and all(name.startswith("bravo_") for name in listed)
    alpha = TestClient(app)
    assert _sign_in(alpha, "pat", A_PW).status_code == 200
    assert not any(
        b["filename"].startswith("bravo_") for b in alpha.get("/api/backups").json()
    )


def test_signing_out_stays_in_the_company(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", B_PW, company="bravo.db").status_code == 200
    client.post("/api/auth/logout")
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is False
    assert status["company_name"] == "Bravo Food Pantry"


def test_a_company_no_longer_listed_is_not_reached(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", B_PW, company="bravo.db").status_code == 200
    manifest = served / "companies.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["companies"] = [c for c in data["companies"] if c["file"] != "bravo.db"] + [
        {"name": "Charlie", "file": "charlie.db"}
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    # the session falls back to the company the server was started for,
    # which is another company: signed out, never Bravo's books in Alpha
    r = client.get("/api/settings")
    assert r.status_code == 401
    assert client.post("/api/auth/company", json={"file": "bravo.db"}).status_code == (
        404
    )


def test_a_missing_file_says_so(served):
    client = TestClient(app)
    (served / "companies" / "bravo.db").unlink()
    assert client.post("/api/auth/company", json={"file": "bravo.db"}).is_success
    # the sign-in screen keeps its choice: the sentence, and the companies
    # this server serves (the 2.25.0 review: a 503 with no body lost them)
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert "missing" in r.json()["company_unavailable"]
    assert [c["name"] for c in r.json()["companies"]] == [
        "Alpha Hardware",
        "Bravo Food Pantry",
    ]


def test_an_api_token_names_its_company(served):
    client = TestClient(app)
    assert _sign_in(client, "lee", B_PW, company="bravo.db").status_code == 200
    r = client.post("/api/tokens", json={"label": "pantry agent", "role": "readonly"})
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    agent = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    # without the header the token is looked up in Alpha, which never made it
    assert agent.get("/api/settings", headers=headers).status_code == 401
    headers["X-SlowBooks-Company"] = "bravo.db"
    r = agent.get("/api/settings", headers=headers)
    assert r.status_code == 200
    assert r.json()["company_name"] == "Bravo Food Pantry"


def test_one_company_on_the_server_offers_no_choice(served, monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE")
    client = TestClient(app)
    assert "companies" not in client.get("/api/auth/status").json()
    r = client.post("/api/auth/company", json={"file": "bravo.db"})
    assert r.status_code == 404
    # a cookie naming another company does not reach it on a desktop
    assert _sign_in(client, "pat", A_PW).status_code == 200
    assert _company_name(client) == "Alpha Hardware"


def _new_company(served, name, filename):
    """A company made under Companies: in the list, no password yet."""
    from sqlalchemy import create_engine

    path = served / "companies" / filename
    engine = create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    manifest = served / "companies.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["companies"].append({"name": name, "file": filename})
    manifest.write_text(json.dumps(data), encoding="utf-8")


def test_a_new_company_is_not_set_up_by_whoever_picks_it(served):
    _new_company(served, "Charlie Cafe", "charlie.db")
    stranger = TestClient(app)
    assert stranger.post("/api/auth/company", json={"file": "charlie.db"}).is_success
    assert stranger.get("/api/auth/status").json()["setup_needed"] is True
    r = stranger.post("/api/auth/setup", json={"password": "stranger-password"})
    assert r.status_code == 403
    assert "administrator sets it up" in r.json()["detail"]


def test_an_administrator_sets_a_new_company_up(served):
    _new_company(served, "Charlie Cafe", "charlie.db")
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    r = client.post("/api/auth/company", json={"file": "charlie.db"})
    assert r.json() == {"status": "ok", "authenticated": False, "setup": True}
    r = client.post(
        "/api/auth/setup",
        json={"password": "charlie-password", "company_name": "Charlie Cafe"},
    )
    assert r.status_code == 200, r.text
    assert _company_name(client) == "Charlie Cafe"
    # the setup's password is Alpha's admin's too, under the same username
    # ("admin", the operator setup makes): the way back is a click
    status = client.get("/api/auth/status").json()
    assert [c["opens"] for c in status["companies"]] == [False, False, True]
    # a bookkeeper in Alpha cannot open it to set it up
    other = TestClient(app)
    assert _sign_in(other, "lee", A_PW).status_code == 200
    assert (
        other.post("/api/auth/company", json={"file": "charlie.db"}).json()[
            "authenticated"
        ]
        is False
    )


def test_serve_lan_brings_every_company_up_to_date(tmp_path, monkeypatch):
    """The launcher migrates each company the server will serve before it
    starts, not only the one it starts for; one that fails is named and
    skipped, and the others still start."""
    import desktop_launcher

    folder = tmp_path / "companies"
    folder.mkdir()
    for name in ("alpha.db", "bravo.db", "charlie.db"):
        (folder / name).write_bytes(b"")
    (tmp_path / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Alpha", "file": "alpha.db"},
                    {"name": "Bravo", "file": "bravo.db"},
                    {"name": "Charlie", "file": "charlie.db"},
                ],
                "last_opened": "alpha.db",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    migrated = []

    def fake_migrate(url, output=None, key=None):
        if url.endswith("charlie.db"):
            raise RuntimeError("a migration failed")
        migrated.append(url.rsplit("/", 1)[1])

    monkeypatch.setattr(desktop_launcher, "migrate", fake_migrate)
    assert desktop_launcher._migrate_other_companies("alpha.db") == ["charlie.db"]
    assert migrated == ["bravo.db"]


def test_every_served_company_gets_the_started_companys_pool(served, monkeypatch):
    """#966 (2.32.0 round 11, W-169): the second company's engine carried
    SQLAlchemy's default 5 + 10 pool while the started company's had the
    thread pool's 40, so sixty requests at once to it failed with 500s and
    SLOWBOOKS_DB_POOL_SIZE never reached it."""
    monkeypatch.delenv("SLOWBOOKS_DB_POOL_SIZE", raising=False)
    engine = served_companies.open_engine("bravo.db")
    try:
        assert engine.pool.size() == 40
        assert engine.pool._max_overflow == 10
    finally:
        engine.dispose()
    monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", "80")
    engine = served_companies.open_engine("bravo.db")
    try:
        assert engine.pool.size() == 80
    finally:
        engine.dispose()
    monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", "5")
    engine = served_companies.open_engine("bravo.db")
    try:
        assert engine.pool.size() == 40  # never below the thread pool
    finally:
        engine.dispose()
