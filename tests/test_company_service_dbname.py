"""Company database creation: name validation and DDL quoting.

The CREATE DATABASE statement cannot use bound parameters, so the service
relies on two layers: a strict allowlist regex (primary defence) and
dialect-level identifier quoting (defence-in-depth). These tests lock both.
"""

from unittest.mock import MagicMock, patch

from app.services.company_service import _VALID_DB_NAME, create_company

HOSTILE_NAMES = [
    'evil"; DROP DATABASE postgres; --',
    "evil'; DROP DATABASE postgres; --",
    "evil name",
    "evil;name",
    "1starts_with_digit",
    "",
    "a" * 64,  # one over the 63-char limit
]


def test_valid_db_name_rejects_hostile_input():
    for name in HOSTILE_NAMES:
        assert not _VALID_DB_NAME.match(name), name


def test_valid_db_name_accepts_normal_names():
    for name in ["acme", "acme_books", "Acme-2026", "a"]:
        assert _VALID_DB_NAME.match(name), name


def test_create_company_rejects_invalid_name_before_any_sql():
    db = MagicMock()
    with patch("app.services.company_service._is_sqlite", return_value=False), patch(
        "app.services.company_service.create_engine"
    ) as engine_factory:
        result = create_company(db, "Evil", 'evil"; DROP DATABASE postgres; --')
    assert result["success"] is False
    engine_factory.assert_not_called()


def test_create_company_quotes_db_name_via_dialect():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    conn = MagicMock()
    conn.dialect.identifier_preparer.quote.return_value = '"acme_books"'
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    with patch("app.services.company_service._is_sqlite", return_value=False), patch(
        "app.services.company_service.create_engine", return_value=engine
    ), patch(
        "app.services.company_service._base_url", return_value="postgresql://x/"
    ), patch(
        "app.services.company_service._init_company_db"
    ) as init_db:
        result = create_company(db, "Acme", "acme_books")

    assert result["success"] is True
    conn.dialect.identifier_preparer.quote.assert_called_once_with("acme_books")
    conn.exec_driver_sql.assert_called_once_with('CREATE DATABASE "acme_books"')
    # The new database must be migrated + seeded (alembic head + CoA),
    # not just create_all'd — a bare schema leaves zero accounts and no
    # alembic stamp. It also carries the name it was created with, which
    # first-run setup prefills.
    init_db.assert_called_once_with("postgresql://x/acme_books", company_name="Acme")


def test_create_company_seeds_new_database():
    """Postgres path runs the same migrate+seed init as the SQLite path."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("app.services.company_service._is_sqlite", return_value=False), patch(
        "app.services.company_service.create_engine"
    ), patch(
        "app.services.company_service._base_url", return_value="postgresql://x/"
    ), patch(
        "app.services.company_service._init_company_db"
    ) as init_db:
        result = create_company(db, "Acme", "acme_books")

    assert result["success"] is True
    init_db.assert_called_once_with("postgresql://x/acme_books", company_name="Acme")
