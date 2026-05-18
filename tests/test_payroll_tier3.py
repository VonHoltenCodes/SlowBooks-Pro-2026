"""Tests for the Tier 3 HR layer.

Covers new-hire onboarding checklists, the state new-hire report, employee
roles, the per-employee document vault, and the token-accessed self-service
portal.
"""


def _create_employee(client, **overrides):
    body = {
        "first_name": "Jordan",
        "last_name": "Hire",
        "pay_type": "hourly",
        "pay_rate": 30,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
        "hire_date": "2026-05-01",
    }
    body.update(overrides)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _portal_token(client, emp_id):
    r = client.get(f"/api/employees/{emp_id}/portal-token")
    assert r.status_code == 200, r.text
    return r.json()["portal_token"]


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------
def test_new_hire_gets_onboarding_checklist(client):
    emp = _create_employee(client)
    r = client.get(f"/api/onboarding/{emp['id']}")
    assert r.status_code == 200, r.text
    checklist = r.json()
    # The default checklist has 8 tasks, all pending on day one.
    assert checklist["total"] == 8
    assert checklist["complete"] == 0
    types = {t["task_type"] for t in checklist["tasks"]}
    assert {"w4", "i9_section1", "everify", "state_new_hire_report"} <= types


def test_completing_onboarding_task_advances_progress(client):
    emp = _create_employee(client)
    checklist = client.get(f"/api/onboarding/{emp['id']}").json()
    task_id = checklist["tasks"][0]["id"]

    done = client.post(
        f"/api/onboarding/tasks/{task_id}/complete", params={"completed_by": "hr_admin"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "complete"
    assert done.json()["completed_at"] is not None

    after = client.get(f"/api/onboarding/{emp['id']}").json()
    assert after["complete"] == 1
    assert after["percent_complete"] > 0


def test_onboarding_task_esign(client):
    emp = _create_employee(client)
    checklist = client.get(f"/api/onboarding/{emp['id']}").json()
    w4 = next(t for t in checklist["tasks"] if t["task_type"] == "w4")
    r = client.put(f"/api/onboarding/tasks/{w4['id']}", json={"signed": True})
    assert r.status_code == 200, r.text
    assert r.json()["signed"] is True
    assert r.json()["signed_at"] is not None


# ---------------------------------------------------------------------------
# State new-hire report
# ---------------------------------------------------------------------------
def test_new_hire_report_data(client):
    emp = _create_employee(client, hire_date="2026-05-10")
    r = client.get(f"/api/onboarding/{emp['id']}/new-hire-report")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["employee_id"] == emp["id"]
    assert report["hire_date"] == "2026-05-10"
    # Deadline is 20 days after hire.
    assert report["report_deadline"] == "2026-05-30"


def test_new_hire_report_flags_overdue(client):
    emp = _create_employee(client, hire_date="2026-01-01")
    report = client.get(f"/api/onboarding/{emp['id']}/new-hire-report").json()
    assert report["overdue"] is True


def test_new_hire_report_pdf(client):
    emp = _create_employee(client)
    r = client.get(f"/api/onboarding/{emp['id']}/new-hire-report/pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def test_employee_roles_and_manager(client):
    boss = _create_employee(client, first_name="Casey", role="manager")
    assert boss["role"] == "manager"
    report = _create_employee(
        client, first_name="Dana", role="employee", manager_id=boss["id"]
    )
    assert report["role"] == "employee"
    assert report["manager_id"] == boss["id"]


# ---------------------------------------------------------------------------
# Document vault
# ---------------------------------------------------------------------------
def test_employee_document_upload_list_download(client):
    emp = _create_employee(client)
    up = client.post(
        f"/api/employees/{emp['id']}/documents",
        files={"file": ("offer.pdf", b"%PDF-1.4 offer letter", "application/pdf")},
        data={"doc_category": "offer_letter"},
    )
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["doc_category"] == "offer_letter"
    assert doc["filename"] == "offer.pdf"

    listing = client.get(f"/api/employees/{emp['id']}/documents").json()
    assert len(listing) == 1

    dl = client.get(f"/api/employees/{emp['id']}/documents/{doc['id']}")
    assert dl.status_code == 200
    assert dl.content == b"%PDF-1.4 offer letter"


def test_document_upload_rejects_disallowed_type(client):
    emp = _create_employee(client)
    r = client.post(
        f"/api/employees/{emp['id']}/documents",
        files={"file": ("evil.exe", b"MZ", "application/x-msdownload")},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Self-service portal
# ---------------------------------------------------------------------------
def test_portal_requires_valid_token(client):
    emp = _create_employee(client)
    token = _portal_token(client, emp["id"])
    assert client.get(f"/portal/{token}").status_code == 200
    assert client.get("/portal/not-a-real-token").status_code == 404


def test_portal_token_can_be_rotated(client):
    emp = _create_employee(client)
    first = _portal_token(client, emp["id"])
    rotated = client.post(f"/api/employees/{emp['id']}/portal-token").json()[
        "portal_token"
    ]
    assert rotated != first
    assert client.get(f"/portal/{first}").status_code == 404  # old link dead
    assert client.get(f"/portal/{rotated}").status_code == 200


def test_portal_profile_update_persists_w4(client):
    emp = _create_employee(client)
    token = _portal_token(client, emp["id"])
    r = client.post(
        f"/portal/{token}/profile",
        data={
            "filing_status": "married",
            "multiple_jobs": "true",
            "dependents_amount": "2000",
            "other_income_annual": "0",
            "deductions_annual": "0",
            "extra_withholding": "25",
            "address1": "1 New St",
            "city": "Seattle",
            "state": "WA",
            "zip": "98101",
        },
    )
    assert r.status_code == 200, r.text  # 303 redirect, followed to the GET
    updated = client.get(f"/api/employees/{emp['id']}").json()
    assert updated["filing_status"] == "married"
    assert updated["multiple_jobs"] is True
    assert updated["extra_withholding"] == 25


def test_portal_pto_request_submission(client):
    emp = _create_employee(client)
    token = _portal_token(client, emp["id"])
    r = client.post(
        f"/portal/{token}/pto",
        data={
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "hours": "24",
            "pto_type": "vacation",
            "notes": "summer trip",
        },
    )
    assert r.status_code == 200, r.text
    requests = client.get(f"/api/pto/requests?employee_id={emp['id']}").json()
    assert len(requests) == 1
    assert requests[0]["status"] == "pending"
    assert requests[0]["hours"] == 24


def test_portal_bank_account_add(client, db_session):
    emp = _create_employee(client)
    token = _portal_token(client, emp["id"])
    r = client.post(
        f"/portal/{token}/bank",
        data={
            "nickname": "Main",
            "account_kind": "checking",
            "routing_number": "123456789",
            "account_number": "5550009999",
            "deposit_type": "full",
        },
    )
    assert r.status_code == 200, r.text
    accounts = client.get(f"/api/employees/{emp['id']}/bank-accounts").json()
    assert len(accounts) == 1
    assert accounts[0]["account_last_four"] == "9999"
