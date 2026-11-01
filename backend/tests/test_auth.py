import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import resolve_tenant_context
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.user import User

PASSWORD = "S3curePassw0rd!"


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _create_user(
    db_session,
    *,
    email: str = "auth@example.test",
    password: str = PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name="Auth User",
        is_active=is_active,
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


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_and_read_current_user(client, db_session):
    user = _create_user(db_session, email="login@example.test")

    response = client.post(
        "/auth/login",
        json={"email": user.email.upper(), "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert "hashed_password" not in response.text

    me_response = client.get(
        "/auth/me",
        headers=_auth_header(body["access_token"]),
    )
    assert me_response.status_code == 200
    me = me_response.json()
    assert me["id"] == str(user.id)
    assert me["email"] == user.email
    assert me["full_name"] == "Auth User"
    assert me["is_active"] is True
    assert "hashed_password" not in me_response.text


def test_login_rejects_incorrect_credentials(client, db_session):
    _create_user(db_session, email="wrong-password@example.test")

    wrong_password = client.post(
        "/auth/login",
        json={"email": "wrong-password@example.test", "password": "wrong"},
    )
    unknown_email = client.post(
        "/auth/login",
        json={"email": "missing@example.test", "password": PASSWORD},
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == "Incorrect email or password"
    assert unknown_email.json()["detail"] == "Incorrect email or password"


def test_expired_and_invalid_tokens_are_rejected(client, db_session):
    user = _create_user(db_session, email="tokens@example.test")
    expired = create_access_token(
        user.id,
        expires_delta=timedelta(seconds=-1),
    )
    invalid = "not-a-valid-jwt"
    wrong_signature = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "wrong-signing-secret",
        algorithm="HS256",
    )

    for token in (expired, invalid, wrong_signature):
        response = client.get("/auth/me", headers=_auth_header(token))
        assert response.status_code == 401

    missing = client.get("/auth/me")
    assert missing.status_code == 401


def test_inactive_user_cannot_login_or_use_existing_token(client, db_session):
    user = _create_user(
        db_session,
        email="inactive@example.test",
        is_active=False,
    )

    login = client.post(
        "/auth/login",
        json={"email": user.email, "password": PASSWORD},
    )
    assert login.status_code == 401

    user.is_active = True
    db_session.commit()
    token = create_access_token(user.id)

    user.is_active = False
    db_session.commit()

    me = client.get("/auth/me", headers=_auth_header(token))
    assert me.status_code == 401


def test_list_companies_only_returns_active_memberships(client, db_session):
    user = _create_user(db_session, email="companies@example.test")
    active_company = _create_company(db_session, slug="active-company")
    inactive_membership_company = _create_company(
        db_session,
        slug="inactive-membership",
    )
    inactive_company = _create_company(db_session, slug="inactive-company")
    inactive_company.is_active = False
    db_session.commit()

    _create_membership(db_session, user, active_company)
    _create_membership(
        db_session,
        user,
        inactive_membership_company,
        is_active=False,
    )
    _create_membership(db_session, user, inactive_company)

    token = create_access_token(user.id)
    response = client.get("/auth/companies", headers=_auth_header(token))

    assert response.status_code == 200
    companies = response.json()
    assert len(companies) == 1
    assert companies[0]["id"] == str(active_company.id)
    assert companies[0]["name"] == active_company.name
    assert companies[0]["role"] == "member"


def test_resolve_tenant_context_sets_company_context(db_session):
    user = _create_user(db_session, email="tenant@example.test")
    company = _create_company(db_session, slug="tenant-company")
    membership = _create_membership(db_session, user, company)

    context = resolve_tenant_context(db_session, user, company.id)

    assert context.user.id == user.id
    assert context.company.id == company.id
    assert context.membership.id == membership.id
    assert db_session.info["current_company_id"] == str(company.id)


def test_resolve_tenant_context_rejects_missing_membership(db_session):
    user = _create_user(db_session, email="no-membership@example.test")
    company = _create_company(db_session, slug="no-membership-company")

    with pytest.raises(HTTPException) as exc_info:
        resolve_tenant_context(db_session, user, company.id)

    assert exc_info.value.status_code == 403
    assert "current_company_id" not in db_session.info


def test_resolve_tenant_context_rejects_inactive_membership(db_session):
    user = _create_user(db_session, email="inactive-membership@example.test")
    company = _create_company(db_session, slug="inactive-membership-company")
    _create_membership(db_session, user, company, is_active=False)

    with pytest.raises(HTTPException) as exc_info:
        resolve_tenant_context(db_session, user, company.id)

    assert exc_info.value.status_code == 403
    assert "current_company_id" not in db_session.info


def test_resolve_tenant_context_rejects_cross_company_access(db_session):
    user = _create_user(db_session, email="cross-company@example.test")
    company_a = _create_company(db_session, slug="cross-company-a")
    company_b = _create_company(db_session, slug="cross-company-b")
    _create_membership(db_session, user, company_a)

    with pytest.raises(HTTPException) as exc_info:
        resolve_tenant_context(db_session, user, company_b.id)

    assert exc_info.value.status_code == 403
    assert "current_company_id" not in db_session.info


def _pg_session():
    url = os.getenv("NARA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NARA_TEST_DATABASE_URL is not set")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url)
    return engine, sessionmaker(bind=engine)()


@pytest.fixture
def pg_session():
    engine, session = _pg_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_tenant_context_is_transaction_scoped_on_postgresql(pg_session):
    user = User(
        email=f"pg-tenant-{uuid.uuid4()}@example.test",
        hashed_password=hash_password(PASSWORD),
        full_name="PG Tenant User",
    )
    company = Company(name="PG Tenant", slug=f"pg-tenant-{uuid.uuid4()}")
    pg_session.add_all([user, company])
    pg_session.flush()

    membership = CompanyMembership(user_id=user.id, company_id=company.id)
    pg_session.add(membership)
    pg_session.flush()

    context = resolve_tenant_context(pg_session, user, company.id)
    assert context.company.id == company.id
    assert context.user.id == user.id
    assert context.membership.id == membership.id

    current_company_id = pg_session.scalar(
        text("SELECT current_setting('app.current_company_id', true)")
    )
    assert current_company_id == str(company.id)

    pg_session.rollback()

    context_after_rollback = pg_session.scalar(
        text("SELECT current_setting('app.current_company_id', true)")
    )
    assert context_after_rollback in (None, "")


def test_jwt_settings_use_configured_secret():
    assert len(settings.SECRET_KEY) >= 32
