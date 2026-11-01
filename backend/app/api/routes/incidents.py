import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    TenantContext,
    get_db,
    require_module_enabled,
    require_permission,
)
from app.models.incident import IncidentStatus
from app.schemas.incident import (
    IncidentAnalyticsResponse,
    IncidentCreate,
    IncidentListResponse,
    IncidentResolve,
    IncidentResponse,
    IncidentUpdate,
)
from app.services import incidents as incident_service
from app.services.rbac import membership_has_permission

router = APIRouter(prefix="/incidents", tags=["incidents"])

_MODULE_DEPENDENCY = Depends(
    require_module_enabled(incident_service.INCIDENT_MODULE_CODE)
)


def _incident_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Incident not found",
    )


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: IncidentCreate,
    context: Annotated[TenantContext, Depends(require_permission("incidents.create"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentResponse:
    incident = incident_service.create_incident(
        db,
        company_id=context.company.id,
        reported_by_user_id=context.user.id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        location=payload.location,
        occurred_at=payload.occurred_at,
        reported_at=payload.reported_at,
        impact=payload.impact,
        priority=payload.priority,
        actor_user_id=context.user.id,
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    context: Annotated[TenantContext, Depends(require_permission("incidents.read"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
    category: Annotated[str | None, Query(max_length=100)] = None,
    location: Annotated[str | None, Query(max_length=255)] = None,
    incident_status: Annotated[
        IncidentStatus | None,
        Query(alias="status"),
    ] = None,
    priority: Annotated[str | None, Query(max_length=50)] = None,
    keyword: Annotated[str | None, Query(max_length=255)] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IncidentListResponse:
    incidents, total = incident_service.list_incidents(
        db,
        company_id=context.company.id,
        category=category,
        location=location,
        status=incident_status,
        priority=priority,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return IncidentListResponse(
        items=[IncidentResponse.model_validate(incident) for incident in incidents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/analytics/summary", response_model=IncidentAnalyticsResponse)
def incident_analytics(
    context: Annotated[TenantContext, Depends(require_permission("incidents.analyze"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return incident_service.get_incident_analytics(
        db,
        company_id=context.company.id,
    )


@router.get("/{incident_id}", response_model=IncidentResponse)
def read_incident(
    incident_id: uuid.UUID,
    context: Annotated[TenantContext, Depends(require_permission("incidents.read"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentResponse:
    try:
        incident = incident_service.get_incident(
            db,
            company_id=context.company.id,
            incident_id=incident_id,
        )
    except incident_service.IncidentNotFoundError:
        raise _incident_not_found() from None
    return incident


@router.patch("/{incident_id}", response_model=IncidentResponse)
def update_incident(
    incident_id: uuid.UUID,
    payload: IncidentUpdate,
    context: Annotated[TenantContext, Depends(require_permission("incidents.update"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentResponse:
    try:
        incident = incident_service.get_incident(
            db,
            company_id=context.company.id,
            incident_id=incident_id,
        )
    except incident_service.IncidentNotFoundError:
        raise _incident_not_found() from None

    if payload.status == IncidentStatus.RESOLVED and not membership_has_permission(
        db,
        context.membership,
        "incidents.resolve",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission required: incidents.resolve",
        )

    try:
        incident = incident_service.update_incident(
            db,
            incident,
            updates=payload.model_dump(exclude_unset=True),
            actor_user_id=context.user.id,
        )
    except incident_service.IncidentStatusTransitionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from None
    except incident_service.IncidentValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    db.commit()
    db.refresh(incident)
    return incident


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: uuid.UUID,
    context: Annotated[TenantContext, Depends(require_permission("incidents.resolve"))],
    _module: Annotated[TenantContext, _MODULE_DEPENDENCY],
    db: Annotated[Session, Depends(get_db)],
    payload: IncidentResolve | None = None,
) -> IncidentResponse:
    try:
        incident = incident_service.get_incident(
            db,
            company_id=context.company.id,
            incident_id=incident_id,
        )
    except incident_service.IncidentNotFoundError:
        raise _incident_not_found() from None

    try:
        incident = incident_service.resolve_incident(
            db,
            incident,
            root_cause=payload.root_cause if payload is not None else None,
            resolution_action=(
                payload.resolution_action if payload is not None else None
            ),
            actor_user_id=context.user.id,
        )
    except incident_service.IncidentStatusTransitionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from None
    except incident_service.IncidentValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    db.commit()
    db.refresh(incident)
    return incident
