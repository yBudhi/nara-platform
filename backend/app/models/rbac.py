import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.company import Company


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company | None"] = relationship("Company", back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
    )

    @property
    def is_system_template(self) -> bool:
        return self.company_id is None

    __table_args__ = (
        UniqueConstraint("id", "company_id", name="uq_roles_id_company"),
        CheckConstraint("length(trim(name)) > 0", name="ck_roles_name_not_empty"),
        Index(
            "uq_roles_company_name",
            "company_id",
            func.lower(name),
            unique=True,
            postgresql_where=company_id.isnot(None),
            sqlite_where=company_id.isnot(None),
        ),
        Index(
            "uq_roles_system_name",
            func.lower(name),
            unique=True,
            postgresql_where=company_id.is_(None),
            sqlite_where=company_id.is_(None),
        ),
    )


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        unique=True,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resource: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    module_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    roles: Mapped[list["Role"]] = relationship(
        "Role",
        secondary="role_permissions",
        back_populates="permissions",
    )

    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_permissions_code_not_empty"),
        CheckConstraint(
            "length(trim(resource)) > 0",
            name="ck_permissions_resource_not_empty",
        ),
        CheckConstraint(
            "length(trim(action)) > 0",
            name="ck_permissions_action_not_empty",
        ),
    )


class RolePermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permissions_role_permission",
        ),
    )


class UserRole(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_roles"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "company_id"],
            ["company_memberships.id", "company_memberships.company_id"],
            name="fk_user_roles_membership_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["role_id", "company_id"],
            ["roles.id", "roles.company_id"],
            name="fk_user_roles_role_company",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "membership_id",
            "role_id",
            name="uq_user_roles_membership_role",
        ),
    )
