import pytest
from sqlalchemy.exc import IntegrityError

from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.services.rbac import (
    RBACError,
    assign_role_to_membership,
    copy_system_role_to_company,
)


def _create_company_with_membership(db_session, *, slug: str, email: str):
    user = User(email=email, hashed_password="password")
    company = Company(name=slug.replace("-", " ").title(), slug=slug)
    db_session.add_all([user, company])
    db_session.commit()

    membership = CompanyMembership(user_id=user.id, company_id=company.id)
    db_session.add(membership)
    db_session.commit()

    return user, company, membership


def _create_permission(db_session, *, code: str = "users.read") -> Permission:
    permission = Permission(
        code=code,
        description="Read users",
        resource="users",
        action="read",
        module_code="identity",
    )
    db_session.add(permission)
    db_session.commit()
    return permission


def test_assign_company_role_to_membership(db_session):
    _, company, membership = _create_company_with_membership(
        db_session, slug="acme", email="admin@acme.test"
    )
    permission = _create_permission(db_session)
    role = Role(company_id=company.id, name="Administrator")
    role.permissions.append(permission)
    db_session.add(role)
    db_session.commit()

    assignment = assign_role_to_membership(db_session, membership, role)
    db_session.commit()
    db_session.refresh(assignment)

    assert assignment.id is not None
    assert assignment.membership_id == membership.id
    assert assignment.role_id == role.id
    assert assignment.company_id == company.id
    assert role.is_system_template is False
    assert role.permissions == [permission]


def test_duplicate_permission_assignment_is_rejected(db_session):
    _, company, _ = _create_company_with_membership(
        db_session, slug="duplicate-permission", email="duplicate@example.test"
    )
    permission = _create_permission(db_session)
    role = Role(company_id=company.id, name="Reader")
    db_session.add(role)
    db_session.commit()

    db_session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db_session.commit()

    db_session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cross_company_role_assignment_is_rejected(db_session):
    _, company_a, membership_a = _create_company_with_membership(
        db_session, slug="company-a", email="a@example.test"
    )
    _, company_b, _ = _create_company_with_membership(
        db_session, slug="company-b", email="b@example.test"
    )
    role_b = Role(company_id=company_b.id, name="Administrator")
    db_session.add(role_b)
    db_session.commit()

    db_session.add(
        UserRole(
            membership_id=membership_a.id,
            role_id=role_b.id,
            company_id=company_a.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cross_company_assignment_is_rejected_by_service(db_session):
    _, _, membership_a = _create_company_with_membership(
        db_session, slug="service-a", email="service-a@example.test"
    )
    _, company_b, _ = _create_company_with_membership(
        db_session, slug="service-b", email="service-b@example.test"
    )
    role_b = Role(company_id=company_b.id, name="Administrator")
    db_session.add(role_b)
    db_session.commit()

    with pytest.raises(RBACError):
        assign_role_to_membership(db_session, membership_a, role_b)
    db_session.rollback()


def test_global_role_template_cannot_be_assigned_directly(db_session):
    _, company, membership = _create_company_with_membership(
        db_session, slug="template-target", email="target@example.test"
    )
    permission = _create_permission(db_session)
    template = Role(name="Administrator", description="System template")
    template.permissions.append(permission)
    db_session.add(template)
    db_session.commit()

    assert template.company_id is None
    assert template.is_system_template is True

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

    with pytest.raises(RBACError):
        assign_role_to_membership(db_session, membership, template)
    db_session.rollback()


def test_global_role_template_can_be_copied_and_assigned(db_session):
    _, company, membership = _create_company_with_membership(
        db_session, slug="copied-template", email="copied@example.test"
    )
    permission = _create_permission(db_session)
    template = Role(name="Administrator", description="System template")
    template.permissions.append(permission)
    db_session.add(template)
    db_session.commit()

    copied = copy_system_role_to_company(db_session, template, company)
    db_session.commit()
    db_session.refresh(copied)

    assert copied.company_id == company.id
    assert copied.name == template.name
    assert copied.is_system_template is False
    assert [item.code for item in copied.permissions] == [permission.code]

    assignment = assign_role_to_membership(db_session, membership, copied)
    db_session.commit()

    assert assignment.role_id == copied.id
    assert assignment.company_id == company.id


def test_company_role_names_are_unique_per_company(db_session):
    _, company_a, _ = _create_company_with_membership(
        db_session, slug="name-a", email="name-a@example.test"
    )
    _, company_b, _ = _create_company_with_membership(
        db_session, slug="name-b", email="name-b@example.test"
    )

    db_session.add(Role(company_id=company_a.id, name="Manager"))
    db_session.add(Role(company_id=company_b.id, name="Manager"))
    db_session.commit()

    db_session.add(Role(company_id=company_a.id, name="manager"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_permission_codes_are_unique(db_session):
    db_session.add(
        Permission(code="users.read", resource="users", action="read")
    )
    db_session.commit()

    db_session.add(
        Permission(code="users.read", resource="users", action="read")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_role_assignment_is_rejected(db_session):
    _, company, membership = _create_company_with_membership(
        db_session, slug="duplicate-role", email="duplicate-role@example.test"
    )
    role = Role(company_id=company.id, name="Administrator")
    db_session.add(role)
    db_session.commit()

    db_session.add(
        UserRole(
            membership_id=membership.id,
            role_id=role.id,
            company_id=company.id,
        )
    )
    db_session.commit()

    db_session.add(
        UserRole(
            membership_id=membership.id,
            role_id=role.id,
            company_id=company.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
