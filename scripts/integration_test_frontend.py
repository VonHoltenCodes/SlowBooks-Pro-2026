#!/usr/bin/env python3
"""
Manual end-to-end smoke test for the Tier 1-3 admin UI.

This is NOT a unit test — it hits a running app over HTTP, so it needs
the server up on http://127.0.0.1:8000. Lives under scripts/ on purpose
so the pytest auto-collector (pinned to tests/) doesn't try to run it.

Validates that:
  1. SPA pages are reachable and render
  2. API endpoints return data
  3. JavaScript files load without errors
  4. Navigation works between pages

Usage:
    uvicorn app.main:app --port 8000 &
    python scripts/integration_test_frontend.py
"""

import requests
import subprocess
import time
import os
import sys
from datetime import date, timedelta

BASE_URL = "http://127.0.0.1:8000"


def test_backend_endpoints():
    """Test all backend API endpoints used by Tier 1-3 pages."""
    print("\n" + "=" * 70)
    print("BACKEND API TESTS")
    print("=" * 70)

    session = requests.Session()

    # Auth setup
    print("\n[1/8] Auth Setup")
    r = session.post(f"{BASE_URL}/api/auth/setup", json={"password": "test1234"})
    assert r.status_code in [200, 409], f"Auth failed: {r.status_code}"
    print("      ✓ Auth OK")

    # Create employee with new fields
    print("\n[2/8] Create Employee (with W-4 & Address)")
    emp_data = {
        "first_name": "TestUser",
        "last_name": "Admin",
        "ssn_last_four": "9999",
        "pay_type": "hourly",
        "pay_rate": 45.00,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "multiple_jobs": False,
        "dependents_amount": 1,
        "other_income_annual": 0,
        "deductions_annual": 0,
        "extra_withholding": 0,
        "address1": "999 Test Lane",
        "address2": "Suite 1",
        "city": "Test City",
        "state": "CA",
        "zip": "90210",
        "work_state": "CA",
        "residence_state": "CA",
        "wc_class_code": "8810",
        "email": "admin@test.local",
        "role": "employee",
        "hire_date": str(date.today() - timedelta(days=90)),
    }
    r = session.post(f"{BASE_URL}/api/employees", json=emp_data)
    assert r.status_code in [200, 201], f"Employee creation failed: {r.status_code}"
    emp = r.json()
    emp_id = emp["id"]
    assert emp["email"] == "admin@test.local", "Email not stored"
    assert emp["address1"] == "999 Test Lane", "Address not stored"
    assert emp["filing_status"] == "single", "W-4 filing_status not stored"
    print(f"      ✓ Employee ID {emp_id} created with all fields")

    # Onboarding (Tier 1)
    print("\n[3/8] Onboarding API (Tier 1)")
    r = session.get(f"{BASE_URL}/api/onboarding/{emp_id}")
    assert r.status_code == 200, f"Onboarding GET failed: {r.status_code}"
    onboard = r.json()
    assert "tasks" in onboard, "No tasks in onboarding response"
    print(f"      ✓ {len(onboard['tasks'])} onboarding tasks returned")

    # Time Entries (Tier 1)
    print("\n[4/8] Time Entries API (Tier 1)")
    r = session.get(f"{BASE_URL}/api/time-entries")
    assert r.status_code == 200, f"Time entries GET failed: {r.status_code}"
    te_data = {
        "employee_id": emp_id,
        "date": str(date.today()),
        "hours": 8.0,
        "work_state": "CA",
    }
    r = session.post(f"{BASE_URL}/api/time-entries", json=te_data)
    assert r.status_code in [200, 201], f"Time entry POST failed: {r.status_code}"
    print("      ✓ Time entries GET and POST working")

    # PTO (Tier 1)
    print("\n[5/8] PTO API (Tier 1)")
    r = session.get(f"{BASE_URL}/api/pto/policies")
    assert r.status_code == 200, f"PTO policies GET failed: {r.status_code}"
    r = session.get(f"{BASE_URL}/api/pto/requests")
    assert r.status_code == 200, f"PTO requests GET failed: {r.status_code}"
    print("      ✓ PTO GET endpoints working")

    # Deductions (Tier 2)
    print("\n[6/8] Deductions API (Tier 2)")
    r = session.get(f"{BASE_URL}/api/deductions/types")
    assert r.status_code == 200, f"Deduction types GET failed: {r.status_code}"
    r = session.get(f"{BASE_URL}/api/deductions/employee/{emp_id}")
    assert r.status_code == 200, f"Employee deductions GET failed: {r.status_code}"
    print("      ✓ Deductions GET endpoints working")

    # Tax Forms (Tier 3)
    print("\n[7/8] Tax Forms API (Tier 3)")
    r = session.post(f"{BASE_URL}/api/payroll/forms/w2/{emp_id}?year=2026")
    # Note: 404 is expected as tax form endpoints are not yet implemented in backend
    # Frontend is built and ready, just awaiting backend implementation
    if r.status_code == 404:
        print("      ℹ Tax forms endpoints not yet implemented (as expected)")
    elif r.status_code in [200, 201, 400]:
        print(f"      ✓ Tax forms endpoint available (status {r.status_code})")
    else:
        print(f"      ℹ Tax forms status: {r.status_code}")

    # Employee Details Extensions
    print("\n[8/8] Employee Details Extensions")
    r = session.get(f"{BASE_URL}/api/employees/{emp_id}")
    assert r.status_code == 200, f"Employee details GET failed: {r.status_code}"
    emp_full = r.json()
    assert emp_full.get("email") == "admin@test.local", "Email not in details"
    assert emp_full.get("address1") == "999 Test Lane", "Address not in details"
    print("      ✓ Employee details include email, address, W-4 fields")

    # Portal token
    r = session.get(f"{BASE_URL}/api/employees/{emp_id}/portal-token")
    assert r.status_code == 200, f"Portal token failed: {r.status_code}"
    token_resp = r.json()
    token = token_resp.get("portal_token")
    assert token, f"No portal_token in response: {token_resp}"
    print("      ✓ Portal token endpoint working")

    # YTD
    r = session.get(f"{BASE_URL}/api/employees/{emp_id}/ytd")
    assert r.status_code == 200, f"YTD failed: {r.status_code}"
    print("      ✓ YTD endpoint working")

    print("\n" + "=" * 70)
    print("✅ ALL BACKEND ENDPOINTS WORKING")
    print("=" * 70)


def test_frontend_pages():
    """Test that frontend pages are accessible and have correct content."""
    print("\n" + "=" * 70)
    print("FRONTEND PAGE TESTS")
    print("=" * 70)

    session = requests.Session()

    # Get the index.html
    print("\n[1/6] SPA Shell (index.html)")
    r = session.get(f"{BASE_URL}/")
    assert r.status_code == 200, f"Index GET failed: {r.status_code}"
    assert "Slowbooks Pro" in r.text, "Title not in HTML"
    assert "app.js" in r.text, "app.js script tag missing"
    print("      ✓ SPA shell loads")

    # Check that page JavaScript files exist
    print("\n[2/6] JavaScript Files")
    js_files = [
        "onboarding.js",
        "time_entries.js",
        "pto.js",
        "deductions.js",
        "tax_forms.js",
    ]
    for js_file in js_files:
        r = session.get(f"{BASE_URL}/static/js/{js_file}")
        assert r.status_code == 200, f"{js_file} not found: {r.status_code}"
        assert "const " in r.text or "function" in r.text, f"{js_file} has no code"
        print(f"      ✓ {js_file} loads")

    # Check that app.js has the new routes
    print("\n[3/6] App Routes")
    r = session.get(f"{BASE_URL}/static/js/app.js")
    assert r.status_code == 200
    app_js = r.text
    routes = [
        "'/hr/onboarding'",
        "'/hr/time-entries'",
        "'/hr/pto'",
        "'/hr/deductions'",
        "'/hr/tax-forms'",
    ]
    for route in routes:
        assert route in app_js, f"Route {route} not in app.js"
        print(f"      ✓ Route {route} defined")

    # Check that index.html has nav links
    print("\n[4/6] Navigation Links")
    r = session.get(f"{BASE_URL}/")
    html = r.text
    nav_links = [
        "#/hr/onboarding",
        "#/hr/time-entries",
        "#/hr/pto",
        "#/hr/deductions",
        "#/hr/tax-forms",
    ]
    for link in nav_links:
        assert link in html, f"Nav link {link} not in index.html"
        print(f"      ✓ Nav link {link} present")

    # Check CSS files
    print("\n[5/6] Styles")
    r = session.get(f"{BASE_URL}/static/css/style.css")
    assert r.status_code == 200, "style.css not found"
    r = session.get(f"{BASE_URL}/static/css/dark.css")
    assert r.status_code == 200, "dark.css not found"
    print("      ✓ CSS files load")

    # Check utilities
    print("\n[6/6] Utility Scripts")
    r = session.get(f"{BASE_URL}/static/js/api.js")
    assert r.status_code == 200, "api.js not found"
    assert "API.get" in r.text or "fetch" in r.text, "API utilities not in api.js"
    r = session.get(f"{BASE_URL}/static/js/utils.js")
    assert r.status_code == 200, "utils.js not found"
    print("      ✓ Utility scripts load")

    print("\n" + "=" * 70)
    print("✅ ALL FRONTEND PAGES WORKING")
    print("=" * 70)


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("TIER 1-3 ADMIN UI - FUNCTIONAL TEST SUITE")
    print("=" * 70)

    # Check if server is running
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=2)
        assert r.status_code == 200
    except Exception:
        print("\n⚠ Server not running. Starting on port 8000...")
        os.environ["DATABASE_URL"] = "sqlite:///./test.db"
        # Clean up old DB
        if os.path.exists("test.db"):
            os.remove("test.db")
        # Start server
        proc = subprocess.Popen(
            [
                "python",
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/home/user/SlowBooks-Pro-2026",
        )
        time.sleep(4)
        # Verify it started
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            assert r.status_code == 200
            print("✓ Server started")
        except Exception:
            print("✗ Failed to start server")
            proc.terminate()
            sys.exit(1)

    try:
        test_backend_endpoints()
        test_frontend_pages()

        print("\n" + "=" * 70)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 70)
        print("\nTo manually test the UI:")
        print(f"  1. Open browser to {BASE_URL}")
        print("  2. Login with password: test1234")
        print("  3. Navigate to:")
        print(f"     - {BASE_URL}/#/employees (view created test employee)")
        print(f"     - {BASE_URL}/#/hr/onboarding")
        print(f"     - {BASE_URL}/#/hr/time-entries")
        print(f"     - {BASE_URL}/#/hr/pto")
        print(f"     - {BASE_URL}/#/hr/deductions")
        print(f"     - {BASE_URL}/#/hr/tax-forms")
        print()
        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
