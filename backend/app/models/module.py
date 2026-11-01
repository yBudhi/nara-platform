import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.company import Company


class Module(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "modules"

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_core: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company_modules: Mapped[list["CompanyModule"]] = relationship(
        "CompanyModule",
        back_populates="module",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_modules_code_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="ck_modules_name_not_empty"),
        CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_modules_version_not_empty",
        ),
    )


class ModuleDependency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "module_dependencies"

    parent_module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dependency_module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "parent_module_id",
            "dependency_module_id",
            name="uq_module_dependencies_parent_dependency",
        ),
        CheckConstraint(
            "parent_module_id <> dependency_module_id",
            name="ck_module_dependencies_no_self_dependency",
        ),
    )


class CompanyModule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "company_modules"

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    configuration: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    company: Mapped["Company"] = relationship("Company")
    module: Mapped["Module"] = relationship(
        "Module",
        back_populates="company_modules",
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "module_id",
            name="uq_company_modules_company_module",
        ),
    )
