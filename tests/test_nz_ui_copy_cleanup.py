import unittest
from pathlib import Path


class NzUiCopyCleanupTests(unittest.TestCase):
    def test_readme_avoids_stale_us_product_copy(self):
        readme = Path("README.md").read_text()

        self.assertNotIn("IRS Pub 583 Mock Data", readme)
        self.assertNotIn("Schedule C data + CSV export", readme)
        self.assertNotIn("Account-to-tax-line mappings for Schedule C", readme)
        self.assertNotIn("Sales Tax Payable", readme)


if __name__ == "__main__":
    unittest.main()
