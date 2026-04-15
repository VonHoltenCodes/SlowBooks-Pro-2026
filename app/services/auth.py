from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import AuthSession, MembershipPermissionOverride, User, UserMembership

CURRENT_COMPANY_SCOPE = "__current__"
SESSION_DAYS = 30
PASSWORD_ITERATIONS = 600_000

PERMISSION_DEFINITIONS = {
    "contacts.view": "View customers and vendors.",
    "contacts.manage": "Create, update, and deactivate customers and vendors.",
    "items.view": "View items and services.",
    "items.manage": "Create, update, and deactivate items and services.",
    "sales.view": "View sales documents and receipts.",
    "sales.manage": "Create and manage invoices, estimates, recurring sales, receipts, and customer credit notes.",
    "purchasing.view": "View purchasing documents and vendor payments.",
    "purchasing.manage": "Create and manage purchase orders, bills, and vendor payments.",
    "banking.view": "View bank accounts, transactions, and reconciliations.",
    "banking.manage": "Create and manage bank accounts, imports, transactions, and reconciliations.",
    "import_export.view": "Export CSV/IIF data and open import/export workspaces.",
    "import_export.manage": "Import CSV/IIF data and run import validation workflows.",
    "employees.view_private": "View employee payroll/private details.",
    "employees.manage": "Create and update employee records.",
    "employees.filing.export": "Export starter/leaver filing data.",
    "payroll.view": "View payroll runs and stubs.",
    "payroll.create": "Create draft pay runs.",
    "payroll.process": "Process pay runs and post payroll journals.",
    "payroll.payslips.view": "View payslip PDFs.",
    "payroll.payslips.email": "Email payslips.",
    "payroll.filing.export": "Export Employment Information and payroll filing outputs.",
    "settings.manage": "View and update sensitive company settings, including SMTP configuration.",
    "accounts.view": "View the chart of accounts and system account role status.",
    "accounts.manage": "Create, update, and delete accounts.",
    "accounts.system_roles.manage": "Assign and clear system account role mappings.",
    "audit.view": "View the audit log.",
    "backups.view": "List and download backups.",
    "backups.manage": "Create and restore backups.",
    "companies.view": "View company database entries.",
    "companies.manage": "Create company database entries.",
    "users.manage": "Create and manage users, roles, and permission overrides.",
}

ROLE_TEMPLATE_DEFINITIONS = {
    "owner": {
        "label": "Owner",
        "description": "Full access across payroll and admin features.",
        "permissions": set(PERMISSION_DEFINITIONS.keys()),
    },
    "operations_admin": {
        "label": "Operations Admin",
        "description": "Administers business modules, settings, accounts, backups, companies, audit, and users.",
        "permissions": {
            "contacts.view",
            "contacts.manage",
            "items.view",
            "items.manage",
            "sales.view",
            "sales.manage",
            "purchasing.view",
            "purchasing.manage",
            "banking.view",
            "banking.manage",
            "import_export.view",
            "import_export.manage",
            "settings.manage",
            "accounts.view",
            "accounts.manage",
            "accounts.system_roles.manage",
            "audit.view",
            "backups.view",
            "backups.manage",
            "companies.view",
            "companies.manage",
            "users.manage",
        },
    },
    "payroll_admin": {
        "label": "Payroll Admin",
        "description": "Manages employees, payroll runs, payslips, and filing outputs.",
        "permissions": {
            "employees.view_private",
            "employees.manage",
            "employees.filing.export",
            "payroll.view",
            "payroll.create",
            "payroll.process",
            "payroll.payslips.view",
            "payroll.payslips.email",
            "payroll.filing.export",
        },
    },
    "payroll_viewer": {
        "label": "Payroll Viewer",
        "description": "Read-only payroll and private employee access.",
        "permissions": {
            "employees.view_private",
            "payroll.view",
            "payroll.payslips.view",
        },
    },
    "staff": {
        "label": "Staff",
        "description": "Base template with no sensitive/admin capabilities by default.",
        "permissions": set(),
    },
}

ROLE_TEMPLATES = {
    key: value["permissions"]
    for key, value in ROLE_TEMPLATE_DEFINITIONS.items()
}


@dataclass(frozen=True)
class AuthContext:
    user: User
    membership: UserMembership
    session: AuthSession
    permissions: frozenset[str]


class AuthError(HTTPException):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${iterations}${salt}${digest}".format(
        iterations=PASSWORD_ITERATIONS,
        salt=base64.b64encode(salt).decode("ascii"),
        digest=base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def supported_role_keys() -> set[str]:
    return set(ROLE_TEMPLATES)


def supported_permission_keys() -> set[str]:
    return set(PERMISSION_DEFINITIONS)


def validate_role_key(role_key: str) -> str:
    if role_key not in ROLE_TEMPLATES:
        raise HTTPException(status_code=400, detail="Unknown role key")
    return role_key


def validate_permission_keys(permission_keys: list[str] | None) -> list[str]:
    result = []
    for permission in permission_keys or []:
        if permission not in PERMISSION_DEFINITIONS:
            raise HTTPException(status_code=400, detail=f"Unknown permission key: {permission}")
        if permission not in result:
            result.append(permission)
    return result


def supported_role_definitions() -> list[dict]:
    return [
        {
            "key": key,
            "label": value["label"],
            "description": value["description"],
            "permissions": sorted(value["permissions"]),
        }
        for key, value in ROLE_TEMPLATE_DEFINITIONS.items()
    ]


def supported_permission_definitions() -> list[dict]:
    return [
        {"key": key, "description": description}
        for key, description in sorted(PERMISSION_DEFINITIONS.items())
    ]


def users_exist(db: Session) -> bool:
    return db.query(User.id).first() is not None


def _active_membership_for_scope(user: User, company_scope: str = CURRENT_COMPANY_SCOPE) -> UserMembership | None:
    for membership in user.memberships:
        if membership.company_scope == company_scope and membership.is_active:
            return membership
    return None


def resolve_effective_permissions(membership: UserMembership) -> frozenset[str]:
    permissions = set(ROLE_TEMPLATES.get(membership.role_key, set()))
    for override in membership.permission_overrides:
        if override.is_allowed:
            permissions.add(override.permission_key)
        else:
            permissions.discard(override.permission_key)
    return frozenset(sorted(permissions))


def _override_lists(membership: UserMembership) -> tuple[list[str], list[str]]:
    allow = sorted([row.permission_key for row in membership.permission_overrides if row.is_allowed])
    deny = sorted([row.permission_key for row in membership.permission_overrides if not row.is_allowed])
    return allow, deny


def build_user_response(user: User, membership: UserMembership):
    from app.schemas.auth import MembershipResponse, UserResponse

    allow_permissions, deny_permissions = _override_lists(membership)
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at,
        membership=MembershipResponse(
            company_scope=membership.company_scope,
            role_key=membership.role_key,
            is_active=membership.is_active,
            allow_permissions=allow_permissions,
            deny_permissions=deny_permissions,
            effective_permissions=sorted(resolve_effective_permissions(membership)),
        ),
    )


def _issue_session(db: Session, user: User, company_scope: str = CURRENT_COMPANY_SCOPE) -> tuple[str, AuthSession]:
    raw_token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        company_scope=company_scope,
        token_hash=_sha256_text(raw_token),
        expires_at=_utcnow() + timedelta(days=SESSION_DAYS),
        last_used_at=_utcnow(),
    )
    db.add(session)
    db.flush()
    return raw_token, session


def bootstrap_admin_user(db: Session, email: str, password: str, full_name: str):
    if users_exist(db):
        raise HTTPException(status_code=400, detail="Initial admin bootstrap is only available before any users exist")
    user = User(email=email.strip().lower(), full_name=full_name.strip(), password_hash=hash_password(password), is_active=True)
    db.add(user)
    db.flush()
    membership = UserMembership(user_id=user.id, company_scope=CURRENT_COMPANY_SCOPE, role_key="owner", is_active=True)
    db.add(membership)
    raw_token, _session = _issue_session(db, user)
    db.commit()
    db.refresh(user)
    db.refresh(membership)
    return raw_token, build_user_response(user, membership)


def login_user(db: Session, email: str, password: str):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    membership = _active_membership_for_scope(user)
    if not membership:
        raise HTTPException(status_code=403, detail="User does not have an active membership for this company")
    raw_token, _session = _issue_session(db, user, company_scope=membership.company_scope)
    db.commit()
    db.refresh(user)
    return raw_token, build_user_response(user, membership)


def _get_session_by_token(db: Session, raw_token: str) -> AuthSession | None:
    if not raw_token:
        return None
    token_hash = _sha256_text(raw_token)
    session = db.query(AuthSession).filter(AuthSession.token_hash == token_hash).first()
    if not session:
        return None
    if session.revoked_at is not None or _as_utc(session.expires_at) <= _utcnow():
        return None
    return session


def get_auth_context(db: Session, authorization: str | None, *, required: bool = True) -> AuthContext | None:
    raw_token = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            raw_token = token.strip()
    session = _get_session_by_token(db, raw_token)
    if not session:
        if required:
            detail = "Authentication required"
            if not users_exist(db):
                detail += ". Create the initial admin via /api/auth/bootstrap-admin"
            raise HTTPException(status_code=401, detail=detail)
        return None

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Authenticated user is inactive")
    membership = _active_membership_for_scope(user, company_scope=session.company_scope)
    if not membership:
        raise HTTPException(status_code=403, detail="Authenticated user does not have an active membership for this company")

    session.last_used_at = _utcnow()
    db.flush()
    return AuthContext(user=user, membership=membership, session=session, permissions=resolve_effective_permissions(membership))


def get_optional_auth_context(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> AuthContext | None:
    return get_auth_context(db, authorization, required=False)


def require_permissions(*required_permissions: str):
    validated = tuple(validate_permission_keys(list(required_permissions)))

    def dependency(
        db: Session = Depends(get_db),
        authorization: str | None = Header(default=None),
    ) -> AuthContext:
        context = get_auth_context(db, authorization, required=True)
        missing = [permission for permission in validated if permission not in context.permissions]
        if missing:
            raise HTTPException(status_code=403, detail=f"Missing required permissions: {', '.join(missing)}")
        return context

    return dependency


def revoke_session(db: Session, context: AuthContext) -> None:
    context.session.revoked_at = _utcnow()
    db.commit()


def _sync_permission_overrides(db: Session, membership: UserMembership, allow_permissions: list[str], deny_permissions: list[str]) -> None:
    allow_permissions = validate_permission_keys(allow_permissions)
    deny_permissions = validate_permission_keys(deny_permissions)
    overlap = set(allow_permissions) & set(deny_permissions)
    if overlap:
        raise HTTPException(status_code=400, detail=f"Permissions cannot be both allowed and denied: {', '.join(sorted(overlap))}")
    db.query(MembershipPermissionOverride).filter(MembershipPermissionOverride.membership_id == membership.id).delete()
    for permission in allow_permissions:
        db.add(MembershipPermissionOverride(membership_id=membership.id, permission_key=permission, is_allowed=True))
    for permission in deny_permissions:
        db.add(MembershipPermissionOverride(membership_id=membership.id, permission_key=permission, is_allowed=False))
    db.flush()


def create_user_account(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str,
    role_key: str,
    allow_permissions: list[str],
    deny_permissions: list[str],
    is_active: bool,
):
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="User email already exists")
    role_key = validate_role_key(role_key)
    user = User(email=email, full_name=full_name.strip(), password_hash=hash_password(password), is_active=is_active)
    db.add(user)
    db.flush()
    membership = UserMembership(user_id=user.id, company_scope=CURRENT_COMPANY_SCOPE, role_key=role_key, is_active=is_active)
    db.add(membership)
    db.flush()
    _sync_permission_overrides(db, membership, allow_permissions, deny_permissions)
    db.commit()
    db.refresh(user)
    db.refresh(membership)
    return build_user_response(user, membership)


def update_user_account(
    db: Session,
    user_id: int,
    *,
    full_name: str | None = None,
    password: str | None = None,
    role_key: str | None = None,
    allow_permissions: list[str] | None = None,
    deny_permissions: list[str] | None = None,
    is_active: bool | None = None,
    membership_active: bool | None = None,
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    membership = _active_membership_for_scope(user) or db.query(UserMembership).filter(
        UserMembership.user_id == user.id,
        UserMembership.company_scope == CURRENT_COMPANY_SCOPE,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="User membership not found")

    if full_name is not None:
        user.full_name = full_name.strip()
    if password is not None:
        user.password_hash = hash_password(password)
    if role_key is not None:
        membership.role_key = validate_role_key(role_key)
    if is_active is not None:
        user.is_active = is_active
    if membership_active is not None:
        membership.is_active = membership_active
    if allow_permissions is not None or deny_permissions is not None:
        _sync_permission_overrides(db, membership, allow_permissions or [], deny_permissions or [])

    db.commit()
    db.refresh(user)
    db.refresh(membership)
    return build_user_response(user, membership)


def list_user_accounts(db: Session):
    memberships = db.query(UserMembership).filter(UserMembership.company_scope == CURRENT_COMPANY_SCOPE).all()
    return [build_user_response(membership.user, membership) for membership in memberships if membership.user]
