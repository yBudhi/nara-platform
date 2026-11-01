import os
import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.module import Module, ModuleDependency
from app.models.rbac import Role
from app.models.user import User
from app.services.audit import record_audit_event
from app.services.modules import (
    ModuleActivationError,
    activate_module,
    deactivate_module,
)
from app.services.organization import create_company
from app.services.rbac import (
    assign_role_to_membership,
    revoke_role_from_membership,
)


def _create_user(db_session, *, email: str = "actor@example.test") -> User:
    user = User(email=email, hashed_password="password")
    db_session.add(user)
    db_session.commit()
    return user


def _create_company(db_session, *, slug: str = "audit-company") -> Company:
    company = Company(name=slug.replace("-", " ").title(), slug=slug)
    db_session.add(company)
    db_session.commit()
    return company


def _create_membership(db_session, user: User, company: Company):
    membership = CompanyMembership(user_id=user.id, company_id=company.id)
    db_session.add(membership)
    db_session.commit()
    return membership


def _create_role(db_session, company: Company, *, name: str = "Administrator") -> Role:
    role = Role(company_id=company.id, name=name)
    db_session.add(role)
    db_session.commit()
    return role


def _create_module(
    db_session,
    *,
    code: str,
    is_active: bool = True,
) -> Module:
    module = Module(
        code=code,
        name=code.replace(".", " ").title(),
        version="1.0.0",
        is_core=False,
        is_active=is_active,
    )
    db_session.add(module)
    db_session.commit()
    return module


def _audit_entries(db_session, action: str | None = None) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    return list(db_session.scalars(query))


def test_company_created_writes_audit_event(db_session):
    actor = _create_user(db_session)

    company = create_company(
        db_session,
        name="Acme Corp",
        slug="acme-audit",
        actor_user_id=actor.id,
        request_id="req-company-created",
    )
    db_session.commit()

    entries = _audit_entries(db_session, "company.created")
    assert len(entries) == 1

    entry = entries[0]
    assert entry.company_id == company.id
    assert entry.actor_user_id == actor.id
    assert entry.entity_type == "company"
    assert entry.entity_id == company.id
    assert entry.before_data is None
    assert entry.after_data == {
        "name": "Acme Corp",
        "slug": "acme-audit",
        "is_active": True,
    }
    assert entry.request_id == "req-company-created"


def test_rbac_role_assignment_and_revocation_write_audit_events(db_session):
    actor = _create_user(db_session)
    company = _create_company(db_session, slug="rbac-audit")
    membership = _create_membership(db_session, actor, company)
    role = _create_role(db_session, company)

    assignment = assign_role_to_membership(
        db_session,
        membership,
        role,
        actor_user_id=actor.id,
        request_id="req-role-assigned",
    )
    assignment_id = assignment.id
    db_session.commit()

    assigned = _audit_entries(db_session, "rbac.user_role.assigned")
    assert len(assigned) == 1
    assert assigned[0].company_id == company.id
    assert assigned[0].actor_user_id == actor.id
    assert assigned[0].entity_type == "user_role"
    assert assigned[0].entity_id == assignment_id
    assert assigned[0].after_data == {
        "membership_id": str(membership.id),
        "role_id": str(role.id),
        "company_id": str(company.id),
    }
    assert assigned[0].request_id == "req-role-assigned"

    revoke_role_from_membership(
        db_session,
        membership,
        role,
        actor_user_id=actor.id,
        request_id="req-role-revoked",
    )
    db_session.commit()

    revoked = _audit_entries(db_session, "rbac.user_role.revoked")
    assert len(revoked) == 1
    assert revoked[0].company_id == company.id
    assert revoked[0].actor_user_id == actor.id
    assert revoked[0].entity_id == assignment_id
    assert revoked[0].before_data == {
        "membership_id": str(membership.id),
        "role_id": str(role.id),
        "company_id": str(company.id),
    }
    assert revoked[0].after_data is None


def test_module_activation_and_deactivation_write_audit_events(db_session):
    actor = _create_user(db_session)
    company = _create_company(db_session, slug="module-audit")
    module = _create_module(db_session, code="reports.core")

    company_module = activate_module(
        db_session,
        company,
        module,
        actor_user_id=actor.id,
        request_id="req-module-activated",
    )
    db_session.commit()

    activated = _audit_entries(db_session, "module.activated")
    assert len(activated) == 1
    assert activated[0].company_id == company.id
    assert activated[0].actor_user_id == actor.id
    assert activated[0].entity_type == "company_module"
    assert activated[0].entity_id == company_module.id
    assert activated[0].before_data is None
    assert activated[0].after_data == {
        "module_id": str(module.id),
        "module_code": "reports.core",
        "is_enabled": True,
    }

    deactivate_module(
        db_session,
        company,
        module,
        actor_user_id=actor.id,
        request_id="req-module-deactivated",
    )
    db_session.commit()

    deactivated = _audit_entries(db_session, "module.deactivated")
    assert len(deactivated) == 1
    assert deactivated[0].company_id == company.id
    assert deactivated[0].actor_user_id == actor.id
    assert deactivated[0].entity_id == company_module.id
    assert deactivated[0].before_data == {"is_enabled": True}
    assert deactivated[0].after_data == {
        "module_id": str(module.id),
        "module_code": "reports.core",
        "is_enabled": False,
    }


def test_failed_operations_do_not_write_audit_events(db_session):
    actor = _create_user(db_session)
    company = _create_company(db_session, slug="failed-audit")
    base = _create_module(db_session, code="billing.base")
    invoices = _create_module(db_session, code="billing.invoices")
    db_session.add(
        ModuleDependency(
            parent_module_id=invoices.id,
            dependency_module_id=base.id,
        )
    )
    db_session.commit()

    with pytest.raises(ModuleActivationError):
        activate_module(
            db_session,
            company,
            invoices,
            actor_user_id=actor.id,
        )
    db_session.rollback()

    assert _audit_entries(db_session, "module.activated") == []

    activate_module(
        db_session,
        company,
        base,
        actor_user_id=actor.id,
    )
    db_session.commit()

    with pytest.raises(ModuleActivationError):
        activate_module(
            db_session,
            company,
            base,
            actor_user_id=actor.id,
        )
    db_session.rollback()

    assert len(_audit_entries(db_session, "module.activated")) == 1


def test_audit_record_rolls_back_with_business_transaction(db_session):
    actor = _create_user(db_session)
    company = _create_company(db_session, slug="rollback-audit")
    membership = _create_membership(db_session, actor, company)
    role = _create_role(db_session, company)

    assign_role_to_membership(
        db_session,
        membership,
        role,
        actor_user_id=actor.id,
    )
    assert len(_audit_entries(db_session, "rbac.user_role.assigned")) == 1

    db_session.rollback()

    assert _audit_entries(db_session, "rbac.user_role.assigned") == []


def test_audit_service_sanitizes_sensitive_fields(db_session):
    company = _create_company(db_session, slug="sanitize-audit")

    entry = record_audit_event(
        db_session,
        company_id=company.id,
        action="test.sensitive",
        entity_type="test",
        entity_id=uuid.uuid4(),
        after_data={
            "password": "should-not-be-stored",
            "nested": {"token": "also-removed", "value": 1},
        },
        metadata={"api_key": "removed", "safe": "kept"},
    )
    db_session.commit()
    db_session.refresh(entry)

    assert entry.after_data == {"nested": {"value": 1}}
    assert entry.audit_metadata == {"safe": "kept"}


def _pg_connection():
    url = os.getenv("NARA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NARA_TEST_DATABASE_URL is not set")

    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg is not installed")

    from sqlalchemy.engine import make_url

    dsn = make_url(url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    return psycopg.connect(dsn)


@pytest.fixture
def pg_runtime_connection():
    connection = _pg_connection()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def pg_runtime_session():
    url = os.getenv("NARA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NARA_TEST_DATABASE_URL is not set")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _pg_set_company_context(connection, company_id: uuid.UUID) -> None:
    connection.execute(
        "SELECT set_config('app.current_company_id', %s, true)",
        (str(company_id),),
    )


def _pg_create_company(connection, company_id: uuid.UUID) -> None:
    connection.execute(
        """
        INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, true, now(), now())
        """,
        (company_id, f"Audit {company_id}", f"pg-audit-{company_id}"),
    )


def _pg_insert_audit(
    connection,
    audit_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    action: str = "test.event",
) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs
            (id, company_id, action, entity_type, entity_id, created_at)
        VALUES (%s, %s, %s, %s, %s, now())
        """,
        (audit_id, company_id, action, "test", uuid.uuid4()),
    )


def _pg_expect_denied(connection, sql: str, params):
    import psycopg

    connection.execute("SAVEPOINT expected_denial")
    try:
        connection.execute(sql, params)
    except psycopg.errors.InsufficientPrivilege as exc:
        connection.execute("ROLLBACK TO SAVEPOINT expected_denial")
        return exc

    connection.execute("RELEASE SAVEPOINT expected_denial")
    raise AssertionError("Expected PostgreSQL to deny the operation")


def test_audit_service_inserts_with_current_company_context(pg_runtime_session):
    from app.core.database import set_current_company_id

    session = pg_runtime_session
    company = Company(name="PG Context", slug=f"pg-context-{uuid.uuid4()}")
    session.add(company)
    session.flush()

    set_current_company_id(session, company.id)
    entry = record_audit_event(
        session,
        company_id=company.id,
        action="test.context",
        entity_type="test",
        entity_id=uuid.uuid4(),
    )
    session.flush()

    loaded = session.scalar(select(AuditLog).where(AuditLog.id == entry.id))
    assert loaded is not None
    assert loaded.company_id == company.id


def test_rls_blocks_cross_company_audit_access(pg_runtime_connection):
    connection = pg_runtime_connection
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    _pg_create_company(connection, company_a)
    _pg_create_company(connection, company_b)

    _pg_set_company_context(connection, company_a)
    audit_a = uuid.uuid4()
    _pg_insert_audit(connection, audit_a, company_a)

    audit_b = uuid.uuid4()
    denied = _pg_expect_denied(
        connection,
        """
        INSERT INTO audit_logs
            (id, company_id, action, entity_type, entity_id, created_at)
        VALUES (%s, %s, %s, %s, %s, now())
        """,
        (audit_b, company_b, "test.event", "test", uuid.uuid4()),
    )
    assert "row-level security" in str(denied)

    _pg_set_company_context(connection, company_b)
    _pg_insert_audit(connection, audit_b, company_b)

    _pg_set_company_context(connection, company_a)
    visible_ids = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM audit_logs WHERE company_id IN (%s, %s)",
            (company_a, company_b),
        ).fetchall()
    }
    assert visible_ids == {audit_a}


def test_runtime_cannot_update_or_delete_audit_logs(pg_runtime_connection):
    import psycopg

    connection = pg_runtime_connection
    company_id = uuid.uuid4()
    _pg_create_company(connection, company_id)
    _pg_set_company_context(connection, company_id)

    audit_id = uuid.uuid4()
    _pg_insert_audit(connection, audit_id, company_id)

    update_denied = _pg_expect_denied(
        connection,
        "UPDATE audit_logs SET action = 'tampered' WHERE id = %s",
        (audit_id,),
    )
    delete_denied = _pg_expect_denied(
        connection,
        "DELETE FROM audit_logs WHERE id = %s",
        (audit_id,),
    )

    assert isinstance(update_denied, psycopg.errors.InsufficientPrivilege)
    assert isinstance(delete_denied, psycopg.errors.InsufficientPrivilege)

    row = connection.execute(
        "SELECT action FROM audit_logs WHERE id = %s AND company_id = %s",
        (audit_id, company_id),
    ).fetchone()
    assert row == ("test.event",)


def test_runtime_has_only_select_and_insert_on_audit_logs(pg_runtime_connection):
    connection = pg_runtime_connection
    privileges = {
        row[0]
        for row in connection.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'audit_logs'
              AND grantee = current_user
            """
        ).fetchall()
    }
    assert privileges == {"SELECT", "INSERT"}
