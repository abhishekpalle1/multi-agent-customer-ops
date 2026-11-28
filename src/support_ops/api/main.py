"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from support_ops.api.routes.health import router as health_router
from support_ops.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="Multi-Agent Customer Operations", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
