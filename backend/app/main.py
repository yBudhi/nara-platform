from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.auth import router as auth_router
from app.api.routes.incidents import router as incidents_router
from app.core.config import settings
from app.core.database import get_engine

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend API for NARA Modular Business Management Platform",
    version=settings.VERSION,
)

app.include_router(auth_router)
app.include_router(incidents_router)


@app.get("/")
def root():
    return {
        "application": settings.PROJECT_NAME,
        "status": "running",
        "version": settings.VERSION,
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health/db")
def database_health():
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )

    return {"status": "connected"}
