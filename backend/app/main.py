import os
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="NARA Platform API",
    description="Backend API for NARA Modular Business Management Platform",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "application": "NARA Platform",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/db")
def database_health():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise HTTPException(
            status_code=500,
            detail="Database configuration is missing",
        )

    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_database(), current_user, version()"
                )
                database, user, version = cur.fetchone()

        return {
            "status": "connected",
            "database": database,
            "user": user,
            "postgresql": version.split(",")[0],
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )
