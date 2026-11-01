"""Audit trail schema with row-level security

Revision ID: 0004_audit_trail
Revises: 0003_module_registry
Create Date: 2026-11-01 00:00:00.000000

"""

import os
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_audit_trail"
down_revision: str | None = "0003_module_registry"
branch_labels: str | None = None
depends_on: str | None = None

_RUNTIME_ROLE = os.getenv("ALEMBIC_RUNTIME_ROLE", "nara_runtime")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _RUNTIME_ROLE):
    raise RuntimeError("ALEMBIC_RUNTIME_ROLE must be a valid PostgreSQL role identifier")


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "before_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "after_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_company_created_at",
        "audit_logs",
        ["company_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_company_entity",
        "audit_logs",
        ["company_id", "entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_company_action",
        "audit_logs",
        ["company_id", "action"],
        unique=False,
    )

    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY audit_logs_company_select ON audit_logs
        FOR SELECT
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY audit_logs_company_insert ON audit_logs
        FOR INSERT
        WITH CHECK (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )

    op.execute(
        f'REVOKE ALL PRIVILEGES ON TABLE "audit_logs" FROM "{_RUNTIME_ROLE}"'
    )
    op.execute(
        f'GRANT SELECT, INSERT ON TABLE "audit_logs" TO "{_RUNTIME_ROLE}"'
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_company_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_company_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_company_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
