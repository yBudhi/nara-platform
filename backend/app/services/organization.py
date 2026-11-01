import uuid

from sqlalchemy.orm import Session

from app.models.company import Company
from app.services.audit import record_audit_event


def create_company(
    session: Session,
    *,
    name: str,
    slug: str,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> Company:
    company = Company(name=name, slug=slug)
    session.add(company)
    session.flush()

    record_audit_event(
        session,
        company_id=company.id,
        actor_user_id=actor_user_id,
        action="company.created",
        entity_type="company",
        entity_id=company.id,
        after_data={
            "name": company.name,
            "slug": company.slug,
            "is_active": company.is_active,
        },
        request_id=request_id,
    )

    return company
