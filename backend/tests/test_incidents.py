import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.database import set_current_company_id
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.incident import Incident, IncidentStatus
from app.models.membership import CompanyMembership
from app.models.module import Module
from app.models.rbac import Permission, Role
from app.models.user import User
from app.services.incidents import (
    IncidentStatusTransitionError,
    create_incident,
    get_incident_analytics,
    resolve_incident,
    update_incident,
)
from app.services.modules import activate_module
from app.services.rbac import assign_role_to_membership

PASSWORD = "S3curePassw0rd!"
ALL_INCIDENT_PERMISSIONS = {
    "incidents.read",
    "incidents.create",
    "incidents.update",
    "incidents.resolve",
    "incidents.analyze",
}


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


def _create_user(db_session, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        full_name="Incident User",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _create_company(db_session, *, slug: str) -> Company:
    company = Company(name=slug.replace("-", " ").title(), slug=slug)
    db_session.add(company)
    db_session.commit()
    return company


def _create_membership(db_session, user: User, company: Company):
    membership = CompanyMembership(user_id=user.id, company_id=company.id)
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
        module_code="incidents",
    )
    db_session.add(permission)
    db_session.flush()
    return permission


def _create_role(
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


def _get_or_create_incident_module(db_session) -> Module:
    module = db_session.scalar(
        select(Module).where(Module.code == "incidents")
    )
    if module is not None:
        return module

    module = Module(
        code="incidents",
        name="Incident Management",
        description="Incident capture, resolution, and analytics",
        version="1.0.0",
        is_core=False,
        is_active=True,
    )
    db_session.add(module)
    db_session.commit()
    return module


def _setup_tenant(
    db_session,
    *,
    email: str,
    slug: str,
    permissions: set[str] | None = None,
    enable_module: bool = True,
):
    user = _create_user(db_session, email=email)
    company = _create_company(db_session, slug=slug)
    membership = _create_membership(db_session, user, company)
    role = _create_role(
        db_session,
        company,
        name="Incident Administrator",
        permission_codes=(
            permissions
            if permissions is not None
            else ALL_INCIDENT_PERMISSIONS
        ),
    )
    assign_role_to_membership(db_session, membership, role)
    db_session.commit()

    module = _get_or_create_incident_module(db_session)
    if enable_module:
        activate_module(db_session, company, module)
        db_session.commit()

    headers = {
        "Authorization": f"Bearer {create_access_token(user.id)}",
        "X-Company-ID": str(company.id),
    }
    return user, company, membership, role, module, headers


def _incident_payload(**overrides) -> dict:
    payload = {
        "title": "Pump pressure loss",
        "description": "Pump pressure dropped below the operating threshold.",
        "category": "Safety",
        "location": "Plant A",
        "occurred_at": datetime.now(UTC).isoformat(),
        "priority": "high",
        "impact": "Production line slowdown",
    }
    payload.update(overrides)
    return payload


def _create_incident_via_service(
    db_session,
    user: User,
    company: Company,
    *,
    title: str = "Service incident",
    description: str = "Service incident description",
    category: str = "Safety",
    location: str = "Plant A",
    occurred_at: datetime | None = None,
    priority: str | None = "high",
    impact: str | None = None,
):
    incident = create_incident(
        db_session,
        company_id=company.id,
        reported_by_user_id=user.id,
        title=title,
        description=description,
        category=category,
        location=location,
        occurred_at=occurred_at or datetime.now(UTC),
        priority=priority,
        impact=impact,
        actor_user_id=user.id,
    )
    db_session.commit()
    return incident


def test_create_incident_generates_company_scoped_references(client, db_session):
    user_a, company_a, _, _, _, headers_a = _setup_tenant(
        db_session,
        email="create-a@example.test",
        slug="create-a",
    )
    _, company_b, _, _, _, headers_b = _setup_tenant(
        db_session,
        email="create-b@example.test",
        slug="create-b",
    )

    first = client.post("/incidents", json=_incident_payload(), headers=headers_a)
    second = client.post(
        "/incidents",
        json=_incident_payload(title="Second incident"),
        headers=headers_a,
    )
    other_company = client.post(
        "/incidents",
        json=_incident_payload(),
        headers=headers_b,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert other_company.status_code == 201
    assert first.json()["incident_number"] == "INC-000001"
    assert second.json()["incident_number"] == "INC-000002"
    assert other_company.json()["incident_number"] == "INC-000001"
    assert first.json()["company_id"] == str(company_a.id)
    assert other_company.json()["company_id"] == str(company_b.id)
    assert first.json()["reported_by_user_id"] == str(user_a.id)
    assert first.json()["status"] == "open"
    assert first.json()["resolved_at"] is None
    assert "hashed_password" not in first.text

    db_session.add(
        Incident(
            company_id=company_a.id,
            incident_number=first.json()["incident_number"],
            title="Duplicate reference",
            description="Duplicate reference",
            category="Safety",
            location="Plant A",
            occurred_at=datetime.now(UTC),
            reported_at=datetime.now(UTC),
            status=IncidentStatus.OPEN.value,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_list_and_detail_are_tenant_scoped(client, db_session):
    _, company_a, _, _, _, headers_a = _setup_tenant(
        db_session,
        email="scope-a@example.test",
        slug="scope-a",
    )
    _, _, _, _, _, headers_b = _setup_tenant(
        db_session,
        email="scope-b@example.test",
        slug="scope-b",
    )

    incident_a = client.post(
        "/incidents",
        json=_incident_payload(title="Company A incident"),
        headers=headers_a,
    ).json()
    incident_b = client.post(
        "/incidents",
        json=_incident_payload(title="Company B incident"),
        headers=headers_b,
    ).json()

    list_a = client.get("/incidents", headers=headers_a)
    list_b = client.get("/incidents", headers=headers_b)
    cross_company = client.get(
        f"/incidents/{incident_b['id']}",
        headers=headers_a,
    )

    assert list_a.status_code == 200
    assert list_a.json()["total"] == 1
    assert list_a.json()["items"][0]["id"] == incident_a["id"]
    assert list_a.json()["items"][0]["company_id"] == str(company_a.id)
    assert list_b.status_code == 200
    assert list_b.json()["total"] == 1
    assert list_b.json()["items"][0]["id"] == incident_b["id"]
    assert cross_company.status_code == 404


def test_permission_enforcement_on_incident_endpoints(client, db_session):
    user, company, _, _, _, headers = _setup_tenant(
        db_session,
        email="permissions-incidents@example.test",
        slug="permissions-incidents",
        permissions={"incidents.read"},
    )
    incident = _create_incident_via_service(db_session, user, company)

    read_response = client.get("/incidents", headers=headers)
    create_response = client.post(
        "/incidents",
        json=_incident_payload(),
        headers=headers,
    )
    update_response = client.patch(
        f"/incidents/{incident.id}",
        json={"title": "Updated"},
        headers=headers,
    )
    resolve_response = client.post(
        f"/incidents/{incident.id}/resolve",
        headers=headers,
    )
    analytics_response = client.get(
        "/incidents/analytics/summary",
        headers=headers,
    )

    assert read_response.status_code == 200
    assert create_response.status_code == 403
    assert create_response.json()["detail"] == (
        "Permission required: incidents.create"
    )
    assert update_response.status_code == 403
    assert resolve_response.status_code == 403
    assert analytics_response.status_code == 403


def test_disabled_module_is_rejected(client, db_session):
    _, company, _, _, module, headers = _setup_tenant(
        db_session,
        email="module-disabled@example.test",
        slug="module-disabled",
        enable_module=False,
    )

    disabled = client.get("/incidents", headers=headers)
    assert disabled.status_code == 403
    assert disabled.json()["detail"] == (
        "Module 'incidents' is not enabled for this company"
    )

    activate_module(db_session, company, module)
    db_session.commit()

    enabled = client.get("/incidents", headers=headers)
    assert enabled.status_code == 200


def test_update_status_transitions_and_company_id_protection(
    client,
    db_session,
):
    _, _, _, _, _, headers = _setup_tenant(
        db_session,
        email="transitions@example.test",
        slug="transitions",
    )
    created = client.post(
        "/incidents",
        json=_incident_payload(),
        headers=headers,
    ).json()

    in_progress = client.patch(
        f"/incidents/{created['id']}",
        json={"title": "Updated title", "status": "in_progress"},
        headers=headers,
    )
    invalid_close = client.patch(
        f"/incidents/{created['id']}",
        json={"status": "closed"},
        headers=headers,
    )
    resolved = client.patch(
        f"/incidents/{created['id']}",
        json={"status": "resolved"},
        headers=headers,
    )
    closed = client.patch(
        f"/incidents/{created['id']}",
        json={"status": "closed"},
        headers=headers,
    )
    reopen = client.patch(
        f"/incidents/{created['id']}",
        json={"status": "open"},
        headers=headers,
    )
    company_change = client.patch(
        f"/incidents/{created['id']}",
        json={"company_id": str(uuid.uuid4())},
        headers=headers,
    )
    empty_update = client.patch(
        f"/incidents/{created['id']}",
        json={},
        headers=headers,
    )

    assert in_progress.status_code == 200
    assert in_progress.json()["status"] == "in_progress"
    assert in_progress.json()["title"] == "Updated title"
    assert invalid_close.status_code == 409
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"] is not None
    assert resolved.json()["resolution_duration_seconds"] >= 0
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert reopen.status_code == 409
    assert company_change.status_code == 422
    assert empty_update.status_code == 422


def test_resolve_calculates_duration_and_rejects_second_resolution(
    client,
    db_session,
):
    user, company, _, _, _, headers = _setup_tenant(
        db_session,
        email="resolve@example.test",
        slug="resolve",
    )
    occurred_at = datetime.now(UTC) - timedelta(hours=2)
    incident = _create_incident_via_service(
        db_session,
        user,
        company,
        occurred_at=occurred_at,
    )

    resolved = client.post(
        f"/incidents/{incident.id}/resolve",
        json={
            "root_cause": "Seal failure",
            "resolution_action": "Replaced seal",
        },
        headers=headers,
    )
    second = client.post(
        f"/incidents/{incident.id}/resolve",
        headers=headers,
    )

    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    assert body["root_cause"] == "Seal failure"
    assert body["resolution_action"] == "Replaced seal"
    assert 7100 <= body["resolution_duration_seconds"] <= 7300
    assert second.status_code == 409


def test_search_filters_and_pagination(client, db_session):
    user, company, _, _, _, headers = _setup_tenant(
        db_session,
        email="search@example.test",
        slug="search",
    )
    safety_a = _create_incident_via_service(
        db_session,
        user,
        company,
        title="Safety issue at plant A",
        category="Safety",
        location="Plant A",
        priority="high",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    safety_b = _create_incident_via_service(
        db_session,
        user,
        company,
        title="Safety issue at plant B",
        category="Safety",
        location="Plant B",
        priority="low",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    it_a = _create_incident_via_service(
        db_session,
        user,
        company,
        title="Server outage at plant A",
        category="IT",
        location="Plant A",
        priority="high",
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    it_c = _create_incident_via_service(
        db_session,
        user,
        company,
        title="Network issue at plant C",
        category="IT",
        location="Plant C",
        priority=None,
        occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    update_incident(
        db_session,
        safety_b,
        updates={"status": IncidentStatus.IN_PROGRESS},
        actor_user_id=user.id,
    )
    db_session.commit()
    resolve_incident(db_session, it_a, actor_user_id=user.id)
    db_session.commit()

    by_category = client.get(
        "/incidents",
        params={"category": "safety"},
        headers=headers,
    )
    by_location = client.get(
        "/incidents",
        params={"location": "plant a"},
        headers=headers,
    )
    by_status = client.get(
        "/incidents",
        params={"status": "in_progress"},
        headers=headers,
    )
    by_priority = client.get(
        "/incidents",
        params={"priority": "high"},
        headers=headers,
    )
    by_keyword = client.get(
        "/incidents",
        params={"keyword": "server"},
        headers=headers,
    )
    by_date = client.get(
        "/incidents",
        params={
            "date_from": datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
            "date_to": datetime(2026, 2, 28, tzinfo=UTC).isoformat(),
        },
        headers=headers,
    )
    paginated = client.get(
        "/incidents",
        params={"limit": 2, "offset": 2},
        headers=headers,
    )

    assert by_category.status_code == 200
    assert by_category.json()["total"] == 2
    assert {item["id"] for item in by_category.json()["items"]} == {
        str(safety_a.id),
        str(safety_b.id),
    }
    assert by_location.json()["total"] == 2
    assert {item["id"] for item in by_location.json()["items"]} == {
        str(safety_a.id),
        str(it_a.id),
    }
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["id"] == str(safety_b.id)
    assert by_priority.json()["total"] == 2
    assert {item["id"] for item in by_priority.json()["items"]} == {
        str(safety_a.id),
        str(it_a.id),
    }
    assert by_keyword.json()["total"] == 1
    assert by_keyword.json()["items"][0]["id"] == str(it_a.id)
    assert by_date.json()["total"] == 1
    assert by_date.json()["items"][0]["id"] == str(it_a.id)
    assert paginated.status_code == 200
    assert paginated.json()["total"] == 4
    assert len(paginated.json()["items"]) == 2
    assert it_c.id is not None


def test_analytics_aggregation(client, db_session):
    user, company, _, _, _, headers = _setup_tenant(
        db_session,
        email="analytics@example.test",
        slug="analytics",
    )
    open_incident = _create_incident_via_service(
        db_session,
        user,
        company,
        category="Safety",
        location="Plant A",
        priority="high",
    )
    in_progress = _create_incident_via_service(
        db_session,
        user,
        company,
        category="IT",
        location="Plant B",
        priority="low",
    )
    resolved = _create_incident_via_service(
        db_session,
        user,
        company,
        category="IT",
        location="Plant A",
        priority="high",
        occurred_at=datetime.now(UTC) - timedelta(hours=1),
    )
    unspecified = _create_incident_via_service(
        db_session,
        user,
        company,
        category="Safety",
        location="Plant C",
        priority=None,
    )
    update_incident(
        db_session,
        in_progress,
        updates={"status": IncidentStatus.IN_PROGRESS},
        actor_user_id=user.id,
    )
    resolve_incident(db_session, resolved, actor_user_id=user.id)
    db_session.commit()

    response = client.get("/incidents/analytics/summary", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_incidents"] == 4
    assert body["by_status"] == {
        "open": 2,
        "in_progress": 1,
        "resolved": 1,
        "closed": 0,
    }
    assert body["by_category"] == {"Safety": 2, "IT": 2}
    assert body["by_location"] == {"Plant A": 2, "Plant B": 1, "Plant C": 1}
    assert body["by_priority"] == {"high": 2, "low": 1, "unspecified": 1}
    assert body["average_resolution_time_seconds"] == pytest.approx(
        3600,
        abs=15,
    )
    assert open_incident.status == IncidentStatus.OPEN.value
    assert unspecified.priority is None


def test_incident_audit_events_are_written_for_successful_changes(
    db_session,
):
    user, company, _, _, _, _ = _setup_tenant(
        db_session,
        email="audit-incidents@example.test",
        slug="audit-incidents",
    )
    incident = _create_incident_via_service(db_session, user, company)
    update_incident(
        db_session,
        incident,
        updates={"title": "Updated incident title"},
        actor_user_id=user.id,
    )
    db_session.commit()
    resolve_incident(db_session, incident, actor_user_id=user.id)
    db_session.commit()

    entries = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action.like("incident.%"))
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )

    assert [entry.action for entry in entries] == [
        "incident.created",
        "incident.updated",
        "incident.resolved",
    ]
    assert all(entry.company_id == company.id for entry in entries)
    assert all(entry.actor_user_id == user.id for entry in entries)
    assert all(entry.entity_type == "incident" for entry in entries)
    assert all(entry.entity_id == incident.id for entry in entries)
    assert entries[0].before_data is None
    assert entries[0].after_data["incident_number"] == incident.incident_number
    assert entries[1].before_data["title"] != entries[1].after_data["title"]
    assert entries[2].after_data["status"] == "resolved"


def test_invalid_status_transition_does_not_write_audit_event(db_session):
    user, company, _, _, _, _ = _setup_tenant(
        db_session,
        email="invalid-audit@example.test",
        slug="invalid-audit",
    )
    incident = _create_incident_via_service(db_session, user, company)
    before_count = len(
        list(
            db_session.scalars(
                select(AuditLog).where(AuditLog.action.like("incident.%"))
            )
        )
    )

    with pytest.raises(IncidentStatusTransitionError):
        update_incident(
            db_session,
            incident,
            updates={"status": IncidentStatus.CLOSED},
            actor_user_id=user.id,
        )
    db_session.rollback()

    after_count = len(
        list(
            db_session.scalars(
                select(AuditLog).where(AuditLog.action.like("incident.%"))
            )
        )
    )
    assert after_count == before_count


def _pg_connection():
    url = os.getenv("NARA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NARA_TEST_DATABASE_URL is not set")

    import psycopg
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
        (company_id, f"Incident {company_id}", f"pg-incident-{company_id}"),
    )


def _pg_insert_incident(
    connection,
    incident_id: uuid.UUID,
    company_id: uuid.UUID,
    incident_number: str,
) -> None:
    connection.execute(
        """
        INSERT INTO incidents
            (id, company_id, incident_number, title, description, category,
             location, occurred_at, reported_at, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), 'open', now(), now())
        """,
        (
            incident_id,
            company_id,
            incident_number,
            "PG incident",
            "PG incident description",
            "Safety",
            "Plant A",
        ),
    )


def _pg_insert_counter(
    connection,
    company_id: uuid.UUID,
    last_number: int,
) -> None:
    connection.execute(
        "INSERT INTO incident_counters (company_id, last_number) VALUES (%s, %s)",
        (company_id, last_number),
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


def test_postgresql_incident_rls_isolates_companies(pg_runtime_connection):
    import psycopg

    connection = pg_runtime_connection
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    _pg_create_company(connection, company_a)
    _pg_create_company(connection, company_b)

    _pg_set_company_context(connection, company_a)
    incident_a = uuid.uuid4()
    _pg_insert_incident(connection, incident_a, company_a, "INC-000001")

    incident_b = uuid.uuid4()
    denied_insert = _pg_expect_denied(
        connection,
        """
        INSERT INTO incidents
            (id, company_id, incident_number, title, description, category,
             location, occurred_at, reported_at, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), 'open', now(), now())
        """,
        (
            incident_b,
            company_b,
            "INC-000001",
            "Cross tenant",
            "Cross tenant",
            "Safety",
            "Plant B",
        ),
    )
    assert "row-level security" in str(denied_insert)

    _pg_set_company_context(connection, company_b)
    _pg_insert_incident(connection, incident_b, company_b, "INC-000001")

    _pg_set_company_context(connection, company_a)
    visible = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM incidents WHERE company_id IN (%s, %s)",
            (company_a, company_b),
        ).fetchall()
    }
    assert visible == {incident_a}

    _pg_set_company_context(connection, company_b)
    update_result = connection.execute(
        "UPDATE incidents SET title = 'tampered' WHERE id = %s",
        (incident_a,),
    )
    assert update_result.rowcount == 0

    delete_denied = _pg_expect_denied(
        connection,
        "DELETE FROM incidents WHERE id = %s",
        (incident_a,),
    )
    assert isinstance(delete_denied, psycopg.errors.InsufficientPrivilege)

    connection.execute("SAVEPOINT duplicate_incident")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _pg_set_company_context(connection, company_a)
        _pg_insert_incident(
            connection,
            uuid.uuid4(),
            company_a,
            "INC-000001",
        )
    connection.execute("ROLLBACK TO SAVEPOINT duplicate_incident")


def test_postgresql_incident_counter_rls_isolates_companies(
    pg_runtime_connection,
):
    connection = pg_runtime_connection
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    _pg_create_company(connection, company_a)
    _pg_create_company(connection, company_b)

    _pg_set_company_context(connection, company_a)
    _pg_insert_counter(connection, company_a, 1)

    denied_insert = _pg_expect_denied(
        connection,
        "INSERT INTO incident_counters (company_id, last_number) VALUES (%s, %s)",
        (company_b, 1),
    )
    assert "row-level security" in str(denied_insert)

    _pg_set_company_context(connection, company_b)
    _pg_insert_counter(connection, company_b, 1)

    _pg_set_company_context(connection, company_a)
    visible = {
        row[0]
        for row in connection.execute(
            """
            SELECT company_id
            FROM incident_counters
            WHERE company_id IN (%s, %s)
            """,
            (company_a, company_b),
        ).fetchall()
    }
    assert visible == {company_a}

    _pg_set_company_context(connection, company_b)
    update_other = connection.execute(
        "UPDATE incident_counters SET last_number = 99 WHERE company_id = %s",
        (company_a,),
    )
    update_own = connection.execute(
        "UPDATE incident_counters SET last_number = 2 WHERE company_id = %s",
        (company_b,),
    )
    assert update_other.rowcount == 0
    assert update_own.rowcount == 1


def test_postgresql_incident_rls_and_runtime_privileges(pg_runtime_connection):
    connection = pg_runtime_connection
    rls = connection.execute(
        """
        SELECT relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE oid = 'public.incidents'::regclass
        """
    ).fetchone()
    assert rls == (True, True)

    policies = {
        row[0]
        for row in connection.execute(
            """
            SELECT cmd
            FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'incidents'
            """
        ).fetchall()
    }
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= policies

    owner = connection.execute(
        """
        SELECT tableowner
        FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'incidents'
        """
    ).fetchone()[0]
    assert owner == "nara_owner"

    counter_rls = connection.execute(
        """
        SELECT relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE oid = 'public.incident_counters'::regclass
        """
    ).fetchone()
    assert counter_rls == (True, True)

    counter_policies = {
        row[0]
        for row in connection.execute(
            """
            SELECT cmd
            FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'incident_counters'
            """
        ).fetchall()
    }
    assert counter_policies == {"SELECT", "INSERT", "UPDATE"}

    counter_owner = connection.execute(
        """
        SELECT tableowner
        FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'incident_counters'
        """
    ).fetchone()[0]
    assert counter_owner == "nara_owner"

    incident_privileges = {
        row[0]
        for row in connection.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'incidents'
              AND grantee = current_user
            """
        ).fetchall()
    }
    assert incident_privileges == {"SELECT", "INSERT", "UPDATE"}

    counter_privileges = {
        row[0]
        for row in connection.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'incident_counters'
              AND grantee = current_user
            """
        ).fetchall()
    }
    assert counter_privileges == {"SELECT", "INSERT", "UPDATE"}

    permission_privileges = {
        row[0]
        for row in connection.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'permissions'
              AND grantee = current_user
            """
        ).fetchall()
    }
    assert permission_privileges == {"SELECT"}


def test_postgresql_incident_seed_and_numbering(pg_runtime_session):
    session = pg_runtime_session
    codes = set(
        session.scalars(
            text("SELECT code FROM permissions WHERE code LIKE 'incidents.%'")
        ).all()
    )
    assert codes == {
        "incidents.read",
        "incidents.create",
        "incidents.update",
        "incidents.resolve",
        "incidents.analyze",
    }
    module = session.execute(
        text("SELECT name FROM modules WHERE code = 'incidents'")
    ).fetchone()
    assert module == ("Incident Management",)

    company = Company(name="PG Incidents", slug=f"pg-incidents-{uuid.uuid4()}")
    session.add(company)
    session.flush()
    set_current_company_id(session, company.id)

    first = create_incident(
        session,
        company_id=company.id,
        reported_by_user_id=None,
        title="First PG incident",
        description="First PG incident",
        category="Safety",
        location="Plant A",
        occurred_at=datetime.now(UTC),
    )
    second = create_incident(
        session,
        company_id=company.id,
        reported_by_user_id=None,
        title="Second PG incident",
        description="Second PG incident",
        category="Safety",
        location="Plant A",
        occurred_at=datetime.now(UTC),
    )
    session.flush()

    assert first.incident_number == "INC-000001"
    assert second.incident_number == "INC-000002"
    analytics = get_incident_analytics(session, company_id=company.id)
    assert analytics["total_incidents"] == 2
    assert analytics["by_status"]["open"] == 2
