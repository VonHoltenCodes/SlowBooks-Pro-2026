import os
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app.database import Base


class CompanyServiceSqlHardeningTests(unittest.TestCase):
    def setUp(self):
        from app.models.companies import Company  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_create_company_rejects_invalid_database_name_before_sql(self):
        from app.services import company_service

        with self.Session() as db, \
             mock.patch.object(company_service, 'create_engine') as create_engine_mock, \
             mock.patch.object(company_service, 'run_database_bootstrap') as bootstrap_mock:
            result = company_service.create_company(
                db,
                name='Auckland Books',
                database_name='auckland_books"; DROP DATABASE postgres; --',
                description='NZ demo company',
            )

        self.assertFalse(result['success'])
        self.assertIn('invalid database name', result['error'].lower())
        create_engine_mock.assert_not_called()
        bootstrap_mock.assert_not_called()

    def test_drop_database_rejects_invalid_database_name_before_sql(self):
        from app.services import company_service

        with mock.patch.object(company_service, 'create_engine') as create_engine_mock:
            with self.assertRaises(ValueError):
                company_service._drop_database('bad-db-name')

        create_engine_mock.assert_not_called()

    def test_get_company_db_url_rejects_invalid_database_name(self):
        from app.services import company_service

        with self.assertRaises(ValueError):
            company_service.get_company_db_url('../other_db')


if __name__ == '__main__':
    unittest.main()
