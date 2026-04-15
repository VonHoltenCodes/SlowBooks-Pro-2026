import importlib
import os
import unittest
from pathlib import Path


class DockerConfigTests(unittest.TestCase):
    def test_database_url_takes_precedence_over_postgres_parts(self):
        import app.config as config

        original = os.environ.copy()
        try:
            os.environ["DATABASE_URL"] = "postgresql://alice:secret@db.example.com:5433/customdb"
            os.environ["POSTGRES_HOST"] = "ignored-host"
            os.environ["POSTGRES_PORT"] = "9999"
            os.environ["POSTGRES_DB"] = "ignored-db"
            os.environ["POSTGRES_USER"] = "ignored-user"
            os.environ["POSTGRES_PASSWORD"] = "ignored-pass"
            os.environ["POSTGRES_SSLMODE"] = "require"

            config = importlib.reload(config)

            self.assertEqual(config.DATABASE_URL, "postgresql://alice:secret@db.example.com:5433/customdb")
        finally:
            os.environ.clear()
            os.environ.update(original)
            importlib.reload(config)

    def test_database_url_builds_from_postgres_parts_when_blank(self):
        import app.config as config

        original = os.environ.copy()
        try:
            os.environ["DATABASE_URL"] = ""
            os.environ["POSTGRES_HOST"] = "postgres"
            os.environ["POSTGRES_PORT"] = "5432"
            os.environ["POSTGRES_DB"] = "bookkeeper"
            os.environ["POSTGRES_USER"] = "bookkeeper"
            os.environ["POSTGRES_PASSWORD"] = "bookkeeper"
            os.environ["POSTGRES_SSLMODE"] = "disable"

            config = importlib.reload(config)

            self.assertEqual(
                config.DATABASE_URL,
                "postgresql://bookkeeper:bookkeeper@postgres:5432/bookkeeper?sslmode=disable",
            )
        finally:
            os.environ.clear()
            os.environ.update(original)
            importlib.reload(config)

    def test_docker_assets_and_env_keys_exist(self):
        root = Path(__file__).resolve().parent.parent
        env_example = (root / ".env.example").read_text()
        dockerfile_text = (root / "Dockerfile").read_text()

        self.assertTrue((root / "Dockerfile").exists())
        self.assertTrue((root / "docker-compose.yml").exists())
        self.assertTrue((root / ".dockerignore").exists())
        self.assertTrue((root / "scripts" / "docker-entrypoint.sh").exists())
        self.assertTrue((root / "scripts" / "docker" / "docker-entrypoint.sh").exists())
        self.assertIn('CMD ["/bin/sh", "/app/scripts/docker-entrypoint.sh"]', dockerfile_text)
        compose_text = (root / "docker-compose.yml").read_text()
        self.assertIn("image: postgres:18", compose_text)

        for key in (
            "DATABASE_URL=",
            "POSTGRES_HOST=",
            "POSTGRES_PORT=",
            "POSTGRES_DB=",
            "POSTGRES_USER=",
            "POSTGRES_PASSWORD=",
            "POSTGRES_SSLMODE=",
        ):
            self.assertIn(key, env_example)


if __name__ == "__main__":
    unittest.main()
