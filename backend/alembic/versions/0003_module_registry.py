"""Module registry and company activation schema

Revision ID: 0003_module_registry
Revises: 0002_rbac
Create Date: 2026-11-01 00:00:00.000000

"""

import os
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_module_registry"
down_revision: str | None = "0002_rbac"
branch_labels: str | None = None
depends_on: str | None = None

_RUNTIME_ROLE = os.getenv("ALEMBIC_RUNTIME_ROLE", "nara_runtime")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _RUNTIME_ROLE):
    raise RuntimeError("ALEMBIC_RUNTIME_ROLE must be a valid PostgreSQL role identifier")


def upgrade() -> None:
    op.create_table(
        "modules",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("is_core", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_modules_code_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_modules_name_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_modules_version_not_empty",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_modules_code"),
        "modules",
        ["code"],
        unique=True,
    )

    op.create_table(
        "module_dependencies",
        sa.Column("parent_module_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dependency_module_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "parent_module_id <> dependency_module_id",
            name="ck_module_dependencies_no_self_dependency",
        ),
        sa.ForeignKeyConstraint(
            ["dependency_module_id"],
            ["modules.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_module_id"],
            ["modules.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_module_id",
            "dependency_module_id",
            name="uq_module_dependencies_parent_dependency",
        ),
    )
    op.create_index(
        op.f("ix_module_dependencies_dependency_module_id"),
        "module_dependencies",
        ["dependency_module_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_module_dependencies_parent_module_id"),
        "module_dependencies",
        ["parent_module_id"],
        unique=False,
    )

    op.create_table(
        "company_modules",
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("module_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "configuration",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()),
                "postgresql",
            ),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["module_id"],
            ["modules.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "module_id",
            name="uq_company_modules_company_module",
        ),
    )
    op.create_index(
        op.f("ix_company_modules_company_id"),
        "company_modules",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_modules_module_id"),
        "company_modules",
        ["module_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            '"modules", "module_dependencies", "company_modules" '
            f'TO "{_RUNTIME_ROLE}"'
        )
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_modules_module_id"),
        table_name="company_modules",
    )
    op.drop_index(
        op.f("ix_company_modules_company_id"),
        table_name="company_modules",
    )
    op.drop_table("company_modules")

    op.drop_index(
        op.f("ix_module_dependencies_parent_module_id"),
        table_name="module_dependencies",
    )
    op.drop_index(
        op.f("ix_module_dependencies_dependency_module_id"),
        table_name="module_dependencies",
    )
    op.drop_table("module_dependencies")

    op.drop_index(op.f("ix_modules_code"), table_name="modules")
    op.drop_table("modules")
