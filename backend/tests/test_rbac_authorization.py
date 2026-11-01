import os
import uuid
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import TenantContext, require_permission
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.rbac import Permission, Role, UserRole
from app.models.user import User
from app.services.rbac import (
    RBACError,
    assign_role_to_membership,
    revoke_role_from_membership,
)

PASSWORD = "S3curePassw0rd!"
PERMISSION_CATALOG = {
    "company.read",
    "company.manage",
    "members.read",
    "members.manage",
    "roles.read",
    "roles.manage",
    "modules.read",
    "modules.manage",
    "audit.read",
}

authorization_app = FastAPI()


@authorization_app.get("/protected")
def protected_route(
    context: Annotated[TenantContext, Depends(require_permission("roles.read"))],
) -> dict[str, str]:
    return {"company_id": str(context.company.id)}


@pytest.fixture
def api_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def protected_client(db_session):
    def override_get_db():
        yield db_session

    authorization_app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(authorization_app) as client:
            yield client
    finally:
        authorization_app.dependency_overrides.clear()


def _create_user(db_session, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        full_name="Authorization User",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _create_company(db_session, *, slug: str) -> Company:
    company = Company(name=slug.replace("-", " ").title(), slug=slug)
    db_session.add(company)
    db_session.commit()
    return company


def _create_membership(
    db_session,
    user: User,
    company: Company,
    *,
    is_active: bool = True,
) -> CompanyMembership:
    membership = CompanyMembership(
        user_id=user.id,
        company_id=company.id,
        is_active=is_active,
    )
    db_session.add(membership)
    db_session.commit()
    return membership


def _get_or_create_permission(db_session, code: str) -> Permission:
    permission = db_session.scalar(
        select(Permission).where(Permission.code == code)
    )
    if permission is not None:
        return permission

    resource, action = code.split(".", 1)
    permission = Permission(
        code=code,
        description=code,
        resource=resource,
        action=action,
    )
    db_session.add(permission)
    db_session.flush()
    return permission


def _create_company_role(
    db_session,
    company: Company,
    *,
    name: str,
    permission_codes: set[str],
) -> Role:
    role = Role(company_id=company.id, name=name)
    db_session.add(role)
    for code in permission_codes:
        role.permissions.append(_get_or_create_permission(db_session, code))
    db_session.commit()
    return role


def _assign(db_session, membership, role) -> None:
    assign_role_to_membership(db_session, membership, role)
    db_session.commit()


def _headers(user: User, company: Company) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id)}",
        "X-Company-ID": str(company.id),
    }


def test_company_role_permission_authorizes_request(
    protected_client,
    db_session,
):
    user = _create_user(db_session, email="authorized@example.test")
    company = _create_company(db_session, slug="authorized-company")
    membership = _create_membership(db_session, user, company)
    role = _create_company_role(
        db_session,
        company,
        name="Role Reader",
        permission_codes={"roles.read"},
    )
    _assign(db_session, membership, role)

    response = protected_client.get(
        "/protected",
        headers=_headers(user, company),
    )

    assert response.status_code == 200
    assert response.json() == {"company_id": str(company.id)}


def test_missing_permission_returns_403(protected_client, db_session):
    user = _create_user(db_session, email="forbidden@example.test")
    company = _create_company(db_session, slug="forbidden-company")
    membership = _create_membership(db_session, user, company)
    role = _create_company_role(
        db_session,
        company,
        name="Role Manager",
        permission_codes={"roles.manage"},
    )
    _assign(db_session, membership, role)

    response = protected_client.get(
        "/protected",
        headers=_headers(user, company),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission required: roles.read"


def test_revoked_role_no_longer_grants_permission(
    protected_client,
    db_session,
):
    user = _create_user(db_session, email="revoked@example.test")
    company = _create_company(db_session, slug="revoked-company")
    membership = _create_membership(db_session, user, company)
    role = _create_company_role(
        db_session,
        company,
        name="Role Reader",
        permission_codes={"roles.read"},
    )
    _assign(db_session, membership, role)

    before = protected_client.get(
        "/protected",
        headers=_headers(user, company),
    )
    assert before.status_code == 200

    revoke_role_from_membership(db_session, membership, role)
    db_session.commit()

    after = protected_client.get(
        "/protected",
        headers=_headers(user, company),
    )
    assert after.status_code == 403


def test_role_from_another_company_cannot_grant_permission(
    protected_client,
    db_session,
):
    user = _create_user(db_session, email="cross-company@example.test")
    company_a = _create_company(db_session, slug="authorization-company-a")
    company_b = _create_company(db_session, slug="authorization-company-b")
    membership_a = _create_membership(db_session, user, company_a)
    membership_b = _create_membership(db_session, user, company_b)
    role_b = _create_company_role(
        db_session,
        company_b,
        name="Role Reader",
        permission_codes={"roles.read"},
    )
    _assign(db_session, membership_b, role_b)

    response_a = protected_client.get(
        "/protected",
        headers=_headers(user, company_a),
    )
    response_b = protected_client.get(
        "/protected",
        headers=_headers(user, company_b),
    )

    assert response_a.status_code == 403
    assert response_b.status_code == 200
    assert membership_a.id != membership_b.id


def test_inactive_membership_is_rejected(protected_client, db_session):
    user = _create_user(db_session, email="inactive-member@example.test")
    company = _create_company(db_session, slug="inactive-member-company")
    membership = _create_membership(db_session, user, company)
    role = _create_company_role(
        db_session,
        company,
        name="Role Reader",
        permission_codes={"roles.read"},
    )
    _assign(db_session, membership, role)

    membership.is_active = False
    db_session.commit()

    response = protected_client.get(
        "/protected",
        headers=_headers(user, company),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Active membership is required for this company"
    )


def test_global_role_template_cannot_be_assigned_directly(db_session):
    user = _create_user(db_session, email="template@example.test")
    company = _create_company(db_session, slug="template-company")
    membership = _create_membership(db_session, user, company)
    template = Role(
        name="Global Template",
        description="Global role template",
    )
    template.permissions.append(
        _get_or_create_permission(db_session, "roles.read")
    )
    db_session.add(template)
    db_session.commit()

    with pytest.raises(RBACError):
        assign_role_to_membership(db_session, membership, template)
    db_session.rollback()

    db_session.add(
        UserRole(
            membership_id=membership.id,
            role_id=template.id,
            company_id=company.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_current_permissions_only_include_active_company(
    api_client,
    db_session,
):
    user = _create_user(db_session, email="permissions@example.test")
    company_a = _create_company(db_session, slug="permissions-company-a")
    company_b = _create_company(db_session, slug="permissions-company-b")
    membership_a = _create_membership(db_session, user, company_a)
    membership_b = _create_membership(db_session, user, company_b)
    role_a = _create_company_role(
        db_session,
        company_a,
        name="Role Reader",
        permission_codes={"roles.read", "members.read"},
    )
    role_b = _create_company_role(
        db_session,
        company_b,
        name="Role Module Manager",
        permission_codes={"modules.manage"},
    )
    _assign(db_session, membership_a, role_a)
    _assign(db_session, membership_b, role_b)

    response_a = api_client.get(
        "/auth/me/permissions",
        headers=_headers(user, company_a),
    )
    response_b = api_client.get(
        "/auth/me/permissions",
        headers=_headers(user, company_b),
    )
    missing_header = api_client.get(
        "/auth/me/permissions",
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )

    assert response_a.status_code == 200
    assert response_a.json() == ["members.read", "roles.read"]
    assert response_b.status_code == 200
    assert response_b.json() == ["modules.manage"]
    assert missing_header.status_code == 400
    assert "modules.manage" not in response_a.json()
    assert "roles.read" not in response_b.json()


def test_current_permissions_require_authentication(api_client):
    response = api_client.get(
        "/auth/me/permissions",
        headers={"X-Company-ID": str(uuid.uuid4())},
    )

    assert response.status_code == 401


def test_seeded_permission_catalog_on_postgresql():
    url = os.getenv("NARA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NARA_TEST_DATABASE_URL is not set")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            codes = set(
                connection.scalars(text("SELECT code FROM permissions")).all()
            )
    finally:
        engine.dispose()

    assert PERMISSION_CATALOG <= codes
