import re
import unittest
from pathlib import Path


class RouteDependencyImportCoverageTests(unittest.TestCase):
    def test_all_route_service_imports_point_to_existing_modules(self):
        root = Path(__file__).resolve().parent.parent
        route_files = list((root / "app" / "routes").glob("*.py"))
        pattern = re.compile(r"from app\.services\.([A-Za-z0-9_]+) import ")

        missing = []
        for route_file in route_files:
            matches = pattern.findall(route_file.read_text())
            for module_name in matches:
                target = root / "app" / "services" / f"{module_name}.py"
                if not target.exists():
                    missing.append(f"{route_file.relative_to(root)} -> app/services/{module_name}.py")

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
