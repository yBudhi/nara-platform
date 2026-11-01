"""Incident management module

Revision ID: 0006_incidents
Revises: 0005_seed_permissions
Create Date: 2026-11-01 00:00:00.000000

"""

import os
import re
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_incidents"
down_revision: str | None = "0005_seed_permissions"
branch_labels: str | None = None
depends_on: str | None = None

_RUNTIME_ROLE = os.getenv("ALEMBIC_RUNTIME_ROLE", "nara_runtime")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _RUNTIME_ROLE):
    raise RuntimeError("ALEMBIC_RUNTIME_ROLE must be a valid PostgreSQL role identifier")

_PERMISSIONS = (
    (
        "incidents.read",
        "Read incidents",
        "incidents",
        "read",
        "incidents",
    ),
    (
        "incidents.create",
        "Create incidents",
        "incidents",
        "create",
        "incidents",
    ),
    (
        "incidents.update",
        "Update incidents",
        "incidents",
        "update",
        "incidents",
    ),
    (
        "incidents.resolve",
        "Resolve incidents",
        "incidents",
        "resolve",
        "incidents",
    ),
    (
        "incidents.analyze",
        "Read incident analytics",
        "incidents",
        "analyze",
        "incidents",
    ),
)


def _seed_module() -> None:
    modules = sa.table(
        "modules",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("version", sa.String()),
        sa.column("is_core", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    statement = (
        postgresql.insert(modules)
        .values(
            id=uuid.uuid4(),
            code="incidents",
            name="Incident Management",
            description="Incident capture, resolution, and analytics",
            version="1.0.0",
            is_core=False,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )
    op.execute(statement)


def _seed_permissions() -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
        sa.column("resource", sa.String()),
        sa.column("action", sa.String()),
        sa.column("module_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "code": code,
            "description": description,
            "resource": resource,
            "action": action,
            "module_code": module_code,
            "created_at": now,
            "updated_at": now,
        }
        for code, description, resource, action, module_code in _PERMISSIONS
    ]
    statement = (
        postgresql.insert(permissions)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["code"])
    )
    op.execute(statement)


def upgrade() -> None:
    _seed_module()
    _seed_permissions()

    op.create_table(
        "incident_counters",
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("company_id"),
    )

    op.create_table(
        "incidents",
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("incident_number", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("impact", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.String(length=50), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution_action", sa.Text(), nullable=True),
        sa.Column("reported_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(incident_number)) > 0",
            name="ck_incidents_number_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_incidents_title_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(category)) > 0",
            name="ck_incidents_category_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(location)) > 0",
            name="ck_incidents_location_not_empty",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name="ck_incidents_status_valid",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= occurred_at",
            name="ck_incidents_resolved_after_occurred",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reported_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "incident_number",
            name="uq_incidents_company_incident_number",
        ),
    )
    op.create_index(
        op.f("ix_incidents_company_id"),
        "incidents",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incidents_reported_by_user_id"),
        "incidents",
        ["reported_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_company_occurred_at",
        "incidents",
        ["company_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_company_status",
        "incidents",
        ["company_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_company_category",
        "incidents",
        ["company_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_company_location",
        "incidents",
        ["company_id", "location"],
        unique=False,
    )
    op.create_index(
        "ix_incidents_company_priority",
        "incidents",
        ["company_id", "priority"],
        unique=False,
    )

    op.execute("ALTER TABLE incidents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE incidents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY incidents_company_select ON incidents
        FOR SELECT
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY incidents_company_insert ON incidents
        FOR INSERT
        WITH CHECK (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY incidents_company_update ON incidents
        FOR UPDATE
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        WITH CHECK (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY incidents_company_delete ON incidents
        FOR DELETE
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )

    op.execute(
        f'REVOKE ALL PRIVILEGES ON TABLE "incidents" FROM "{_RUNTIME_ROLE}"'
    )
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE ON TABLE "incidents" TO "{_RUNTIME_ROLE}"'
    )
    op.execute(
        f'REVOKE ALL PRIVILEGES ON TABLE "incident_counters" FROM "{_RUNTIME_ROLE}"'
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE "
        f'"incident_counters" TO "{_RUNTIME_ROLE}"'
    )
    op.execute(
        f'REVOKE INSERT, UPDATE, DELETE ON TABLE "permissions" FROM "{_RUNTIME_ROLE}"'
    )
    op.execute(
        f'GRANT SELECT ON TABLE "permissions" TO "{_RUNTIME_ROLE}"'
    )


def downgrade() -> None:
    op.execute(
        f'GRANT INSERT, UPDATE, DELETE ON TABLE "permissions" TO "{_RUNTIME_ROLE}"'
    )
    op.drop_index("ix_incidents_company_priority", table_name="incidents")
    op.drop_index("ix_incidents_company_location", table_name="incidents")
    op.drop_index("ix_incidents_company_category", table_name="incidents")
    op.drop_index("ix_incidents_company_status", table_name="incidents")
    op.drop_index("ix_incidents_company_occurred_at", table_name="incidents")
    op.drop_index(
        op.f("ix_incidents_reported_by_user_id"),
        table_name="incidents",
    )
    op.drop_index(op.f("ix_incidents_company_id"), table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("incident_counters")
