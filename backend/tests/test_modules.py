import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.company import Company
from app.models.module import CompanyModule, Module, ModuleDependency
from app.services.modules import (
    ModuleActivationError,
    activate_module,
    deactivate_module,
)


def _create_company(db_session, *, slug: str) -> Company:
    company = Company(name=slug.replace("-", " ").title(), slug=slug)
    db_session.add(company)
    db_session.commit()
    return company


def _create_module(
    db_session,
    *,
    code: str,
    version: str = "1.0.0",
    is_core: bool = False,
    is_active: bool = True,
) -> Module:
    module = Module(
        code=code,
        name=code.replace(".", " ").title(),
        description=f"{code} module",
        version=version,
        is_core=is_core,
        is_active=is_active,
    )
    db_session.add(module)
    db_session.commit()
    return module


def _add_dependency(db_session, parent: Module, dependency: Module) -> ModuleDependency:
    link = ModuleDependency(
        parent_module_id=parent.id,
        dependency_module_id=dependency.id,
    )
    db_session.add(link)
    db_session.commit()
    return link


def _company_module(db_session, company: Company, module: Module):
    return db_session.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == company.id,
            CompanyModule.module_id == module.id,
        )
    )


def test_activate_module_with_configuration(db_session):
    company = _create_company(db_session, slug="activation")
    module = _create_module(db_session, code="reports.core")

    company_module = activate_module(
        db_session,
        company,
        module,
        configuration={"default_currency": "USD"},
    )
    db_session.commit()
    db_session.refresh(company_module)

    assert company_module.is_enabled is True
    assert company_module.configuration == {"default_currency": "USD"}
    assert company_module.company_id == company.id
    assert company_module.module_id == module.id


def test_activate_module_requires_enabled_dependencies(db_session):
    company = _create_company(db_session, slug="missing-deps")
    base = _create_module(db_session, code="billing.base")
    invoices = _create_module(db_session, code="billing.invoices")
    _add_dependency(db_session, invoices, base)

    with pytest.raises(ModuleActivationError, match="billing.base"):
        activate_module(db_session, company, invoices)

    assert _company_module(db_session, company, invoices) is None


def test_activate_module_succeeds_when_dependencies_are_enabled(db_session):
    company = _create_company(db_session, slug="with-deps")
    base = _create_module(db_session, code="billing.base", is_core=True)
    invoices = _create_module(db_session, code="billing.invoices")
    _add_dependency(db_session, invoices, base)

    activate_module(db_session, company, base)
    invoices_activation = activate_module(db_session, company, invoices)
    db_session.commit()

    assert invoices_activation.is_enabled is True
    assert _company_module(db_session, company, base).is_enabled is True


def test_duplicate_activation_is_rejected(db_session):
    company = _create_company(db_session, slug="duplicate-activation")
    module = _create_module(db_session, code="inventory.core")

    activate_module(db_session, company, module)
    db_session.commit()

    with pytest.raises(ModuleActivationError, match="already enabled"):
        activate_module(db_session, company, module)
    db_session.rollback()


def test_duplicate_company_module_row_is_rejected(db_session):
    company = _create_company(db_session, slug="duplicate-row")
    module = _create_module(db_session, code="crm.core")

    db_session.add(
        CompanyModule(company_id=company.id, module_id=module.id, is_enabled=True)
    )
    db_session.commit()

    db_session.add(
        CompanyModule(company_id=company.id, module_id=module.id, is_enabled=True)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_deactivate_module(db_session):
    company = _create_company(db_session, slug="deactivation")
    module = _create_module(db_session, code="support.core")

    activate_module(db_session, company, module)
    db_session.commit()

    company_module = deactivate_module(db_session, company, module)
    db_session.commit()
    db_session.refresh(company_module)

    assert company_module.is_enabled is False


def test_deactivation_blocked_by_enabled_dependent_module(db_session):
    company = _create_company(db_session, slug="blocked-deactivation")
    base = _create_module(db_session, code="billing.base")
    invoices = _create_module(db_session, code="billing.invoices")
    _add_dependency(db_session, invoices, base)

    activate_module(db_session, company, base)
    activate_module(db_session, company, invoices)
    db_session.commit()

    with pytest.raises(ModuleActivationError, match="billing.invoices"):
        deactivate_module(db_session, company, base)
    db_session.rollback()

    assert _company_module(db_session, company, base).is_enabled is True


def test_deactivation_is_scoped_to_the_company(db_session):
    company_a = _create_company(db_session, slug="tenant-a")
    company_b = _create_company(db_session, slug="tenant-b")
    base = _create_module(db_session, code="billing.base")
    invoices = _create_module(db_session, code="billing.invoices")
    _add_dependency(db_session, invoices, base)

    activate_module(db_session, company_a, base)
    activate_module(db_session, company_a, invoices)
    activate_module(db_session, company_b, base)
    db_session.commit()

    company_b_base = deactivate_module(db_session, company_b, base)
    db_session.commit()

    assert company_b_base.is_enabled is False
    assert _company_module(db_session, company_a, base).is_enabled is True


def test_module_dependency_prevents_self_and_duplicate_links(db_session):
    base = _create_module(db_session, code="base.module")
    feature = _create_module(db_session, code="feature.module")

    db_session.add(
        ModuleDependency(
            parent_module_id=base.id,
            dependency_module_id=base.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        ModuleDependency(
            parent_module_id=feature.id,
            dependency_module_id=base.id,
        )
    )
    db_session.commit()

    db_session.add(
        ModuleDependency(
            parent_module_id=feature.id,
            dependency_module_id=base.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
