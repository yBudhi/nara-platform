"""Seed initial permission catalog

Revision ID: 0005_seed_permissions
Revises: 0004_audit_trail
Create Date: 2026-11-01 00:00:00.000000

"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_seed_permissions"
down_revision: str | None = "0004_audit_trail"
branch_labels: str | None = None
depends_on: str | None = None

_PERMISSIONS = (
    (
        "company.read",
        "Read company details",
        "company",
        "read",
        "identity",
    ),
    (
        "company.manage",
        "Manage company settings",
        "company",
        "manage",
        "identity",
    ),
    (
        "members.read",
        "Read company members",
        "members",
        "read",
        "identity",
    ),
    (
        "members.manage",
        "Manage company members",
        "members",
        "manage",
        "identity",
    ),
    (
        "roles.read",
        "Read company roles",
        "roles",
        "read",
        "rbac",
    ),
    (
        "roles.manage",
        "Manage company roles",
        "roles",
        "manage",
        "rbac",
    ),
    (
        "modules.read",
        "Read module registry",
        "modules",
        "read",
        "module_registry",
    ),
    (
        "modules.manage",
        "Manage module registry",
        "modules",
        "manage",
        "module_registry",
    ),
    (
        "audit.read",
        "Read audit trail",
        "audit",
        "read",
        "audit",
    ),
)


def upgrade() -> None:
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


def downgrade() -> None:
    """Permission catalog data is intentionally preserved on downgrade."""
