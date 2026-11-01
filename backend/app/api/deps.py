import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, set_current_company_id
from app.core.security import decode_access_token
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.user import User
from app.services.modules import is_module_enabled
from app.services.rbac import membership_has_permission

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


@dataclass(frozen=True)
class TenantContext:
    user: User
    company: Company
    membership: CompanyMembership


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not subject:
            raise _credentials_exception()
        user_id = uuid.UUID(str(subject))
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise _credentials_exception() from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_exception()

    return user


def resolve_tenant_context(
    db: Session,
    user: User,
    company_id: uuid.UUID,
) -> TenantContext:
    membership = db.scalar(
        select(CompanyMembership)
        .join(Company, Company.id == CompanyMembership.company_id)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company_id,
            CompanyMembership.is_active.is_(True),
            Company.is_active.is_(True),
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active membership is required for this company",
        )

    company = db.get(Company, company_id)
    if company is None or not company.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active membership is required for this company",
        )

    set_current_company_id(db, company_id)
    return TenantContext(user=user, company=company, membership=membership)


def get_active_company(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    x_company_id: Annotated[uuid.UUID | None, Header(alias="X-Company-ID")] = None,
) -> TenantContext:
    if x_company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Company-ID header is required",
        )

    return resolve_tenant_context(db, current_user, x_company_id)


def require_permission(permission_code: str):
    def dependency(
        context: Annotated[TenantContext, Depends(get_active_company)],
        db: Annotated[Session, Depends(get_db)],
    ) -> TenantContext:
        if not membership_has_permission(
            db,
            context.membership,
            permission_code,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission_code}",
            )
        return context

    return dependency


def require_module_enabled(module_code: str):
    def dependency(
        context: Annotated[TenantContext, Depends(get_active_company)],
        db: Annotated[Session, Depends(get_db)],
    ) -> TenantContext:
        if not is_module_enabled(db, context.company.id, module_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Module '{module_code}' is not enabled for this company",
            )
        return context

    return dependency


get_tenant_context = get_active_company

__all__ = [
    "TenantContext",
    "get_active_company",
    "get_current_user",
    "get_db",
    "get_tenant_context",
    "oauth2_scheme",
    "require_module_enabled",
    "require_permission",
    "resolve_tenant_context",
]
