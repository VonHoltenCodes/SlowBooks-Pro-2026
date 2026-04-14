import re
import unittest
from pathlib import Path


REVISION_RE = re.compile(r"revision\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]|revision\s*=\s*['\"]([^'\"]+)['\"]")
DOWN_RE = re.compile(r"down_revision\s*:\s*Union\[str, None\]\s*=\s*(None|['\"]([^'\"]+)['\"])|down_revision\s*=\s*(None|['\"]([^'\"]+)['\"])")


class AlembicMigrationIntegrityTests(unittest.TestCase):
    def test_migration_files_form_single_linear_chain(self):
        migrations = {}
        for path in sorted(Path("alembic/versions").glob("*.py")):
            text = path.read_text()
            revision_match = REVISION_RE.search(text)
            down_match = DOWN_RE.search(text)
            self.assertIsNotNone(revision_match, f"missing revision in {path}")
            self.assertIsNotNone(down_match, f"missing down_revision in {path}")
            revision = next(group for group in revision_match.groups() if group)
            down_revision = None
            for group in down_match.groups()[1:]:
                if group and group != "None":
                    down_revision = group
                    break
            migrations[revision] = {"down_revision": down_revision}

        children = {item["down_revision"] for item in migrations.values() if item["down_revision"] is not None}
        heads = sorted(revision for revision in migrations if revision not in children)
        roots = sorted(revision for revision, item in migrations.items() if item["down_revision"] is None)

        self.assertEqual(len(roots), 1, f"expected 1 root, found {roots}")
        self.assertEqual(len(heads), 1, f"expected 1 head, found {heads}")

        chain = []
        current = heads[0]
        seen = set()
        while current is not None:
            self.assertNotIn(current, seen, "migration chain contains a cycle")
            seen.add(current)
            chain.append(current)
            current = migrations[current]["down_revision"]
            if current is not None:
                self.assertIn(current, migrations, f"missing down_revision target {current}")

        self.assertEqual(len(chain), len(migrations), "migration chain does not cover every revision")


if __name__ == "__main__":
    unittest.main()
