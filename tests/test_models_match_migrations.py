"""The models and the migrations describe the same schema (W-146).

The suite builds its database with ``Base.metadata.create_all``, so a column
put on a model with no migration behind it passes every test and is a 500
("no such column") on every real company file, which the migrations build.
2.32.0's first gate round found four such columns on ``sov_lines``: a job's
page, its contract, its schedule of values and every pay application failed
while CI stayed green.

Here a file is built the way a company's is, ``alembic upgrade head``, and
every table and column the models declare must be in it. Then the screens
that broke are driven against that file, not against create_all.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import close_all_sessions, sessionmaker

import app.database as db_module
import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base, get_db
from app.main import app
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

ROOT = Path(__file__).resolve().parents[1]

# Model columns the migrations are allowed not to create, as
# {table: {column: reason}}. Empty: every column a model declares has a
# migration. Anything added here needs a one-line reason.
ALLOWED_MISSING_COLUMNS: dict[str, dict[str, str]] = {}

# Model tables the migrations are allowed not to create, as {table: reason}.
ALLOWED_MISSING_TABLES: dict[str, str] = {}


def _cfg(db: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


@pytest.fixture(scope="module")
def migrated_file(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("migrated") / "head.db"
    command.upgrade(_cfg(db), "head")
    return db


def _migrated_columns(db: Path) -> dict[str, set[str]]:
    engine = sa.create_engine("sqlite:///" + db.as_posix())
    try:
        insp = sa.inspect(engine)
        return {
            t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()
        }
    finally:
        engine.dispose()


def test_every_model_table_and_column_is_made_by_the_migrations(migrated_file):
    migrated = _migrated_columns(migrated_file)
    missing_tables = []
    missing_columns = []
    for name, table in sorted(Base.metadata.tables.items()):
        if name not in migrated:
            if name not in ALLOWED_MISSING_TABLES:
                missing_tables.append(name)
            continue
        allowed = ALLOWED_MISSING_COLUMNS.get(name, {})
        for column in table.columns:
            if column.name not in migrated[name] and column.name not in allowed:
                missing_columns.append(f"{name}.{column.name}")
    problems = []
    if missing_tables:
        problems.append(
            "tables the models have and no migration creates: "
            + ", ".join(missing_tables)
        )
    if missing_columns:
        problems.append(
            "model columns no migration creates: " + ", ".join(missing_columns)
        )
    assert not problems, "; ".join(problems)


def test_the_allowlists_name_only_real_gaps(migrated_file):
    """An allowlist entry whose gap a migration has since closed goes, so
    the lists never grow stale and hide nothing."""
    migrated = _migrated_columns(migrated_file)
    stale = [t for t in ALLOWED_MISSING_TABLES if t in migrated]
    for table, columns in ALLOWED_MISSING_COLUMNS.items():
        stale += [f"{table}.{c}" for c in columns if c in migrated.get(table, set())]
    assert not stale, "allowlisted but made by the migrations: " + ", ".join(stale)


def test_the_sov_line_carries_no_exemption_certificate():
    """The certificate is the job's (#864); a schedule-of-values line only
    says whether its billing is taxed."""
    sov = Base.metadata.tables["sov_lines"]
    assert not [c.name for c in sov.columns if c.name.startswith("exempt_cert")]
    jobs = Base.metadata.tables["jobs"]
    assert {
        "exempt_certificate_number",
        "exempt_certificate_state",
        "exempt_certificate_kind",
        "exempt_certificate_expires",
    } <= set(jobs.columns.keys())


# ── A construction company on a file the migrations built ───────────────


@pytest.fixture
def migrated_client(tmp_path):
    """An authenticated client whose database is a fresh ``upgrade head``
    file (no create_all anywhere), with the chart of accounts seeded."""
    db = tmp_path / "company.db"
    command.upgrade(_cfg(db), "head")
    engine = sa.create_engine(
        "sqlite:///" + db.as_posix(), connect_args={"check_same_thread": False}
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from app.models.accounts import Account, AccountType

    with factory() as s:
        for data in CHART_OF_ACCOUNTS:
            s.add(
                Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    bank_kind=data.get("bank_kind"),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
            )
        s.commit()

    saved = (db_module.engine, db_module.SessionLocal)
    db_module.engine, db_module.SessionLocal = engine, factory

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        c = TestClient(app)
        r = c.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, r.text
        yield c
    finally:
        app.dependency_overrides.clear()
        close_all_sessions()
        db_module.engine, db_module.SessionLocal = saved
        engine.dispose()


def test_a_construction_job_its_contract_sov_and_pay_application_work_on_a_migrated_file(
    migrated_client,
):
    c = migrated_client
    r = c.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    r = c.post("/api/construction/setup")
    assert r.status_code == 200, r.text
    r = c.post("/api/customers", json={"name": "Harbor Owner"})
    assert r.status_code in (200, 201), r.text
    customer_id = r.json()["id"]

    r = c.post("/api/jobs", json={"customer_id": customer_id, "name": "Tower"})
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    r = c.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text

    r = c.put(
        f"/api/jobs/{job_id}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 100000,
            "retainage_pct": 10,
        },
    )
    assert r.status_code == 200, r.text

    r = c.put(
        f"/api/jobs/{job_id}/sov",
        json={
            "lines": [
                {"description": "Sitework", "kind": "work", "scheduled_value": 60000},
                {"description": "Structure", "kind": "work", "scheduled_value": 40000},
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = c.get(f"/api/jobs/{job_id}/contract")
    assert r.status_code == 200, r.text
    assert len(r.json()["sov"]) == 2

    r = c.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text

    r = c.post("/api/pay-applications", json={"job_id": job_id, "date": "2026-06-01"})
    assert r.status_code == 201, r.text
