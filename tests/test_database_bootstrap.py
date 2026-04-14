import sys
import unittest
from pathlib import Path
from unittest import mock


class DatabaseBootstrapTests(unittest.TestCase):
    def test_run_bootstrap_runs_alembic_then_seed_with_target_database_url(self):
        import scripts.bootstrap_database as bootstrap_database

        target_url = "postgresql://bookkeeper:bookkeeper@db:5432/new_company?sslmode=disable"

        with mock.patch.object(bootstrap_database, "resolve_alembic_executable", return_value="/venv/bin/alembic"), \
             mock.patch.object(bootstrap_database, "REPO_ROOT", Path("/repo")), \
             mock.patch.object(bootstrap_database.subprocess, "run") as run_mock:
            bootstrap_database.run_bootstrap(target_url)

        self.assertEqual(run_mock.call_count, 2)

        alembic_args, alembic_kwargs = run_mock.call_args_list[0]
        seed_args, seed_kwargs = run_mock.call_args_list[1]

        self.assertEqual(alembic_args[0], ["/venv/bin/alembic", "upgrade", "head"])
        self.assertEqual(seed_args[0], [sys.executable, "scripts/seed_database.py"])

        self.assertEqual(alembic_kwargs["cwd"], Path("/repo"))
        self.assertEqual(seed_kwargs["cwd"], Path("/repo"))
        self.assertTrue(alembic_kwargs["check"])
        self.assertTrue(seed_kwargs["check"])
        self.assertEqual(alembic_kwargs["env"]["DATABASE_URL"], target_url)
        self.assertEqual(seed_kwargs["env"]["DATABASE_URL"], target_url)
        self.assertEqual(alembic_kwargs["env"]["DATABASE_URL"], seed_kwargs["env"]["DATABASE_URL"])


if __name__ == "__main__":
    unittest.main()
