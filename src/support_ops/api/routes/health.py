"""Operational health endpoint: app, PostgreSQL connectivity, and pgvector availability."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from support_ops.db.session import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "error", "missing", "unknown"]


class HealthChecks(BaseModel):
    app: CheckStatus
    database: CheckStatus
    pgvector: CheckStatus


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    checks: HealthChecks
    pgvector_version: str | None = None


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
def health(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> HealthResponse:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            pgvector_version = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        # Driver messages can contain the database host, so only the exception type is logged.
        logger.error("Health check database query failed: %s", type(exc).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="error",
            checks=HealthChecks(app="ok", database="error", pgvector="unknown"),
        )

    if pgvector_version is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="error",
            checks=HealthChecks(app="ok", database="ok", pgvector="missing"),
        )

    return HealthResponse(
        status="ok",
        checks=HealthChecks(app="ok", database="ok", pgvector="ok"),
        pgvector_version=str(pgvector_version),
    )
