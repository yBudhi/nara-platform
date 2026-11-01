import pytest
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.user import User
from sqlalchemy.exc import IntegrityError


def test_create_user(db_session):
    user = User(
        email="test@example.com",
        hashed_password="fakehashedpassword",
        full_name="Test User",
        system_role="user",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.id is not None
    assert user.email == "test@example.com"
    assert user.is_active is True
    assert user.system_role == "user"


def test_create_company(db_session):
    company = Company(
        name="Acme Corp",
        slug="acme-corp",
    )
    db_session.add(company)
    db_session.commit()
    db_session.refresh(company)

    assert company.id is not None
    assert company.name == "Acme Corp"
    assert company.slug == "acme-corp"
    assert company.is_active is True


def test_company_membership(db_session):
    user = User(
        email="member@example.com",
        hashed_password="password",
        full_name="Member User",
    )
    company = Company(
        name="Beta Inc",
        slug="beta-inc",
    )
    db_session.add_all([user, company])
    db_session.commit()

    membership = CompanyMembership(
        user_id=user.id,
        company_id=company.id,
        role="admin",
    )
    db_session.add(membership)
    db_session.commit()
    db_session.refresh(membership)

    assert membership.id is not None
    assert membership.user_id == user.id
    assert membership.company_id == company.id
    assert membership.role == "admin"
    assert len(user.memberships) == 1
    assert len(company.memberships) == 1


def test_email_uniqueness_is_case_insensitive(db_session):
    db_session.add(
        User(
            email="User@Example.com",
            hashed_password="first",
        )
    )
    db_session.commit()

    db_session.add(
        User(
            email="user@example.com",
            hashed_password="second",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_membership_is_unique_per_user_and_company(db_session):
    user = User(email="unique@example.com", hashed_password="password")
    company = Company(name="Unique Corp", slug="unique-corp")
    db_session.add_all([user, company])
    db_session.commit()

    db_session.add(
        CompanyMembership(user_id=user.id, company_id=company.id, role="admin")
    )
    db_session.commit()

    db_session.add(
        CompanyMembership(user_id=user.id, company_id=company.id, role="member")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
