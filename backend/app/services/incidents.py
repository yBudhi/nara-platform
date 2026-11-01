import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentCounter, IncidentStatus
from app.services.audit import record_audit_event

INCIDENT_MODULE_CODE = "incidents"
INCIDENT_NUMBER_PREFIX = "INC"

_ALLOWED_TRANSITIONS = {
    IncidentStatus.OPEN: {
        IncidentStatus.IN_PROGRESS,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.IN_PROGRESS: {
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.RESOLVED: {
        IncidentStatus.CLOSED,
    },
    IncidentStatus.CLOSED: set(),
}

_UPDATABLE_FIELDS = {
    "title",
    "description",
    "category",
    "location",
    "occurred_at",
    "impact",
    "priority",
    "root_cause",
    "resolution_action",
    "status",
}

_NON_NULLABLE_FIELDS = {
    "title",
    "description",
    "category",
    "location",
    "occurred_at",
    "status",
}


class IncidentError(ValueError):
    pass


class IncidentNotFoundError(IncidentError):
    pass


class IncidentStatusTransitionError(IncidentError):
    pass


class IncidentValidationError(IncidentError):
    pass


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _snapshot(incident: Incident) -> dict:
    return {
        "incident_number": incident.incident_number,
        "title": incident.title,
        "description": incident.description,
        "category": incident.category,
        "location": incident.location,
        "occurred_at": _ensure_aware(incident.occurred_at).isoformat(),
        "reported_at": _ensure_aware(incident.reported_at).isoformat(),
        "resolved_at": (
            _ensure_aware(incident.resolved_at).isoformat()
            if incident.resolved_at is not None
            else None
        ),
        "status": incident.status,
        "impact": incident.impact,
        "priority": incident.priority,
        "root_cause": incident.root_cause,
        "resolution_action": incident.resolution_action,
        "reported_by_user_id": (
            str(incident.reported_by_user_id)
            if incident.reported_by_user_id is not None
            else None
        ),
    }


def _next_incident_number(session: Session, company_id: uuid.UUID) -> str:
    dialect_name = session.get_bind().dialect.name

    if dialect_name == "postgresql":
        statement = (
            postgresql_insert(IncidentCounter)
            .values(company_id=company_id, last_number=1)
            .on_conflict_do_update(
                index_elements=[IncidentCounter.company_id],
                set_={"last_number": IncidentCounter.last_number + 1},
            )
            .returning(IncidentCounter.last_number)
        )
    elif dialect_name == "sqlite":
        statement = (
            sqlite_insert(IncidentCounter)
            .values(company_id=company_id, last_number=1)
            .on_conflict_do_update(
                index_elements=[IncidentCounter.company_id],
                set_={"last_number": IncidentCounter.last_number + 1},
            )
            .returning(IncidentCounter.last_number)
        )
    else:
        raise RuntimeError(
            f"Unsupported database dialect for incident numbering: {dialect_name}"
        )

    number = session.scalar(statement)
    if number is None:
        raise RuntimeError("Failed to allocate incident number")
    return f"{INCIDENT_NUMBER_PREFIX}-{number:06d}"


def _validate_transition(
    current_status: IncidentStatus,
    target_status: IncidentStatus,
) -> None:
    if target_status == current_status:
        return
    if target_status not in _ALLOWED_TRANSITIONS[current_status]:
        raise IncidentStatusTransitionError(
            f"Cannot transition incident from '{current_status.value}' "
            f"to '{target_status.value}'"
        )


def _validate_resolution_time(incident: Incident, resolved_at: datetime) -> None:
    if resolved_at < _ensure_aware(incident.occurred_at):
        raise IncidentValidationError(
            "resolved_at cannot be earlier than occurred_at"
        )


def create_incident(
    session: Session,
    *,
    company_id: uuid.UUID,
    reported_by_user_id: uuid.UUID | None,
    title: str,
    description: str,
    category: str,
    location: str,
    occurred_at: datetime,
    reported_at: datetime | None = None,
    impact: str | None = None,
    priority: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> Incident:
    now = datetime.now(UTC)
    incident = Incident(
        company_id=company_id,
        incident_number=_next_incident_number(session, company_id),
        title=title,
        description=description,
        category=category,
        location=location,
        occurred_at=_ensure_aware(occurred_at),
        reported_at=_ensure_aware(reported_at or now),
        status=IncidentStatus.OPEN.value,
        impact=impact,
        priority=priority,
        reported_by_user_id=reported_by_user_id,
    )
    session.add(incident)
    session.flush()

    record_audit_event(
        session,
        company_id=company_id,
        actor_user_id=actor_user_id,
        action="incident.created",
        entity_type="incident",
        entity_id=incident.id,
        after_data=_snapshot(incident),
        request_id=request_id,
    )
    return incident


def get_incident(
    session: Session,
    *,
    company_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> Incident:
    incident = session.scalar(
        select(Incident).where(
            Incident.id == incident_id,
            Incident.company_id == company_id,
        )
    )
    if incident is None:
        raise IncidentNotFoundError("Incident not found")
    return incident


def list_incidents(
    session: Session,
    *,
    company_id: uuid.UUID,
    category: str | None = None,
    location: str | None = None,
    status: IncidentStatus | None = None,
    priority: str | None = None,
    keyword: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Incident], int]:
    conditions = [Incident.company_id == company_id]

    if category is not None:
        conditions.append(func.lower(Incident.category) == category.lower())
    if location is not None:
        conditions.append(func.lower(Incident.location) == location.lower())
    if status is not None:
        conditions.append(Incident.status == status.value)
    if priority is not None:
        conditions.append(func.lower(Incident.priority) == priority.lower())
    if keyword is not None and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Incident.title.ilike(pattern),
                Incident.description.ilike(pattern),
                Incident.incident_number.ilike(pattern),
            )
        )
    if date_from is not None:
        conditions.append(Incident.occurred_at >= _ensure_aware(date_from))
    if date_to is not None:
        conditions.append(Incident.occurred_at <= _ensure_aware(date_to))

    total = session.scalar(
        select(func.count(Incident.id)).where(*conditions)
    )
    incidents = list(
        session.scalars(
            select(Incident)
            .where(*conditions)
            .order_by(Incident.occurred_at.desc(), Incident.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return incidents, total or 0


def update_incident(
    session: Session,
    incident: Incident,
    *,
    updates: dict,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> Incident:
    unknown_fields = set(updates) - _UPDATABLE_FIELDS
    if unknown_fields:
        raise IncidentValidationError(
            "Unsupported incident fields: " + ", ".join(sorted(unknown_fields))
        )

    for field in _NON_NULLABLE_FIELDS:
        if field in updates and updates[field] is None:
            raise IncidentValidationError(f"{field} cannot be null")

    before = _snapshot(incident)
    target_status: IncidentStatus | None = None
    if "status" in updates:
        target_status = IncidentStatus(updates["status"])
        _validate_transition(IncidentStatus(incident.status), target_status)

    if "occurred_at" in updates:
        updates["occurred_at"] = _ensure_aware(updates["occurred_at"])

    for field, value in updates.items():
        if field == "status":
            continue
        setattr(incident, field, value)

    if incident.resolved_at is not None:
        _validate_resolution_time(incident, _ensure_aware(incident.resolved_at))

    if target_status is not None:
        if (
            target_status == IncidentStatus.RESOLVED
            and incident.resolved_at is None
        ):
            resolved_at = datetime.now(UTC)
            _validate_resolution_time(incident, resolved_at)
            incident.resolved_at = resolved_at
        incident.status = target_status.value

    session.flush()
    after = _snapshot(incident)

    if after != before:
        action = (
            "incident.resolved"
            if target_status == IncidentStatus.RESOLVED
            and before["status"] != IncidentStatus.RESOLVED.value
            else "incident.updated"
        )
        record_audit_event(
            session,
            company_id=incident.company_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type="incident",
            entity_id=incident.id,
            before_data=before,
            after_data=after,
            request_id=request_id,
        )

    return incident


def resolve_incident(
    session: Session,
    incident: Incident,
    *,
    root_cause: str | None = None,
    resolution_action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> Incident:
    current_status = IncidentStatus(incident.status)
    if current_status not in {
        IncidentStatus.OPEN,
        IncidentStatus.IN_PROGRESS,
    }:
        raise IncidentStatusTransitionError(
            f"Cannot resolve incident with status '{current_status.value}'"
        )

    resolved_at = datetime.now(UTC)
    _validate_resolution_time(incident, resolved_at)

    before = _snapshot(incident)
    incident.status = IncidentStatus.RESOLVED.value
    incident.resolved_at = resolved_at
    if root_cause is not None:
        incident.root_cause = root_cause
    if resolution_action is not None:
        incident.resolution_action = resolution_action

    session.flush()
    after = _snapshot(incident)

    record_audit_event(
        session,
        company_id=incident.company_id,
        actor_user_id=actor_user_id,
        action="incident.resolved",
        entity_type="incident",
        entity_id=incident.id,
        before_data=before,
        after_data=after,
        request_id=request_id,
    )
    return incident


def get_incident_analytics(
    session: Session,
    *,
    company_id: uuid.UUID,
) -> dict:
    base_conditions = [Incident.company_id == company_id]

    total = session.scalar(
        select(func.count(Incident.id)).where(*base_conditions)
    )
    by_status = {status.value: 0 for status in IncidentStatus}
    for status_value, count in session.execute(
        select(Incident.status, func.count(Incident.id))
        .where(*base_conditions)
        .group_by(Incident.status)
    ).all():
        by_status[status_value] = count

    def grouped(column) -> dict[str, int]:
        rows = session.execute(
            select(column, func.count(Incident.id))
            .where(*base_conditions)
            .group_by(column)
        ).all()
        return {
            str(value) if value is not None else "unspecified": count
            for value, count in rows
        }

    resolved_rows = session.execute(
        select(Incident.occurred_at, Incident.resolved_at).where(
            *base_conditions,
            Incident.resolved_at.isnot(None),
        )
    ).all()
    durations = []
    for occurred_at, resolved_at in resolved_rows:
        duration = (
            _ensure_aware(resolved_at) - _ensure_aware(occurred_at)
        ).total_seconds()
        if duration >= 0:
            durations.append(duration)

    return {
        "total_incidents": total or 0,
        "by_status": by_status,
        "by_category": grouped(Incident.category),
        "by_location": grouped(Incident.location),
        "by_priority": grouped(Incident.priority),
        "average_resolution_time_seconds": (
            sum(durations) / len(durations) if durations else None
        ),
    }
