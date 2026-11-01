import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.incident import IncidentStatus


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    reported_at: datetime | None = None
    impact: str | None = Field(default=None, max_length=500)
    priority: str | None = Field(default=None, max_length=50)

    model_config = ConfigDict(extra="forbid")


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_at: datetime | None = None
    impact: str | None = Field(default=None, max_length=500)
    priority: str | None = Field(default=None, max_length=50)
    root_cause: str | None = None
    resolution_action: str | None = None
    status: IncidentStatus | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "IncidentUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one incident field must be provided")
        return self


class IncidentResolve(BaseModel):
    root_cause: str | None = None
    resolution_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class IncidentResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    incident_number: str
    title: str
    description: str
    category: str
    location: str
    occurred_at: datetime
    reported_at: datetime
    resolved_at: datetime | None
    status: IncidentStatus
    impact: str | None
    priority: str | None
    root_cause: str | None
    resolution_action: str | None
    reported_by_user_id: uuid.UUID | None
    resolution_duration_seconds: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int
    limit: int
    offset: int


class IncidentAnalyticsResponse(BaseModel):
    total_incidents: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_location: dict[str, int]
    by_priority: dict[str, int]
    average_resolution_time_seconds: float | None
