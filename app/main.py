from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.calculate import router as calculate_router
from app.api.routes.elevenlabs_webhook import router as elevenlabs_webhook_router
from app.api.routes.orders import router as orders_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
    )

    app.include_router(orders_router)
    app.include_router(calculate_router)
    app.include_router(elevenlabs_webhook_router)
    return app


app = create_app()
