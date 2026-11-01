import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.module import CompanyModule, Module, ModuleDependency
from app.services.audit import record_audit_event


class ModuleActivationError(ValueError):
    pass


def _get_company_module(
    session: Session,
    company_id: uuid.UUID,
    module_id: uuid.UUID,
) -> CompanyModule | None:
    return session.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == company_id,
            CompanyModule.module_id == module_id,
        )
    )


def _enabled_module_ids(session: Session, company_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        session.scalars(
            select(CompanyModule.module_id).where(
                CompanyModule.company_id == company_id,
                CompanyModule.is_enabled.is_(True),
            )
        )
    )


def is_module_enabled(
    session: Session,
    company_id: uuid.UUID,
    module_code: str,
) -> bool:
    module_id = session.scalar(
        select(Module.id)
        .join(
            CompanyModule,
            CompanyModule.module_id == Module.id,
        )
        .where(
            Module.code == module_code,
            Module.is_active.is_(True),
            CompanyModule.company_id == company_id,
            CompanyModule.is_enabled.is_(True),
        )
        .limit(1)
    )
    return module_id is not None


def activate_module(
    session: Session,
    company: Company,
    module: Module,
    *,
    configuration: dict | None = None,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> CompanyModule:
    if not module.is_active:
        raise ModuleActivationError(f"Module '{module.code}' is not active")

    company_module = _get_company_module(session, company.id, module.id)
    if company_module is not None and company_module.is_enabled:
        raise ModuleActivationError(
            f"Module '{module.code}' is already enabled for this company"
        )

    required_dependencies = session.execute(
        select(Module.id, Module.code)
        .join(
            ModuleDependency,
            ModuleDependency.dependency_module_id == Module.id,
        )
        .where(ModuleDependency.parent_module_id == module.id)
    ).all()
    enabled_ids = _enabled_module_ids(session, company.id)
    missing_codes = sorted(
        code for module_id, code in required_dependencies if module_id not in enabled_ids
    )
    if missing_codes:
        raise ModuleActivationError(
            "Missing enabled dependencies: " + ", ".join(missing_codes)
        )

    previous_state = (
        {"is_enabled": company_module.is_enabled}
        if company_module is not None
        else None
    )

    if company_module is None:
        company_module = CompanyModule(
            company_id=company.id,
            module_id=module.id,
            is_enabled=True,
            configuration=configuration if configuration is not None else {},
        )
        session.add(company_module)
    else:
        company_module.is_enabled = True
        if configuration is not None:
            company_module.configuration = configuration

    session.flush()

    record_audit_event(
        session,
        company_id=company.id,
        actor_user_id=actor_user_id,
        action="module.activated",
        entity_type="company_module",
        entity_id=company_module.id,
        before_data=previous_state,
        after_data={
            "module_id": str(module.id),
            "module_code": module.code,
            "is_enabled": True,
        },
        request_id=request_id,
    )

    return company_module


def deactivate_module(
    session: Session,
    company: Company,
    module: Module,
    *,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> CompanyModule:
    company_module = _get_company_module(session, company.id, module.id)
    if company_module is None:
        raise ModuleActivationError(
            f"Module '{module.code}' is not activated for this company"
        )
    if not company_module.is_enabled:
        raise ModuleActivationError(
            f"Module '{module.code}' is already disabled for this company"
        )

    blocking_modules = session.scalars(
        select(Module.code)
        .join(
            ModuleDependency,
            ModuleDependency.parent_module_id == Module.id,
        )
        .join(
            CompanyModule,
            and_(
                CompanyModule.module_id == ModuleDependency.parent_module_id,
                CompanyModule.company_id == company.id,
                CompanyModule.is_enabled.is_(True),
            ),
        )
        .where(ModuleDependency.dependency_module_id == module.id)
        .order_by(Module.code)
    ).all()
    if blocking_modules:
        raise ModuleActivationError(
            "Module is required by enabled modules: " + ", ".join(blocking_modules)
        )

    company_module.is_enabled = False
    session.flush()

    record_audit_event(
        session,
        company_id=company.id,
        actor_user_id=actor_user_id,
        action="module.deactivated",
        entity_type="company_module",
        entity_id=company_module.id,
        before_data={"is_enabled": True},
        after_data={
            "module_id": str(module.id),
            "module_code": module.code,
            "is_enabled": False,
        },
        request_id=request_id,
    )

    return company_module
