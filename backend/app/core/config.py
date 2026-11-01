from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "NARA Platform"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    NARA_ENV: str = "poc"

    DATABASE_URL: str = Field(
        ...,
        description="Runtime database URL. Use the nara_runtime role.",
    )
    SECRET_KEY: str = Field(..., min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            raise ValueError(
                "DATABASE_URL must use the postgresql+psycopg:// driver"
            )
        return value

    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        if "change_me" in value.lower():
            raise ValueError(
                "SECRET_KEY must not use the example placeholder value"
            )
        return value


settings = Settings()
