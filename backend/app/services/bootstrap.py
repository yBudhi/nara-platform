import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import set_current_company_id
from app.core.security import hash_password
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.module import Module
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.services.modules import activate_module, is_module_enabled
from app.services.organization import create_company
from app.services.rbac import (
    assign_role_to_membership,
    get_effective_permission_codes,
)

COMPANY_NAME = "WE Playground"
COMPANY_SLUG = "we-playground"
ADMIN_ROLE_NAME = "company_admin"
AGENT_ROLE_NAME = "nara_ai_agent"
AGENT_PERMISSION_CODES = frozenset(
    {
        "incidents.read",
        "incidents.create",
        "incidents.analyze",
    }
)
INCIDENTS_MODULE_CODE = "incidents"

ADMIN_FULL_NAME = "Administrator"
AGENT_FULL_NAME = "NARA AI Agent"


class BootstrapError(ValueError):
    pass


@dataclass(frozen=True)
class BootstrapResult:
    company: Company
    admin_user: User
    agent_user: User
    admin_membership: CompanyMembership
    agent_membership: CompanyMembership
    admin_role: Role
    agent_role: Role


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if (
        not normalized
        or normalized.count("@") != 1
        or normalized.startswith("@")
        or normalized.endswith("@")
    ):
        raise BootstrapError(f"Invalid email address: {email!r}")
    return normalized


def _get_or_create_company(session: Session) -> Company:
    company = session.scalar(
        select(Company).where(Company.slug == COMPANY_SLUG)
    )
    if company is not None:
        if company.name != COMPANY_NAME:
            raise BootstrapError(
                f"Company slug '{COMPANY_SLUG}' belongs to '{company.name}', "
                f"not '{COMPANY_NAME}'"
            )
        if not company.is_active:
            raise BootstrapError(f"Company '{COMPANY_SLUG}' is inactive")
        return company

    name_conflict = session.scalar(
        select(Company).where(func.lower(Company.name) == COMPANY_NAME.lower())
    )
    if name_conflict is not None:
        raise BootstrapError(
            f"Company name '{COMPANY_NAME}' already exists with slug "
            f"'{name_conflict.slug}'"
        )

    return create_company(
        session,
        name=COMPANY_NAME,
        slug=COMPANY_SLUG,
    )


def _load_permission_catalog(session: Session) -> dict[str, Permission]:
    permissions = session.scalars(
        select(Permission).order_by(Permission.code)
    ).all()
    if not permissions:
        raise BootstrapError(
            "Permission catalog is empty; apply database migrations first"
        )

    catalog = {permission.code: permission for permission in permissions}
    missing = sorted(AGENT_PERMISSION_CODES - catalog.keys())
    if missing:
        raise BootstrapError(
            "Required permissions are missing: " + ", ".join(missing)
        )
    return catalog


def _get_or_create_user(
    session: Session,
    *,
    email: str,
    password: str | None,
    full_name: str,
    label: str,
) -> User:
    normalized_email = normalize_email(email)
    user = session.scalar(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    if user is not None:
        if not user.is_active:
            raise BootstrapError(f"{label} user '{normalized_email}' is inactive")
        return user

    if not password:
        raise BootstrapError(
            f"A password is required to create the {label.lower()} user"
        )

    user = User(
        email=normalized_email,
        hashed_password=hash_password(password),
        full_name=full_name,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _get_or_create_membership(
    session: Session,
    *,
    user: User,
    company: Company,
    label: str,
) -> CompanyMembership:
    membership = session.scalar(
        select(CompanyMembership).where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
    )
    if membership is not None:
        if not membership.is_active:
            raise BootstrapError(
                f"{label} membership for '{user.email}' is inactive"
            )
        return membership

    membership = CompanyMembership(
        user_id=user.id,
        company_id=company.id,
        is_active=True,
    )
    session.add(membership)
    session.flush()
    return membership


def _get_or_create_role(
    session: Session,
    *,
    company: Company,
    name: str,
    description: str,
    expected_permission_ids: set[uuid.UUID],
) -> Role:
    role = session.scalar(
        select(Role).where(
            Role.company_id == company.id,
            func.lower(Role.name) == name.lower(),
        )
    )
    if role is None:
        role = Role(
            company_id=company.id,
            name=name,
            description=description,
            is_active=True,
        )
        session.add(role)
        session.flush()
    elif not role.is_active:
        raise BootstrapError(f"Role '{name}' is inactive")

    current_permission_ids = set(
        session.scalars(
            select(RolePermission.permission_id).where(
                RolePermission.role_id == role.id
            )
        )
    )
    unexpected = current_permission_ids - expected_permission_ids
    if unexpected:
        unexpected_codes = session.scalars(
            select(Permission.code)
            .where(Permission.id.in_(unexpected))
            .order_by(Permission.code)
        ).all()
        raise BootstrapError(
            f"Role '{name}' has unexpected permissions: "
            + ", ".join(unexpected_codes)
        )

    missing = expected_permission_ids - current_permission_ids
    for permission_id in missing:
        session.add(
            RolePermission(role_id=role.id, permission_id=permission_id)
        )
    session.flush()
    return role


def _ensure_role_assignment(
    session: Session,
    *,
    membership: CompanyMembership,
    role: Role,
    actor_user_id: uuid.UUID,
) -> None:
    assignment = session.scalar(
        select(UserRole).where(
            UserRole.membership_id == membership.id,
            UserRole.role_id == role.id,
            UserRole.company_id == membership.company_id,
        )
    )
    if assignment is None:
        assign_role_to_membership(
            session,
            membership,
            role,
            actor_user_id=actor_user_id,
        )


def _ensure_incidents_module(
    session: Session,
    *,
    company: Company,
    actor_user_id: uuid.UUID,
) -> None:
    module = session.scalar(
        select(Module).where(Module.code == INCIDENTS_MODULE_CODE)
    )
    if module is None:
        raise BootstrapError(
            f"Module '{INCIDENTS_MODULE_CODE}' is not registered; "
            "apply database migrations first"
        )
    if not module.is_active:
        raise BootstrapError(
            f"Module '{INCIDENTS_MODULE_CODE}' is inactive"
        )

    if not is_module_enabled(session, company.id, INCIDENTS_MODULE_CODE):
        activate_module(
            session,
            company,
            module,
            actor_user_id=actor_user_id,
        )


def bootstrap_initial_company(
    session: Session,
    *,
    admin_email: str,
    admin_password: str | None,
    agent_email: str,
    agent_password: str | None,
    admin_full_name: str = ADMIN_FULL_NAME,
    agent_full_name: str = AGENT_FULL_NAME,
) -> BootstrapResult:
    normalized_admin_email = normalize_email(admin_email)
    normalized_agent_email = normalize_email(agent_email)
    if normalized_admin_email == normalized_agent_email:
        raise BootstrapError("Administrator and AI Agent emails must differ")

    company = _get_or_create_company(session)
    set_current_company_id(session, company.id)

    catalog = _load_permission_catalog(session)
    all_permission_ids = {
        permission.id for permission in catalog.values()
    }
    agent_permission_ids = {
        catalog[code].id for code in AGENT_PERMISSION_CODES
    }

    admin_user = _get_or_create_user(
        session,
        email=normalized_admin_email,
        password=admin_password,
        full_name=admin_full_name,
        label="Administrator",
    )
    agent_user = _get_or_create_user(
        session,
        email=normalized_agent_email,
        password=agent_password,
        full_name=agent_full_name,
        label="NARA AI Agent",
    )

    admin_membership = _get_or_create_membership(
        session,
        user=admin_user,
        company=company,
        label="Administrator",
    )
    agent_membership = _get_or_create_membership(
        session,
        user=agent_user,
        company=company,
        label="NARA AI Agent",
    )

    admin_role = _get_or_create_role(
        session,
        company=company,
        name=ADMIN_ROLE_NAME,
        description="Full company administration",
        expected_permission_ids=all_permission_ids,
    )
    agent_role = _get_or_create_role(
        session,
        company=company,
        name=AGENT_ROLE_NAME,
        description="Incident intake and analysis only",
        expected_permission_ids=agent_permission_ids,
    )

    _ensure_role_assignment(
        session,
        membership=admin_membership,
        role=admin_role,
        actor_user_id=admin_user.id,
    )
    _ensure_role_assignment(
        session,
        membership=agent_membership,
        role=agent_role,
        actor_user_id=admin_user.id,
    )
    _ensure_incidents_module(
        session,
        company=company,
        actor_user_id=admin_user.id,
    )

    admin_permissions = get_effective_permission_codes(
        session,
        admin_membership,
    )
    if admin_permissions != set(catalog):
        raise BootstrapError(
            "Administrator effective permissions do not match the catalog"
        )

    agent_permissions = get_effective_permission_codes(
        session,
        agent_membership,
    )
    if agent_permissions != AGENT_PERMISSION_CODES:
        raise BootstrapError(
            "NARA AI Agent effective permissions do not match the required set"
        )

    return BootstrapResult(
        company=company,
        admin_user=admin_user,
        agent_user=agent_user,
        admin_membership=admin_membership,
        agent_membership=agent_membership,
        admin_role=admin_role,
        agent_role=agent_role,
    )
