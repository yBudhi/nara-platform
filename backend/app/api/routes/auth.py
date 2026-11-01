from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    TenantContext,
    get_active_company,
    get_current_user,
    get_db,
)
from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.company import Company
from app.models.membership import CompanyMembership
from app.models.user import User
from app.schemas.auth import (
    CompanyAccessResponse,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from app.services.rbac import get_effective_permission_codes

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))

    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
def read_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user


@router.get("/me/permissions", response_model=list[str])
def read_my_permissions(
    context: Annotated[TenantContext, Depends(get_active_company)],
    db: Annotated[Session, Depends(get_db)],
) -> list[str]:
    return sorted(get_effective_permission_codes(db, context.membership))


@router.get("/companies", response_model=list[CompanyAccessResponse])
def list_companies(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CompanyAccessResponse]:
    memberships = db.scalars(
        select(CompanyMembership)
        .join(Company, Company.id == CompanyMembership.company_id)
        .options(selectinload(CompanyMembership.company))
        .where(
            CompanyMembership.user_id == current_user.id,
            CompanyMembership.is_active.is_(True),
            Company.is_active.is_(True),
        )
        .order_by(Company.name)
    ).all()

    return [
        CompanyAccessResponse(
            membership_id=membership.id,
            id=membership.company.id,
            name=membership.company.name,
            slug=membership.company.slug,
            role=membership.role,
        )
        for membership in memberships
    ]
