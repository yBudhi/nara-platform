import uuid

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    system_role: str

    model_config = ConfigDict(from_attributes=True)


class CompanyAccessResponse(BaseModel):
    membership_id: uuid.UUID
    id: uuid.UUID
    name: str
    slug: str
    role: str
