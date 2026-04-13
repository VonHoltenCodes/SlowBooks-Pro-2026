import unittest

from fastapi import HTTPException
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"


class ScheduleCDisabledTests(unittest.TestCase):
    def test_schedule_c_json_endpoint_is_disabled_for_nz(self):
        from app.routes.tax import schedule_c_report

        with self.assertRaises(HTTPException) as ctx:
            schedule_c_report()

        self.assertEqual(ctx.exception.status_code, 410)
        self.assertIn("SlowBooks NZ", ctx.exception.detail)
        self.assertIn("Schedule C", ctx.exception.detail)

    def test_schedule_c_csv_endpoint_is_disabled_for_nz(self):
        from app.routes.tax import schedule_c_csv

        with self.assertRaises(HTTPException) as ctx:
            schedule_c_csv()

        self.assertEqual(ctx.exception.status_code, 410)
        self.assertIn("NZ income-tax output", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
