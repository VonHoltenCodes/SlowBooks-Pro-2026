"""
Tier 3 HR Module Tests

Tests the complete Tier 3 payroll/HR system:
- Tax form generation (W-2, W-3, Form 940, Form 941)
- Employee self-service portal (token-based access)
- Portal workflows (W-4, bank accounts, PTO requests)
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.payroll import Employee
from app.models.pto import PTOPolicy, PTORequest, PTOType, PTORequestStatus, PTOAccrual
from app.models.bank_accounts import EmployeeBankAccount, BankAccountKind
from app.services.encryption import encrypt, decrypt

# --- Tier 3: Tax Forms -------------------------------------------------------


def test_w2_endpoint_returns_json(client: any, db_session: Session):
    """W-2 endpoint returns employee's tax data as JSON."""
    # Create employee
    emp = Employee(
        first_name="Alice",
        last_name="Testworker",
        ssn_last_four="1234",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Request W-2
    r = client.post(f"/api/payroll/forms/w2/{emp.id}?year=2026")
    assert r.status_code == 200
    data = r.json()

    # Verify W-2 structure
    assert "box_1" in data  # Gross wages
    assert "box_2" in data  # Federal tax
    assert "box_3" in data  # SS wages
    assert "box_4" in data  # SS tax
    assert "box_5" in data  # Medicare wages
    assert "box_6" in data  # Medicare tax
    assert "employee_name" in data
    assert "employee_ssn" in data
    assert "employer_ein" in data
    assert "tax_year" in data
    assert data["employee_name"] == "Alice Testworker"
    assert data["tax_year"] == "2026"


def test_w2_endpoint_404_for_nonexistent_employee(client: any):
    """W-2 endpoint returns 404 for nonexistent employee."""
    r = client.post("/api/payroll/forms/w2/9999?year=2026")
    assert r.status_code == 404


def test_w3_endpoint_aggregates_all_employees(client: any, db_session: Session):
    """W-3 endpoint aggregates W-2 data across all active employees."""
    # Create two employees
    emp1 = Employee(
        first_name="Alice",
        last_name="A",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    emp2 = Employee(
        first_name="Bob",
        last_name="B",
        pay_type="hourly",
        pay_rate=Decimal("30"),
        pay_frequency="biweekly",
        filing_status="married",
        is_active=True,
    )
    db_session.add_all([emp1, emp2])
    db_session.commit()

    # Request W-3
    r = client.post("/api/payroll/forms/w3/2026")
    assert r.status_code == 200
    data = r.json()

    # Verify W-3 structure
    assert "number_of_w2s" in data
    assert "employer_ein" in data
    assert "employer_name" in data
    assert "tax_year" in data
    assert data["tax_year"] == "2026"


def test_form_940_calculates_futa(client: any, db_session: Session):
    """Form 940 endpoint calculates FUTA (federal unemployment tax)."""
    emp = Employee(
        first_name="Charlie",
        last_name="C",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    r = client.post("/api/payroll/forms/940/2026")
    assert r.status_code == 200
    data = r.json()

    # Verify Form 940 structure
    assert "box_1" in data  # Wages subject to FUTA
    assert "box_2" in data  # FUTA tax
    assert "employer_ein" in data
    assert "tax_year" in data
    assert data["tax_year"] == "2026"


def test_form_941_quarterly_aggregation(client: any, db_session: Session):
    """Form 941 endpoint aggregates pay stubs for the quarter."""
    emp = Employee(
        first_name="Dave",
        last_name="D",
        pay_type="hourly",
        pay_rate=Decimal("22"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Q1 request
    r = client.post("/api/payroll/forms/941/2026/1")
    assert r.status_code == 200
    data = r.json()

    # Verify Form 941 structure
    assert "quarter" in data
    assert "year" in data
    assert "box_1" in data  # Total wages
    assert "box_2" in data  # Federal withholding
    assert "box_3" in data  # SS wages
    assert "box_4" in data  # SS tax
    assert "box_5" in data  # Medicare wages
    assert "box_6" in data  # Medicare tax
    assert data["quarter"] == "1"
    assert data["year"] == "2026"


def test_form_941_invalid_quarter(client: any):
    """Form 941 endpoint rejects invalid quarter (must be 1-4)."""
    r = client.post("/api/payroll/forms/941/2026/5")
    assert r.status_code == 400


# --- Tier 3: Tax form PDF variants ------------------------------------------


def _is_pdf(body: bytes) -> bool:
    """PDF files start with the magic '%PDF-' header byte sequence."""
    return body[:5] == b"%PDF-"


def test_w2_pdf_endpoint_returns_pdf(client: any, db_session: Session):
    """POST /api/payroll/forms/w2/{emp_id}/pdf renders a real PDF."""
    emp = Employee(
        first_name="Alice",
        last_name="Tester",
        ssn_last_four="1234",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    r = client.post(f"/api/payroll/forms/w2/{emp.id}/pdf?year=2026")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert _is_pdf(r.content)
    # PDFs over 1KB are non-trivially rendered (vs. a stub response). The
    # text content is compressed inside the PDF and not byte-greppable.
    assert len(r.content) > 1024


def test_w2_pdf_404_for_nonexistent_employee(client: any):
    r = client.post("/api/payroll/forms/w2/9999/pdf?year=2026")
    assert r.status_code == 404


def test_w3_pdf_endpoint_returns_pdf(client: any, db_session: Session):
    """POST /api/payroll/forms/w3/{year}/pdf renders the W-3 summary."""
    emp = Employee(
        first_name="Bob",
        last_name="Aggregator",
        pay_type="hourly",
        pay_rate=Decimal("30"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    r = client.post("/api/payroll/forms/w3/2026/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert _is_pdf(r.content)


def test_form_940_pdf_endpoint_returns_pdf(client: any, db_session: Session):
    """POST /api/payroll/forms/940/{year}/pdf renders the FUTA form."""
    r = client.post("/api/payroll/forms/940/2026/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert _is_pdf(r.content)


def test_form_941_pdf_endpoint_returns_pdf(client: any, db_session: Session):
    """POST /api/payroll/forms/941/{year}/{quarter}/pdf renders quarterly FICA."""
    r = client.post("/api/payroll/forms/941/2026/2/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert _is_pdf(r.content)


def test_form_941_pdf_rejects_invalid_quarter(client: any):
    r = client.post("/api/payroll/forms/941/2026/5/pdf")
    assert r.status_code == 400


# --- Tier 3: Employee Self-Service Portal -----------------------------------


def test_portal_token_generation(client: any, db_session: Session):
    """Employee portal token can be generated and regenerated."""
    emp = Employee(
        first_name="Eve",
        last_name="E",
        pay_type="hourly",
        pay_rate=Decimal("24"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Get initial token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    assert r.status_code == 200
    data = r.json()
    assert "portal_token" in data
    initial_token = data["portal_token"]

    # Regenerate token
    r = client.post(f"/api/employees/{emp.id}/portal-token")
    assert r.status_code == 200
    data = r.json()
    new_token = data["portal_token"]

    # Tokens should be different
    assert initial_token != new_token


def test_portal_dashboard_requires_valid_token(client: any, db_session: Session):
    """Portal dashboard is accessible with valid token, rejects invalid."""
    emp = Employee(
        first_name="Frank",
        last_name="F",
        pay_type="hourly",
        pay_rate=Decimal("26"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Get valid token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    token = r.json()["portal_token"]

    # Valid token should work (returns HTML)
    r = client.get(f"/portal/{token}")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")

    # Invalid token should 404
    r = client.get("/portal/invalid-token-xyz")
    assert r.status_code == 404


def test_portal_w4_update(client: any, db_session: Session):
    """Employee can update W-4 fields via portal."""
    emp = Employee(
        first_name="Grace",
        last_name="G",
        pay_type="hourly",
        pay_rate=Decimal("28"),
        pay_frequency="biweekly",
        filing_status="single",
        multiple_jobs=False,
        dependents_amount=0,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Get portal token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    token = r.json()["portal_token"]

    # Update W-4
    r = client.post(
        f"/portal/{token}/profile",
        data={
            "filing_status": "married",
            "multiple_jobs": "on",
            "dependents_amount": "2",
            "other_income_annual": "5000",
            "deductions_annual": "10000",
            "extra_withholding": "100",
            "address1": "456 Oak Ave",
            "city": "Seattle",
            "state": "WA",
            "zip": "98101",
        },
    )
    # POST should redirect to profile page
    assert r.status_code in [200, 303]

    # Verify updates persisted
    db_session.refresh(emp)
    assert emp.filing_status.value == "married"
    assert emp.multiple_jobs is True
    assert emp.dependents_amount == 2
    assert emp.address1 == "456 Oak Ave"
    assert emp.city == "Seattle"


def test_portal_bank_account_encryption(client: any, db_session: Session):
    """Bank account data is encrypted when stored via portal."""
    emp = Employee(
        first_name="Hank",
        last_name="H",
        pay_type="hourly",
        pay_rate=Decimal("30"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Get portal token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    token = r.json()["portal_token"]

    # Add bank account via portal
    r = client.post(
        f"/portal/{token}/bank",
        data={
            "nickname": "Primary Checking",
            "account_kind": "checking",
            "routing_number": "123456789",
            "account_number": "9876543210",
            "deposit_type": "full",
        },
    )
    assert r.status_code in [200, 303]

    # Verify account was added with encrypted data
    accounts = db_session.query(EmployeeBankAccount).filter_by(employee_id=emp.id).all()
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.nickname == "Primary Checking"
    assert acc.account_kind == BankAccountKind.CHECKING
    assert acc.account_last_four == "3210"  # Last 4 should be plaintext
    assert acc.routing_number_enc is not None  # Encrypted
    assert acc.account_number_enc is not None  # Encrypted


def test_portal_pto_request(client: any, db_session: Session, seed_accounts):
    """Employee can submit PTO requests via portal."""
    emp = Employee(
        first_name="Ivy",
        last_name="I",
        pay_type="hourly",
        pay_rate=Decimal("32"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Create PTO policy
    policy = PTOPolicy(
        name="Vacation",
        pto_type=PTOType.VACATION,
        accrual_rate=Decimal("1.67"),  # ~20 days/year biweekly
        max_carryover=Decimal("40"),
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()

    # Create accrual balance
    accrual = PTOAccrual(employee_id=emp.id, policy_id=policy.id, balance=Decimal("40"))
    db_session.add(accrual)
    db_session.commit()

    # Get portal token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    token = r.json()["portal_token"]

    # Submit PTO request
    start = date.today()
    end = start + timedelta(days=4)
    r = client.post(
        f"/portal/{token}/pto",
        data={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hours": "40",
            "pto_type": "vacation",
            "notes": "Summer vacation",
        },
    )
    assert r.status_code in [200, 303]

    # Verify request was created
    reqs = db_session.query(PTORequest).filter_by(employee_id=emp.id).all()
    assert len(reqs) == 1
    req = reqs[0]
    assert req.start_date == start
    assert req.end_date == end
    assert req.hours == 40
    assert req.pto_type == PTOType.VACATION
    assert req.status == PTORequestStatus.PENDING


def test_portal_invalid_bank_account_routing(client: any, db_session: Session):
    """Portal rejects bank accounts with invalid routing numbers."""
    emp = Employee(
        first_name="Jack",
        last_name="J",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Get portal token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    token = r.json()["portal_token"]

    # Try to add account with invalid routing (not 9 digits)
    r = client.post(
        f"/portal/{token}/bank",
        data={
            "account_kind": "checking",
            "routing_number": "12345",  # Should be 9 digits
            "account_number": "9876543210",
            "deposit_type": "full",
        },
    )
    # Should redirect with error
    assert r.status_code in [200, 303]

    # Account should not be added
    accounts = db_session.query(EmployeeBankAccount).filter_by(employee_id=emp.id).all()
    assert len(accounts) == 0


# --- Tier 3: Integration Tests -----------------------------------------------


def test_tier3_complete_workflow(client: any, db_session: Session, seed_accounts):
    """Complete Tier 3 workflow: create employee, generate forms, use portal."""
    # 1. Create employee
    emp = Employee(
        first_name="Kelly",
        last_name="K",
        ssn_last_four="9876",
        pay_type="hourly",
        pay_rate=Decimal("30"),
        pay_frequency="biweekly",
        filing_status="single",
        email="kelly@test.local",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # 2. Generate tax forms
    r = client.post(f"/api/payroll/forms/w2/{emp.id}?year=2026")
    assert r.status_code == 200
    w2 = r.json()
    assert w2["employee_name"] == "Kelly K"

    r = client.post("/api/payroll/forms/w3/2026")
    assert r.status_code == 200
    w3 = r.json()
    assert "number_of_w2s" in w3

    # 3. Generate portal token
    r = client.get(f"/api/employees/{emp.id}/portal-token")
    assert r.status_code == 200
    token = r.json()["portal_token"]

    # 4. Access portal dashboard
    r = client.get(f"/portal/{token}")
    assert r.status_code == 200

    # 5. Update W-4 via portal
    r = client.post(
        f"/portal/{token}/profile",
        data={
            "filing_status": "married",
            "dependents_amount": "1",
            "extra_withholding": "50",
        },
    )
    assert r.status_code in [200, 303]

    # 6. Add bank account via portal
    r = client.post(
        f"/portal/{token}/bank",
        data={
            "account_kind": "checking",
            "routing_number": "987654321",
            "account_number": "1234567890",
            "deposit_type": "full",
        },
    )
    assert r.status_code in [200, 303]

    # 7. Verify persisted data
    db_session.refresh(emp)
    assert emp.filing_status.value == "married"
    assert emp.dependents_amount == 1
    assert emp.extra_withholding == Decimal("50")

    accounts = db_session.query(EmployeeBankAccount).filter_by(employee_id=emp.id).all()
    assert len(accounts) == 1


# --- Tier 3: Security Hardening ---------------------------------------------


def test_portal_token_hard_expiry_blocks_access(client: any, db_session: Session):
    """A token past its hard expiry returns 410 Gone, not the dashboard."""
    emp = Employee(
        first_name="Expired",
        last_name="Token",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
        portal_token="expired-token-fixture",
        portal_token_last_used=datetime.now(timezone.utc),
        portal_token_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(emp)
    db_session.commit()

    r = client.get("/portal/expired-token-fixture")
    assert r.status_code == 410


def test_portal_token_idle_expiry_blocks_access(client: any, db_session: Session):
    """A token unused for >90 days returns 410, even if not past hard expiry."""
    emp = Employee(
        first_name="Idle",
        last_name="Token",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
        portal_token="idle-token-fixture",
        portal_token_last_used=datetime.now(timezone.utc) - timedelta(days=91),
        portal_token_expires_at=datetime.now(timezone.utc) + timedelta(days=300),
    )
    db_session.add(emp)
    db_session.commit()

    r = client.get("/portal/idle-token-fixture")
    assert r.status_code == 410


def test_portal_last_used_rolls_forward_on_access(client: any, db_session: Session):
    """Every authenticated portal request updates last_used to 'now'."""
    old = datetime.now(timezone.utc) - timedelta(days=30)
    emp = Employee(
        first_name="Active",
        last_name="User",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
        portal_token="active-token-fixture",
        portal_token_last_used=old,
        portal_token_expires_at=datetime.now(timezone.utc) + timedelta(days=300),
    )
    db_session.add(emp)
    db_session.commit()

    r = client.get("/portal/active-token-fixture")
    assert r.status_code == 200

    db_session.refresh(emp)
    last_used = emp.portal_token_last_used
    if last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=timezone.utc)
    assert last_used > old + timedelta(days=29)


def test_portal_responses_send_no_referrer_header(client: any, db_session: Session):
    """Portal HTML pages must not leak the URL token via Referer."""
    emp = Employee(
        first_name="Refer",
        last_name="Block",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]
    r = client.get(f"/portal/{token}")
    assert r.status_code == 200
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "no-store" in r.headers.get("cache-control", "")


def test_portal_token_mint_sets_expiry(client: any, db_session: Session):
    """Newly minted tokens carry an expires_at roughly 1 year out."""
    emp = Employee(
        first_name="Fresh",
        last_name="Token",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    payload = client.get(f"/api/employees/{emp.id}/portal-token").json()
    assert payload["expires_at"] is not None
    expires = datetime.fromisoformat(payload["expires_at"])
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(days=360) < delta < timedelta(days=370)


def test_security_headers_present(client: any):
    """CSP and frame-ancestors must appear on every response."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    csp = r.headers.get("content-security-policy") or ""
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_encryption_roundtrip_with_version_prefix():
    """Ciphertext carries a v{N}: prefix and roundtrips back to plaintext."""
    plaintext = "987654321"
    ct = encrypt(plaintext)
    assert ct is not None
    assert ct.startswith("v1:")
    assert decrypt(ct) == plaintext


def test_encryption_decrypts_legacy_unprefixed_ciphertext():
    """Pre-versioning ciphertext (no v{N}: prefix) still decrypts cleanly."""
    plaintext = "123456789"
    versioned = encrypt(plaintext)
    legacy = versioned[len("v1:") :]
    assert decrypt(legacy) == plaintext


def test_encryption_returns_none_for_garbage():
    """Tampered ciphertext returns None instead of raising or leaking."""
    assert decrypt("v1:not-a-real-fernet-token") is None
    assert decrypt("") is None
    assert decrypt(None) is None


# --- Tier 3: Frontend → Backend wiring -----------------------------------


def test_pto_policy_get_by_id_endpoint_exists(client: any, db_session: Session):
    """GET /api/pto/policies/{id} is wired up — pto.js calls it to edit a policy."""
    policy = PTOPolicy(
        name="Vacation",
        pto_type=PTOType.VACATION,
        accrual_rate=Decimal("1.5"),
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()

    r = client.get(f"/api/pto/policies/{policy.id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Vacation"

    r404 = client.get("/api/pto/policies/99999")
    assert r404.status_code == 404


def test_pto_policy_put_endpoint_updates(client: any, db_session: Session):
    """PUT /api/pto/policies/{id} is wired up — pto.js posts to it on edit."""
    policy = PTOPolicy(
        name="Old Name",
        pto_type=PTOType.VACATION,
        accrual_rate=Decimal("1.0"),
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()

    r = client.put(
        f"/api/pto/policies/{policy.id}",
        json={
            "name": "Renamed Policy",
            "pto_type": "vacation",
            "accrual_method": "per_pay_period",
            "accrual_rate": 2.5,
            "max_carryover": 40,
        },
    )
    assert r.status_code == 200
    db_session.refresh(policy)
    assert policy.name == "Renamed Policy"
    assert float(policy.accrual_rate) == 2.5


def test_pto_request_approve_alias_decisions_request(client: any, db_session: Session):
    """POST /requests/{id}/approve forwards into the decision logic with status=approved."""
    emp = Employee(
        first_name="Approve",
        last_name="Test",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    req = PTORequest(
        employee_id=emp.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=2),
        hours=Decimal("16"),
        pto_type=PTOType.VACATION,
        status=PTORequestStatus.PENDING,
    )
    db_session.add(req)
    db_session.commit()

    r = client.post(f"/api/pto/requests/{req.id}/approve")
    assert r.status_code == 200
    db_session.refresh(req)
    assert req.status == PTORequestStatus.APPROVED


def test_pto_request_reject_alias_decisions_request(client: any, db_session: Session):
    """POST /requests/{id}/reject forwards into the decision logic with status=denied."""
    emp = Employee(
        first_name="Reject",
        last_name="Test",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    req = PTORequest(
        employee_id=emp.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=2),
        hours=Decimal("16"),
        pto_type=PTOType.VACATION,
        status=PTORequestStatus.PENDING,
    )
    db_session.add(req)
    db_session.commit()

    r = client.post(f"/api/pto/requests/{req.id}/reject")
    assert r.status_code == 200
    db_session.refresh(req)
    assert req.status == PTORequestStatus.DENIED
