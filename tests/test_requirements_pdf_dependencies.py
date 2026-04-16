import unittest
from pathlib import Path


class RequirementsPdfDependenciesTests(unittest.TestCase):
    def test_requirements_include_gst_pdf_dependencies(self):
        requirements = Path('requirements.txt').read_text().splitlines()
        self.assertIn('pypdf==6.10.0', requirements)
        self.assertIn('reportlab==4.4.10', requirements)


if __name__ == '__main__':
    unittest.main()
