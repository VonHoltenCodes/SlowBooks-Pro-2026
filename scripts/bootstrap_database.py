"""Canonical database bootstrap entry point for migrations + NZ seed data."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import resolve_database_url

REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_alembic_executable() -> str:
    """Resolve the Alembic console script from the active Python environment or PATH."""
    python_path = Path(sys.executable)
    candidates = [python_path.with_name("alembic")]
    if os.name == "nt":
        candidates.append(python_path.with_name("alembic.exe"))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    path_hit = shutil.which("alembic")
    if path_hit:
        return path_hit

    raise RuntimeError(
        "Alembic CLI was not found. Install requirements and ensure the active environment provides the 'alembic' console script."
    )


def run_bootstrap(database_url: str | None = None) -> None:
    target_url = database_url or resolve_database_url()
    env = os.environ.copy()
    env["DATABASE_URL"] = target_url

    commands = [
        [resolve_alembic_executable(), "upgrade", "head"],
        [sys.executable, "scripts/seed_database.py"],
    ]
    for command in commands:
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


if __name__ == "__main__":
    run_bootstrap()
