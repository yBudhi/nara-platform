"""RBAC schema

Revision ID: 0002_rbac
Revises: 0001_initial_identity
Create Date: 2026-11-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_rbac"
down_revision: str | None = "0001_initial_identity"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_roles_name_not_empty",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "company_id", name="uq_roles_id_company"),
    )
    op.create_index(
        op.f("ix_roles_company_id"),
        "roles",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "uq_roles_company_name",
        "roles",
        ["company_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.create_index(
        "uq_roles_system_name",
        "roles",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("company_id IS NULL"),
    )

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("resource", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("module_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_permissions_code_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(resource)) > 0",
            name="ck_permissions_resource_not_empty",
        ),
        sa.CheckConstraint(
            "length(trim(action)) > 0",
            name="ck_permissions_action_not_empty",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_permissions_code"),
        "permissions",
        ["code"],
        unique=True,
    )
    op.create_index(
        op.f("ix_permissions_module_code"),
        "permissions",
        ["module_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_permissions_resource"),
        "permissions",
        ["resource"],
        unique=False,
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("permission_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permissions_role_permission",
        ),
    )
    op.create_index(
        op.f("ix_role_permissions_permission_id"),
        "role_permissions",
        ["permission_id"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_company_memberships_id_company",
        "company_memberships",
        ["id", "company_id"],
    )

    op.create_table(
        "user_roles",
        sa.Column("membership_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id", "company_id"],
            ["company_memberships.id", "company_memberships.company_id"],
            name="fk_user_roles_membership_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "company_id"],
            ["roles.id", "roles.company_id"],
            name="fk_user_roles_role_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id",
            "role_id",
            name="uq_user_roles_membership_role",
        ),
    )
    op.create_index(
        op.f("ix_user_roles_company_id"),
        "user_roles",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_roles_membership_id"),
        "user_roles",
        ["membership_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_roles_role_id"),
        "user_roles",
        ["role_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_roles_role_id"), table_name="user_roles")
    op.drop_index(op.f("ix_user_roles_membership_id"), table_name="user_roles")
    op.drop_index(op.f("ix_user_roles_company_id"), table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_constraint(
        "uq_company_memberships_id_company",
        "company_memberships",
        type_="unique",
    )

    op.drop_index(
        op.f("ix_role_permissions_permission_id"),
        table_name="role_permissions",
    )
    op.drop_table("role_permissions")

    op.drop_index(op.f("ix_permissions_resource"), table_name="permissions")
    op.drop_index(op.f("ix_permissions_module_code"), table_name="permissions")
    op.drop_index(op.f("ix_permissions_code"), table_name="permissions")
    op.drop_table("permissions")

    op.drop_index("uq_roles_system_name", table_name="roles")
    op.drop_index("uq_roles_company_name", table_name="roles")
    op.drop_index(op.f("ix_roles_company_id"), table_name="roles")
    op.drop_table("roles")
