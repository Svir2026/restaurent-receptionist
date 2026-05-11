from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.orders import router as orders_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
    )

    app.include_router(orders_router)
    return app


app = create_app()
