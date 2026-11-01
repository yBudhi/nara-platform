import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import app.bootstrap as bootstrap_cli
from app.core.security import hash_password, verify_password
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.module import CompanyModule, Module
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.services.bootstrap import (
    ADMIN_ROLE_NAME,
    AGENT_PERMISSION_CODES,
    AGENT_ROLE_NAME,
    COMPANY_NAME,
    COMPANY_SLUG,
    BootstrapError,
    bootstrap_initial_company,
)
from app.services.modules import is_module_enabled
from app.services.rbac import get_effective_permission_codes

ADMIN_EMAIL = "admin@we-playground.test"
AGENT_EMAIL = "agent@we-playground.test"
ADMIN_PASSWORD = "Admin-Passw0rd-123!"
AGENT_PASSWORD = "Agent-Passw0rd-123!"
PERMISSION_CODES = (
    "company.read",
    "company.manage",
    "members.read",
    "members.manage",
    "roles.read",
    "roles.manage",
    "modules.read",
    "modules.manage",
    "audit.read",
    "incidents.read",
    "incidents.create",
    "incidents.update",
    "incidents.resolve",
    "incidents.analyze",
)


def _seed_catalog(
    db_session,
    *,
    permission_codes=PERMISSION_CODES,
    with_module: bool = True,
) -> None:
    for code in permission_codes:
        resource, action = code.split(".", 1)
        db_session.add(
            Permission(
                code=code,
                description=code,
                resource=resource,
                action=action,
                module_code="incidents" if resource == "incidents" else None,
            )
        )

    if with_module:
        db_session.add(
            Module(
                code="incidents",
                name="Incident Management",
                description="Incident capture and analysis",
                version="1.0.0",
                is_active=True,
            )
        )

    db_session.commit()


def _bootstrap(
    db_session,
    *,
    admin_email: str = ADMIN_EMAIL,
    admin_password: str | None = ADMIN_PASSWORD,
    agent_email: str = AGENT_EMAIL,
    agent_password: str | None = AGENT_PASSWORD,
):
    return bootstrap_initial_company(
        db_session,
        admin_email=admin_email,
        admin_password=admin_password,
        agent_email=agent_email,
        agent_password=agent_password,
    )


def _count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def test_bootstrap_creates_initial_company_and_access(db_session):
    _seed_catalog(db_session)

    result = _bootstrap(
        db_session,
        admin_email="ADMIN@WE-PLAYGROUND.TEST",
        agent_email="AGENT@WE-PLAYGROUND.TEST",
    )
    db_session.commit()

    assert result.company.name == COMPANY_NAME
    assert result.company.slug == COMPANY_SLUG
    assert result.admin_user.email == ADMIN_EMAIL
    assert result.agent_user.email == AGENT_EMAIL
    assert verify_password(ADMIN_PASSWORD, result.admin_user.hashed_password)
    assert verify_password(AGENT_PASSWORD, result.agent_user.hashed_password)
    assert result.admin_membership.is_active is True
    assert result.agent_membership.is_active is True
    assert result.admin_role.name == ADMIN_ROLE_NAME
    assert result.agent_role.name == AGENT_ROLE_NAME
    assert get_effective_permission_codes(
        db_session,
        result.admin_membership,
    ) == set(PERMISSION_CODES)
    assert get_effective_permission_codes(
        db_session,
        result.agent_membership,
    ) == AGENT_PERMISSION_CODES
    assert is_module_enabled(db_session, result.company.id, "incidents") is True

    assert _count(db_session, Company) == 1
    assert _count(db_session, User) == 2
    assert _count(db_session, CompanyMembership) == 2
    assert _count(db_session, Role) == 2
    assert _count(db_session, UserRole) == 2
    assert _count(db_session, CompanyModule) == 1
    assert _count(db_session, RolePermission) == (
        len(PERMISSION_CODES) + len(AGENT_PERMISSION_CODES)
    )


def test_bootstrap_is_idempotent_and_preserves_passwords(db_session):
    _seed_catalog(db_session)
    first = _bootstrap(db_session)
    db_session.commit()

    original_admin_hash = first.admin_user.hashed_password
    original_agent_hash = first.agent_user.hashed_password
    original_ids = (
        first.company.id,
        first.admin_user.id,
        first.agent_user.id,
        first.admin_membership.id,
        first.agent_membership.id,
        first.admin_role.id,
        first.agent_role.id,
    )

    second = _bootstrap(
        db_session,
        admin_password="Different-Admin-Passw0rd!",
        agent_password="Different-Agent-Passw0rd!",
    )
    db_session.commit()

    assert (
        second.company.id,
        second.admin_user.id,
        second.agent_user.id,
        second.admin_membership.id,
        second.agent_membership.id,
        second.admin_role.id,
        second.agent_role.id,
    ) == original_ids
    assert second.admin_user.hashed_password == original_admin_hash
    assert second.agent_user.hashed_password == original_agent_hash
    assert verify_password(ADMIN_PASSWORD, second.admin_user.hashed_password)
    assert verify_password(AGENT_PASSWORD, second.agent_user.hashed_password)
    assert _count(db_session, Company) == 1
    assert _count(db_session, User) == 2
    assert _count(db_session, CompanyMembership) == 2
    assert _count(db_session, Role) == 2
    assert _count(db_session, UserRole) == 2
    assert _count(db_session, CompanyModule) == 1
    assert _count(db_session, RolePermission) == (
        len(PERMISSION_CODES) + len(AGENT_PERMISSION_CODES)
    )


def test_bootstrap_restores_missing_agent_permission(db_session):
    _seed_catalog(db_session)
    result = _bootstrap(db_session)
    db_session.commit()

    permission = db_session.scalar(
        select(Permission).where(Permission.code == "incidents.analyze")
    )
    assignment = db_session.scalar(
        select(RolePermission).where(
            RolePermission.role_id == result.agent_role.id,
            RolePermission.permission_id == permission.id,
        )
    )
    db_session.delete(assignment)
    db_session.commit()

    _bootstrap(db_session, admin_password=None, agent_password=None)
    db_session.commit()

    assert get_effective_permission_codes(
        db_session,
        result.agent_membership,
    ) == AGENT_PERMISSION_CODES


def test_bootstrap_rejects_extra_agent_permission(db_session):
    _seed_catalog(db_session)
    result = _bootstrap(db_session)
    db_session.commit()

    permission = db_session.scalar(
        select(Permission).where(Permission.code == "incidents.update")
    )
    db_session.add(
        RolePermission(
            role_id=result.agent_role.id,
            permission_id=permission.id,
        )
    )
    db_session.commit()

    with pytest.raises(BootstrapError, match="incidents.update"):
        _bootstrap(db_session, admin_password=None, agent_password=None)
    db_session.rollback()


def test_bootstrap_reenables_incidents_module(db_session):
    _seed_catalog(db_session)
    result = _bootstrap(db_session)
    db_session.commit()

    module = db_session.scalar(
        select(Module).where(Module.code == "incidents")
    )
    company_module = db_session.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == result.company.id,
            CompanyModule.module_id == module.id,
        )
    )
    company_module.is_enabled = False
    db_session.commit()
    assert is_module_enabled(db_session, result.company.id, "incidents") is False

    _bootstrap(db_session, admin_password=None, agent_password=None)
    db_session.commit()

    assert is_module_enabled(db_session, result.company.id, "incidents") is True
    assert _count(db_session, CompanyModule) == 1


def test_bootstrap_requires_all_agent_permissions(db_session):
    _seed_catalog(
        db_session,
        permission_codes=tuple(
            code
            for code in PERMISSION_CODES
            if code != "incidents.create"
        ),
    )

    with pytest.raises(BootstrapError, match="incidents.create"):
        _bootstrap(db_session)
    db_session.rollback()


def test_bootstrap_requires_registered_incidents_module(db_session):
    _seed_catalog(db_session, with_module=False)

    with pytest.raises(BootstrapError, match="incidents"):
        _bootstrap(db_session)
    db_session.rollback()


def test_bootstrap_rejects_inactive_user(db_session):
    _seed_catalog(db_session)
    db_session.add(
        User(
            email=ADMIN_EMAIL,
            hashed_password=hash_password("Existing-Passw0rd!"),
            is_active=False,
        )
    )
    db_session.commit()

    with pytest.raises(BootstrapError, match="inactive"):
        _bootstrap(db_session)
    db_session.rollback()


def test_bootstrap_rejects_inactive_membership(db_session):
    _seed_catalog(db_session)
    result = _bootstrap(db_session)
    db_session.commit()

    result.agent_membership.is_active = False
    db_session.commit()

    with pytest.raises(BootstrapError, match="membership.*inactive"):
        _bootstrap(db_session, admin_password=None, agent_password=None)
    db_session.rollback()


def test_bootstrap_rejects_inactive_role(db_session):
    _seed_catalog(db_session)
    result = _bootstrap(db_session)
    db_session.commit()

    result.agent_role.is_active = False
    db_session.commit()

    with pytest.raises(BootstrapError, match="nara_ai_agent.*inactive"):
        _bootstrap(db_session, admin_password=None, agent_password=None)
    db_session.rollback()


def test_bootstrap_rejects_company_slug_conflict(db_session):
    _seed_catalog(db_session)
    db_session.add(Company(name="Another Company", slug=COMPANY_SLUG))
    db_session.commit()

    with pytest.raises(BootstrapError, match="belongs to"):
        _bootstrap(db_session)
    db_session.rollback()


def test_bootstrap_rejects_company_name_conflict(db_session):
    _seed_catalog(db_session)
    db_session.add(Company(name=COMPANY_NAME, slug="another-company"))
    db_session.commit()

    with pytest.raises(BootstrapError, match="already exists"):
        _bootstrap(db_session)
    db_session.rollback()


def test_bootstrap_rejects_duplicate_user_emails(db_session):
    _seed_catalog(db_session)

    with pytest.raises(BootstrapError, match="must differ"):
        _bootstrap(
            db_session,
            admin_email="same@example.test",
            agent_email="SAME@example.test",
        )
    db_session.rollback()


def test_bootstrap_requires_passwords_for_new_users(db_session):
    _seed_catalog(db_session)

    with pytest.raises(BootstrapError, match="password is required"):
        _bootstrap(db_session, admin_password=None)
    db_session.rollback()


def test_cli_bootstraps_and_skips_passwords_for_existing_users(
    db_session,
    monkeypatch,
    capsys,
):
    _seed_catalog(db_session)
    session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(
        bootstrap_cli,
        "get_session_factory",
        lambda: session_factory,
    )
    passwords = iter(
        [
            ADMIN_PASSWORD,
            ADMIN_PASSWORD,
            AGENT_PASSWORD,
            AGENT_PASSWORD,
        ]
    )
    monkeypatch.setattr(
        bootstrap_cli.getpass,
        "getpass",
        lambda _prompt: next(passwords),
    )

    exit_code = bootstrap_cli.main(
        [
            "--admin-email",
            ADMIN_EMAIL,
            "--agent-email",
            AGENT_EMAIL,
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr()
    assert ADMIN_PASSWORD not in output.out + output.err
    assert AGENT_PASSWORD not in output.out + output.err
    assert output.out.count("Bootstrap complete") == 1

    def _unexpected_prompt(_prompt):
        raise AssertionError("existing users must not be prompted for passwords")

    monkeypatch.setattr(
        bootstrap_cli.getpass,
        "getpass",
        _unexpected_prompt,
    )

    exit_code = bootstrap_cli.main(
        [
            "--admin-email",
            ADMIN_EMAIL,
            "--agent-email",
            AGENT_EMAIL,
        ]
    )

    assert exit_code == 0
    assert _count(db_session, User) == 2


def test_cli_rejects_duplicate_emails_without_prompting(
    db_session,
    monkeypatch,
    capsys,
):
    _seed_catalog(db_session)
    session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(
        bootstrap_cli,
        "get_session_factory",
        lambda: session_factory,
    )

    def _unexpected_prompt(_prompt):
        raise AssertionError("duplicate emails must fail before prompting")

    monkeypatch.setattr(
        bootstrap_cli.getpass,
        "getpass",
        _unexpected_prompt,
    )

    exit_code = bootstrap_cli.main(
        [
            "--admin-email",
            "same@example.test",
            "--agent-email",
            "SAME@example.test",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr()
    assert "must differ" in output.err
