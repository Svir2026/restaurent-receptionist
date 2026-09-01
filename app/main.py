from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.calculate import (
    router as calculate_router,
)
from app.api.routes.elevenlabs_webhook import (
    router as elevenlabs_webhook_router,
)
from app.api.routes.orders import (
    router as orders_router,
)
from app.api.routes.provisioning import (
    router as provisioning_router,
)
from app.api.routes.restaurant_tools_v2 import (
    router as restaurant_tools_v2_router,
)
from app.api.routes.libanon_order_engine import (
    router as libanon_order_engine_router,
)
from app.api.routes.tool_auth_check import (
    router as tool_auth_check_router,
)
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
    )

    # Befintliga endpoints – lämnas oförändrade.
    app.include_router(orders_router)
    app.include_router(calculate_router)
    app.include_router(elevenlabs_webhook_router)
    app.include_router(provisioning_router)

    # Nya restaurangsäkra v2-endpoints.
    app.include_router(tool_auth_check_router)
    app.include_router(restaurant_tools_v2_router)
    app.include_router(libanon_order_engine_router)

    return app


app = create_app()
