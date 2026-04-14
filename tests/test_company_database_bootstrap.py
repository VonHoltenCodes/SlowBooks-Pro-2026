import os
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import Base


class CompanyDatabaseBootstrapTests(unittest.TestCase):
    def setUp(self):
        from app.models.companies import Company  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_create_company_bootstraps_new_database_via_migrations_and_seed(self):
        from app.services import company_service

        system_engine = mock.MagicMock()
        system_conn = system_engine.connect.return_value.__enter__.return_value

        with self.Session() as db, \
             mock.patch.object(company_service, "create_engine", return_value=system_engine) as create_engine_mock, \
             mock.patch.object(company_service, "run_database_bootstrap") as bootstrap_mock:
            result = company_service.create_company(
                db,
                name="Auckland Books",
                database_name="auckland_books",
                description="NZ demo company",
            )

            self.assertTrue(result["success"])
            bootstrap_mock.assert_called_once_with(
                company_service._database_url("auckland_books")
            )

            company = db.query(company_service.Company).filter_by(database_name="auckland_books").one()
            self.assertEqual(company.name, "Auckland Books")
            self.assertEqual(company.description, "NZ demo company")

        create_engine_mock.assert_called_once_with(
            company_service._database_url("postgres"),
            isolation_level="AUTOCOMMIT",
        )
        system_conn.execute.assert_called_once()
        system_engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
