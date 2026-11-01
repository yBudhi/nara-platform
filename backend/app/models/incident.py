import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IncidentStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentCounter(Base):
    __tablename__ = "incident_counters"

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Incident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incidents"

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    incident_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IncidentStatus.OPEN.value,
    )
    impact: Mapped[str | None] = mapped_column(String(500), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(50), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    @property
    def resolution_duration_seconds(self) -> int | None:
        if self.resolved_at is None:
            return None

        occurred_at = self.occurred_at
        resolved_at = self.resolved_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=UTC)

        duration = (resolved_at - occurred_at).total_seconds()
        if duration < 0:
            return None
        return int(duration)

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "incident_number",
            name="uq_incidents_company_incident_number",
        ),
        CheckConstraint(
            "length(trim(incident_number)) > 0",
            name="ck_incidents_number_not_empty",
        ),
        CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_incidents_title_not_empty",
        ),
        CheckConstraint(
            "length(trim(category)) > 0",
            name="ck_incidents_category_not_empty",
        ),
        CheckConstraint(
            "length(trim(location)) > 0",
            name="ck_incidents_location_not_empty",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name="ck_incidents_status_valid",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= occurred_at",
            name="ck_incidents_resolved_after_occurred",
        ),
        Index("ix_incidents_company_occurred_at", "company_id", "occurred_at"),
        Index("ix_incidents_company_status", "company_id", "status"),
        Index("ix_incidents_company_category", "company_id", "category"),
        Index("ix_incidents_company_location", "company_id", "location"),
        Index("ix_incidents_company_priority", "company_id", "priority"),
    )
