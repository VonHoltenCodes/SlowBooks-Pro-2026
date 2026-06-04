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


# --- Tier 3: Document audit hashing for tax forms ---------------------------


def test_w2_pdf_writes_audit_row(client: any, db_session: Session):
    """Rendering a W-2 PDF inserts a document_audits row with a SHA-256
    over the canonical (company, data) payload."""
    from app.models.document_audit import DocumentAudit

    emp = Employee(
        first_name="Hash",
        last_name="Test",
        ssn_last_four="9999",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    r = client.post(f"/api/payroll/forms/w2/{emp.id}/pdf?year=2026")
    assert r.status_code == 200

    rows = (
        db_session.query(DocumentAudit)
        .filter(DocumentAudit.doc_type == "w2")
        .order_by(DocumentAudit.id.desc())
        .all()
    )
    assert len(rows) >= 1
    last = rows[0]
    assert last.doc_key == f"emp{emp.id}-yr2026"
    assert len(last.content_hash) == 64
    assert all(c in "0123456789abcdef" for c in last.content_hash)


def test_audit_lookup_endpoint_round_trip(client: any, db_session: Session):
    """The /api/document-audits/{id} endpoint returns the row the PDF
    footer points at, and the /verify/{hash} endpoint finds it by hash."""
    emp = Employee(
        first_name="Lookup",
        last_name="Tester",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    client.post(f"/api/payroll/forms/w2/{emp.id}/pdf?year=2026")

    listing = client.get("/api/document-audits?doc_type=w2&limit=1").json()
    assert len(listing) >= 1
    audit = listing[0]

    # Lookup by id
    r = client.get(f"/api/document-audits/{audit['id']}")
    assert r.status_code == 200
    assert r.json()["content_hash"] == audit["content_hash"]

    # Lookup by hash
    r = client.get(f"/api/document-audits/verify/{audit['content_hash']}")
    assert r.status_code == 200
    hits = r.json()
    assert any(h["id"] == audit["id"] for h in hits)


def test_audit_hash_is_deterministic_for_same_data(client: any, db_session: Session):
    """Two PDF renders of the same W-2 produce the same content_hash. The
    audit IDs differ (each render writes its own row) but the hashes match."""
    from app.models.document_audit import DocumentAudit

    emp = Employee(
        first_name="Determ",
        last_name="Inistic",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    client.post(f"/api/payroll/forms/w2/{emp.id}/pdf?year=2026")
    client.post(f"/api/payroll/forms/w2/{emp.id}/pdf?year=2026")

    rows = (
        db_session.query(DocumentAudit)
        .filter(
            DocumentAudit.doc_type == "w2",
            DocumentAudit.doc_key == f"emp{emp.id}-yr2026",
        )
        .order_by(DocumentAudit.id.desc())
        .limit(2)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].content_hash == rows[1].content_hash
    assert rows[0].id != rows[1].id


def test_audit_verify_rejects_non_hex_hash(client: any):
    r = client.get("/api/document-audits/verify/not-a-hash")
    assert r.status_code == 400


def test_compute_doc_hash_handles_decimals_and_dates():
    """The hasher must canonicalize Decimals + datetimes — otherwise two
    semantically identical payloads would hash to different values."""
    from datetime import date as _date
    from app.services.document_audit import compute_doc_hash

    h1 = compute_doc_hash({"amt": Decimal("123.45"), "d": _date(2026, 1, 1)})
    h2 = compute_doc_hash({"amt": Decimal("123.45"), "d": _date(2026, 1, 1)})
    h3 = compute_doc_hash({"amt": Decimal("123.46"), "d": _date(2026, 1, 1)})
    assert h1 == h2
    assert h1 != h3


# --- Tier 3: Cookie-based portal session ------------------------------------


def test_portal_token_url_sets_cookie_and_redirects(client: any, db_session: Session):
    """Visiting /portal/{token} once stamps the slowbooks_portal cookie and
    303-redirects to /portal/. After that the URL never carries the token."""
    emp = Employee(
        first_name="Cookie",
        last_name="Path",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]

    # Don't follow redirects — we want to inspect the 303 + Set-Cookie itself.
    r = client.get(f"/portal/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/portal/"
    # The cookie should be in the response headers (TestClient stores it too).
    # Compare lowercased — different stacks emit attribute casing differently.
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "slowbooks_portal=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie


def test_portal_cookieless_routes_require_cookie(client: any, db_session: Session):
    """Without the cookie, the cookieless routes return 401."""
    # Fresh client without ever claiming a token.
    r = client.get("/portal/", follow_redirects=False)
    assert r.status_code == 401
    r = client.get("/portal/paystubs", follow_redirects=False)
    assert r.status_code == 401


def test_portal_full_cookieless_navigation_after_claim(
    client: any, db_session: Session
):
    """Full happy path: claim once via token URL, then every subsequent
    navigation goes through cookieless URLs."""
    emp = Employee(
        first_name="Flow",
        last_name="Tester",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]

    # Claim
    r = client.get(f"/portal/{token}")  # default follows redirects
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")

    # Cookie is now in the TestClient jar — direct cookieless hits work
    for path in (
        "/portal/",
        "/portal/paystubs",
        "/portal/profile",
        "/portal/bank",
        "/portal/pto",
    ):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_portal_logout_clears_cookie(client: any, db_session: Session):
    """POST /portal/logout deletes the cookie; next request 401s."""
    emp = Employee(
        first_name="Logout",
        last_name="Tester",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]
    client.get(f"/portal/{token}")  # claim

    # Logged in
    assert client.get("/portal/", follow_redirects=False).status_code == 200

    client.post("/portal/logout", follow_redirects=False)
    # TestClient may or may not honor the cookie deletion — clear the jar
    # explicitly to mirror what the browser would do.
    client.cookies.clear()

    assert client.get("/portal/", follow_redirects=False).status_code == 401


def test_portal_end_to_end_flow(client: any, db_session: Session, seed_accounts):
    """One test that walks the entire portal lifecycle:
    mint -> claim -> dashboard -> paystubs -> profile -> bank -> pto ->
    PTO request -> logout -> cookie cleared -> idle/hard expire."""
    from app.models.portal_access import PortalAccess

    emp = Employee(
        first_name="Lifecycle",
        last_name="Tester",
        ssn_last_four="0007",
        pay_type="hourly",
        pay_rate=Decimal("30"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
        email="lc@test.local",
    )
    db_session.add(emp)
    db_session.commit()

    # 1. Mint a token via the admin endpoint
    mint = client.get(f"/api/employees/{emp.id}/portal-token").json()
    token = mint["portal_token"]
    assert mint["expires_at"] is not None

    # 2. Claim — first visit sets the cookie and redirects to /portal/
    r = client.get(f"/portal/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/portal/"
    assert "slowbooks_portal" in r.headers.get("set-cookie", "")

    # 3. Cookie now in TestClient jar — dashboard works
    r = client.get("/portal/", follow_redirects=False)
    assert r.status_code == 200

    # 4. Every cookieless page returns HTML
    for path in ("/portal/paystubs", "/portal/profile", "/portal/bank", "/portal/pto"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} returned {r.status_code}"

    # 5. Submit a PTO request — POST through cookie auth, redirects to GET
    from datetime import date as _date

    pol = PTOPolicy(
        name="Vacation",
        pto_type=PTOType.VACATION,
        accrual_rate=Decimal("1"),
        is_active=True,
    )
    db_session.add(pol)
    db_session.commit()
    accr = PTOAccrual(employee_id=emp.id, policy_id=pol.id, balance=Decimal("40"))
    db_session.add(accr)
    db_session.commit()

    r = client.post(
        "/portal/pto",
        data={
            "start_date": _date.today().isoformat(),
            "end_date": _date.today().isoformat(),
            "hours": "8",
            "pto_type": "vacation",
            "notes": "Single-day vacation",
        },
    )
    assert r.status_code == 200  # 303 -> 200 via follow_redirects default

    # The request landed
    pto_rows = db_session.query(PTORequest).filter_by(employee_id=emp.id).all()
    assert len(pto_rows) == 1
    assert pto_rows[0].status == PTORequestStatus.PENDING

    # 6. Logout clears the portal cookie (but not the admin session cookie —
    # we still need that to hit /api/employees/.../portal-token to rotate).
    client.post("/portal/logout", follow_redirects=False)
    client.cookies.delete("slowbooks_portal", path="/portal")
    r = client.get("/portal/", follow_redirects=False)
    assert r.status_code == 401

    # 7. Force-expire the token + verify rotation works
    emp.portal_token_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    r = client.get(f"/portal/{token}", follow_redirects=False)
    assert r.status_code == 410

    # Rotate via admin
    rotate_resp = client.post(f"/api/employees/{emp.id}/portal-token")
    assert rotate_resp.status_code == 200, rotate_resp.text
    rotated = rotate_resp.json()
    assert rotated["portal_token"] != token
    r = client.get(f"/portal/{rotated['portal_token']}", follow_redirects=False)
    assert r.status_code == 303  # fresh token claims successfully

    # 8. The audit log captured every step
    audits = db_session.query(PortalAccess).order_by(PortalAccess.id).all()
    # Claim (1) + 5 cookieless GETs (2-6) + PTO POST + 2 GETs after POST
    # + cold /portal/ (failure) + expired claim (failure) + new claim (success)
    assert len(audits) >= 8
    assert any(not a.success for a in audits)
    assert any(a.success and a.employee_id == emp.id for a in audits)


def test_portal_access_audit_log_records_success_and_failure(
    client: any, db_session: Session
):
    """Every cookieless portal hit writes a portal_accesses row — success
    when the cookie resolves to a live employee, failure when it doesn't."""
    from app.models.portal_access import PortalAccess

    emp = Employee(
        first_name="Audit",
        last_name="Trail",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]
    client.get(f"/portal/{token}")  # claim sets cookie + records access
    # one authed page-view
    client.get("/portal/paystubs", follow_redirects=False)

    # And one un-cookied attempt (after clearing the jar)
    client.cookies.clear()
    client.get("/portal/", follow_redirects=False)

    rows = db_session.query(PortalAccess).order_by(PortalAccess.id).all()
    # claim, paystubs, failed cold hit = 3 rows minimum
    assert len(rows) >= 3
    success = [r for r in rows if r.success]
    failure = [r for r in rows if not r.success]
    assert any(r.employee_id == emp.id for r in success)
    assert any(r.path == "/portal/" for r in failure)
    # path + ip + ua populated
    for r in rows:
        assert r.path.startswith("/portal/")


def test_audit_log_covers_new_entities_but_skips_audit_tables(
    client: any, db_session: Session
):
    """Lock-in for the audit-coverage matrix.

    The SQLAlchemy after_flush hook auto-logs every model unless the
    table is in `_SKIP_TABLES`. New entities (ResellerPermit, customer
    notes edits, etc.) must show up in `audit_log`; audit-flavored
    tables (portal_accesses, document_audits, login_attempts, email_log)
    must NOT, so we don't double-record the same event.
    """
    from app.models.audit import AuditLog
    from app.models.portal_access import PortalAccess

    # 1) Insert a ResellerPermit — should land in audit_log.
    resp = client.post(
        "/api/reseller-permits",
        json={
            "entity_type": "customer",
            "entity_id": 1,
            "jurisdiction": "WA",
            "permit_number": "123456789",
        },
    )
    assert resp.status_code == 201
    permit_id = resp.json()["id"]

    db_session.expire_all()
    permit_audits = (
        db_session.query(AuditLog)
        .filter_by(table_name="reseller_permits", record_id=permit_id)
        .all()
    )
    assert any(
        a.action == "INSERT" for a in permit_audits
    ), "ResellerPermit INSERT must be audited"

    # 2) Insert a PortalAccess row directly — should NOT land in audit_log.
    pa = PortalAccess(
        employee_id=None,
        ip="1.2.3.4",
        user_agent="test",
        path="/portal/",
        success=False,
    )
    db_session.add(pa)
    db_session.commit()
    db_session.expire_all()

    portal_audits = (
        db_session.query(AuditLog).filter_by(table_name="portal_accesses").all()
    )
    assert (
        portal_audits == []
    ), "portal_accesses is itself an audit table — must not double-log into audit_log"


def test_register_audit_hooks_is_idempotent():
    """Registering the same factory twice must NOT attach the listener twice
    (which would write duplicate audit_log rows for one change)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    from app.services.audit import register_audit_hooks, _after_flush
    from app.models.contacts import Customer

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)

    register_audit_hooks(factory)
    register_audit_hooks(factory)  # second call must be a no-op
    assert event.contains(factory, "after_flush", _after_flush)

    from app.models.audit import AuditLog

    s = factory()
    s.add(Customer(name="Dup Check", is_active=True))
    s.commit()
    rows = s.query(AuditLog).filter_by(table_name="customers").all()
    s.close()
    engine.dispose()
    assert (
        len(rows) == 1
    ), f"expected exactly 1 audit row, got {len(rows)} (double-registered)"


def test_portal_cookieless_profile_save_via_cookie(client: any, db_session: Session):
    """POST /portal/profile (cookieless) saves W-4 fields via cookie auth."""
    emp = Employee(
        first_name="W4",
        last_name="Cookieflow",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]
    client.get(f"/portal/{token}")  # claim sets the cookie

    r = client.post(
        "/portal/profile",
        data={
            "filing_status": "married",
            "dependents_amount": "3",
            "extra_withholding": "75",
        },
    )
    assert r.status_code == 200  # TestClient follows the 303

    db_session.refresh(emp)
    assert emp.filing_status.value == "married"
    assert emp.dependents_amount == 3
    assert emp.extra_withholding == Decimal("75")


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


def test_portal_token_endpoint_returns_last_used(client: any, db_session: Session):
    """GET /api/employees/{id}/portal-token now returns last_used_at so
    the admin UI can show "last used N days ago"."""
    emp = Employee(
        first_name="Last",
        last_name="Used",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    payload = client.get(f"/api/employees/{emp.id}/portal-token").json()
    assert "last_used_at" in payload
    # Brand-new token — last_used was stamped at mint, so should be ~now.
    assert payload["last_used_at"] is not None


def test_portal_access_list_endpoint(client: any, db_session: Session):
    """GET /api/employees/{id}/portal-access lists recent access rows."""
    emp = Employee(
        first_name="Audit",
        last_name="View",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = client.get(f"/api/employees/{emp.id}/portal-token").json()["portal_token"]
    client.get(f"/portal/{token}")  # claim writes one access row

    rows = client.get(f"/api/employees/{emp.id}/portal-access?limit=5").json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert rows[0]["path"].startswith("/portal/")
    for r in rows:
        # success rows are linked to the employee
        if r["success"]:
            # employee_id in DB ; the API didn't surface it but the row
            # is returned because we filtered by employee_id server-side
            assert "ip" in r and "path" in r


def test_everify_lifecycle(client: any, db_session: Session):
    """E-Verify endpoints: read default, update case + status, read back."""
    emp = Employee(
        first_name="Eve",
        last_name="Rify",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    # Default state
    r = client.get(f"/api/employees/{emp.id}/everify").json()
    assert r["status"] == "not_submitted"
    assert r["case_number"] is None
    assert r["submitted_at"] is None
    assert r["closed_at"] is None

    # Submit
    r = client.put(
        f"/api/employees/{emp.id}/everify",
        json={"case_number": "2026123456789", "status": "pending"},
    ).json()
    assert r["status"] == "pending"
    assert r["case_number"] == "2026123456789"
    assert r["submitted_at"] is not None
    assert r["closed_at"] is None

    # Close as authorized
    r = client.put(
        f"/api/employees/{emp.id}/everify",
        json={"status": "employment_authorized", "notes": "All good"},
    ).json()
    assert r["status"] == "employment_authorized"
    assert r["closed_at"] is not None
    assert r["notes"] == "All good"


def test_everify_rejects_unknown_status(client: any, db_session: Session):
    emp = Employee(
        first_name="Bad",
        last_name="Status",
        pay_type="hourly",
        pay_rate=Decimal("25"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    r = client.put(
        f"/api/employees/{emp.id}/everify",
        json={"status": "made-up-status"},
    )
    assert r.status_code == 400


# --- Reseller permits -------------------------------------------------------


def _make_permit(client, **overrides):
    body = {
        "entity_type": "customer",
        "entity_id": 1,
        "jurisdiction": "WA",
        "permit_number": "123-456-789",
        "issued_at": "2024-01-01",
        "expires_at": "2028-01-01",
    }
    body.update(overrides)
    r = client.post("/api/reseller-permits", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_reseller_permit_crud(client: any):
    """Create, read, update, delete a reseller permit."""
    created = _make_permit(client)
    pid = created["id"]
    assert created["jurisdiction"] == "WA"
    # WA 9-digit format auto-normalizes — dashes stripped on write
    assert created["permit_number"] == "123456789"
    # Verification URL pre-filled for WA
    assert created["verification_url"] and "dor.wa.gov" in created["verification_url"]

    fetched = client.get(f"/api/reseller-permits/{pid}").json()
    assert fetched["id"] == pid

    updated = client.put(
        f"/api/reseller-permits/{pid}",
        json={
            "entity_type": "customer",
            "entity_id": 1,
            "jurisdiction": "WA",
            "permit_number": "999-888-777",
            "expires_at": "2030-06-30",
        },
    ).json()
    # PUT applies the same WA normalization
    assert updated["permit_number"] == "999888777"

    deleted = client.delete(f"/api/reseller-permits/{pid}").json()
    assert deleted["deleted"] == pid


def test_reseller_permit_expiry_computation(client: any):
    """days_to_expire + is_expired are computed correctly off expires_at."""
    from datetime import date as _date, timedelta as _td

    future = (_date.today() + _td(days=45)).isoformat()
    past = (_date.today() - _td(days=10)).isoformat()

    fresh = _make_permit(client, permit_number="FUTURE", expires_at=future)
    expired = _make_permit(client, permit_number="EXPIRED", expires_at=past)

    assert fresh["is_expired"] is False
    assert 44 <= fresh["days_to_expire"] <= 46
    assert expired["is_expired"] is True
    assert expired["days_to_expire"] <= -9


def test_reseller_permit_expiring_filter(client: any):
    """The /expiring endpoint returns permits inside the window (and
    already-expired ones, which sort to the front)."""
    from datetime import date as _date, timedelta as _td

    soon = (_date.today() + _td(days=20)).isoformat()
    far = (_date.today() + _td(days=200)).isoformat()
    past = (_date.today() - _td(days=5)).isoformat()

    _make_permit(client, permit_number="SOON-1", expires_at=soon)
    _make_permit(client, permit_number="FAR-1", expires_at=far)
    _make_permit(client, permit_number="EXPIRED-1", expires_at=past)

    rows = client.get("/api/reseller-permits/expiring?within_days=30").json()
    numbers = {r["permit_number"] for r in rows}
    assert "SOON-1" in numbers
    assert "EXPIRED-1" in numbers  # past is <= cutoff so included
    assert "FAR-1" not in numbers


def test_reseller_permit_mark_verified(client: any):
    """POST /mark-verified stamps last_verified_at + verified_by."""
    permit = _make_permit(client, permit_number="VERIFY-ME")
    pid = permit["id"]
    assert permit["last_verified_at"] is None

    stamped = client.post(
        f"/api/reseller-permits/{pid}/mark-verified",
        json={"verified_by": "audit@example.com"},
    ).json()
    assert stamped["last_verified_at"] is not None
    assert stamped["verified_by"] == "audit@example.com"


def test_reseller_permit_wa_format_normalizes_dashes(client: any):
    """WA permits often come in as 123-456-789 — backend strips to digits
    so two writes that differ only in formatting collide on the same row."""
    p1 = _make_permit(client, jurisdiction="WA", permit_number="123-456-789")
    p2 = _make_permit(client, jurisdiction="WA", permit_number="123456789")
    assert p1["permit_number"] == "123456789"
    assert p2["permit_number"] == "123456789"


def test_reseller_permit_validate_format_endpoint(client: any):
    """The /validate-format endpoint returns ok=True for matching formats,
    ok=False (but doesn't raise) for mismatches."""
    r = client.get(
        "/api/reseller-permits/validate-format"
        "?jurisdiction=WA&permit_number=123-456-789"
    ).json()
    assert r["ok"] is True
    assert r["normalized"] == "123456789"
    assert "9 digits" in r["message"]

    r = client.get(
        "/api/reseller-permits/validate-format" "?jurisdiction=WA&permit_number=12345"
    ).json()
    assert r["ok"] is False
    assert "9 digits" in r["message"]

    # Unknown state — no rule, returns ok=True with explanatory message
    r = client.get(
        "/api/reseller-permits/validate-format"
        "?jurisdiction=ZZ&permit_number=anything-goes"
    ).json()
    assert r["ok"] is True
    assert "no format rule" in r["message"].lower()


def test_reseller_permit_invalid_entity_type_rejected(client: any):
    r = client.post(
        "/api/reseller-permits",
        json={
            "entity_type": "garbage",
            "jurisdiction": "WA",
            "permit_number": "X",
        },
    )
    assert r.status_code == 400


def test_rewrap_all_re_encrypts_old_key_ciphertext(db_session: Session):
    """rewrap_all() finds ciphertext encrypted with the previous key and
    re-encrypts it with the current key. Already-current rows are skipped."""

    import app.services.encryption as enc

    # Snapshot the current key chain.
    old_fernets = enc._fernets

    # Pretend the current key was different by swapping the chain temporarily:
    # _fernets[0] becomes a "new" key; _fernets[-1] is the "old" key that we
    # encrypt the seed data with.
    new_secret = "test-new-secret-different-from-default"
    old_secret = "test-old-secret-different-from-new"
    new_fernet = enc._derive_fernet(new_secret)
    old_fernet = enc._derive_fernet(old_secret)

    # Seed an account with ciphertext encrypted under the OLD key directly.
    enc._fernets = [old_fernet]  # so encrypt() uses the old key
    routing_blob = enc.encrypt("123456789")
    account_blob = enc.encrypt("9876543210")

    rewrap_emp = Employee(
        first_name="Rew",
        last_name="Rap",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(rewrap_emp)
    db_session.commit()
    acct = EmployeeBankAccount(
        employee_id=rewrap_emp.id,
        nickname="Rewrap Test",
        account_kind=BankAccountKind.CHECKING,
        routing_number_enc=routing_blob,
        account_number_enc=account_blob,
        account_last_four="3210",
    )
    db_session.add(acct)
    db_session.commit()

    # Now rotate: current key is new, old key fall-through is old.
    enc._fernets = [new_fernet, old_fernet]
    try:
        summary = enc.rewrap_all(db_session, dry_run=False)
    finally:
        enc._fernets = old_fernets

    assert summary["checked"] == 2  # both fields
    assert summary["rewrapped"] == 2
    assert summary["already_current"] == 0
    assert summary["failed"] == 0

    # After rewrap, both blobs should decrypt under the new key alone.
    enc._fernets = [new_fernet]
    try:
        db_session.refresh(acct)
        assert enc.decrypt(acct.routing_number_enc) == "123456789"
        assert enc.decrypt(acct.account_number_enc) == "9876543210"
    finally:
        enc._fernets = old_fernets


def test_rewrap_all_skips_already_current(db_session: Session):
    """Rows already encrypted with the current key get counted but not touched."""
    import app.services.encryption as enc

    same_emp = Employee(
        first_name="Same",
        last_name="Key",
        pay_type="hourly",
        pay_rate=Decimal("20"),
        pay_frequency="biweekly",
        filing_status="single",
        is_active=True,
    )
    db_session.add(same_emp)
    db_session.commit()
    acct = EmployeeBankAccount(
        employee_id=same_emp.id,
        nickname="Already Current",
        account_kind=BankAccountKind.CHECKING,
        routing_number_enc=enc.encrypt("111222333"),
        account_number_enc=enc.encrypt("4444444444"),
        account_last_four="4444",
    )
    db_session.add(acct)
    db_session.commit()

    orig_routing = acct.routing_number_enc
    summary = enc.rewrap_all(db_session, dry_run=False)
    db_session.refresh(acct)

    assert summary["already_current"] == 2
    assert summary["rewrapped"] == 0
    # Blob unchanged
    assert acct.routing_number_enc == orig_routing


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


def test_portal_access_log_redacts_token():
    """The portal access log must never persist the live token in the path —
    /portal/<token> would be a working bearer credential in the DB."""
    from app.routes.portal import _redact_portal_path

    tok = "abcDEF123456789_-xyzABC789012"  # ~29 chars, token-shaped
    assert _redact_portal_path(f"/portal/{tok}") == "/portal/REDACTED"
    assert _redact_portal_path(f"/portal/{tok}/paystubs") == "/portal/REDACTED/paystubs"
    # Cookieless route names are short — must pass through untouched.
    assert _redact_portal_path("/portal/paystubs") == "/portal/paystubs"
    assert _redact_portal_path("/portal/") == "/portal/"
