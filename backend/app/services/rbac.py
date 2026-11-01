import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.services.audit import record_audit_event


class RBACError(ValueError):
    pass


def copy_system_role_to_company(
    session: Session,
    template: Role,
    company: Company,
    *,
    name: str | None = None,
) -> Role:
    if not template.is_system_template:
        raise RBACError("Only global system role templates can be copied")

    role = Role(
        company_id=company.id,
        name=name or template.name,
        description=template.description,
        is_active=template.is_active,
    )
    session.add(role)
    session.flush()

    for permission in template.permissions:
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.flush()

    return role


def assign_role_to_membership(
    session: Session,
    membership: CompanyMembership,
    role: Role,
    *,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> UserRole:
    if role.is_system_template:
        raise RBACError(
            "Global role templates must be copied into the company before assignment"
        )
    if role.company_id != membership.company_id:
        raise RBACError("Role and membership belong to different companies")

    assignment = UserRole(
        membership_id=membership.id,
        role_id=role.id,
        company_id=membership.company_id,
    )
    session.add(assignment)
    session.flush()

    record_audit_event(
        session,
        company_id=membership.company_id,
        actor_user_id=actor_user_id,
        action="rbac.user_role.assigned",
        entity_type="user_role",
        entity_id=assignment.id,
        after_data={
            "membership_id": str(membership.id),
            "role_id": str(role.id),
            "company_id": str(membership.company_id),
        },
        request_id=request_id,
    )

    return assignment


def revoke_role_from_membership(
    session: Session,
    membership: CompanyMembership,
    role: Role,
    *,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> UserRole:
    assignment = session.scalar(
        select(UserRole).where(
            UserRole.membership_id == membership.id,
            UserRole.role_id == role.id,
            UserRole.company_id == membership.company_id,
        )
    )
    if assignment is None:
        raise RBACError("Role is not assigned to this membership")

    assignment_id = assignment.id
    before_data = {
        "membership_id": str(membership.id),
        "role_id": str(role.id),
        "company_id": str(membership.company_id),
    }
    session.delete(assignment)
    session.flush()

    record_audit_event(
        session,
        company_id=membership.company_id,
        actor_user_id=actor_user_id,
        action="rbac.user_role.revoked",
        entity_type="user_role",
        entity_id=assignment_id,
        before_data=before_data,
        request_id=request_id,
    )

    return assignment


def get_effective_permission_codes(
    session: Session,
    membership: CompanyMembership,
) -> set[str]:
    if not membership.is_active:
        return set()

    return set(
        session.scalars(
            select(Permission.code)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.membership_id == membership.id,
                UserRole.company_id == membership.company_id,
                Role.company_id == membership.company_id,
                Role.is_active.is_(True),
            )
            .distinct()
        )
    )


def membership_has_permission(
    session: Session,
    membership: CompanyMembership,
    permission_code: str,
) -> bool:
    if not membership.is_active:
        return False

    permission_id = session.scalar(
        select(Permission.id)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.membership_id == membership.id,
            UserRole.company_id == membership.company_id,
            Role.company_id == membership.company_id,
            Role.is_active.is_(True),
            Permission.code == permission_code,
        )
        .limit(1)
    )
    return permission_id is not None
