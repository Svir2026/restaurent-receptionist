from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.tool_auth import (
    ToolRestaurantContext,
    require_restaurant_tool_context,
)
from app.schemas.libanon_order_engine import (
    LibanonOrderTurnRequest,
    LibanonOrderTurnResponse,
)
from app.services.libanon_menu_catalog import (
    LIBANON_RESTAURANT_ID,
    LIBANON_RESTAURANT_SLUG,
)
from app.services.libanon_order_engine import process_libanon_order_turn
from app.services.voice_order_state import (
    SQLiteVoiceOrderStateRepository,
    SupabaseVoiceOrderStateRepository,
    VoiceOrderStateRepository,
    VoiceOrderStateError,
)


router = APIRouter(
    prefix="/v2/libanon",
    tags=["libanon-order-engine-test"],
)

ENABLE_ENV = "LIBANON_ORDER_ENGINE_TEST_ENABLED"
STATE_BACKEND_ENV = "LIBANON_ORDER_STATE_BACKEND"
SQLITE_PATH_ENV = "LIBANON_ORDER_SQLITE_PATH"


def _is_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


@lru_cache(maxsize=2)
def _state_repository(
    backend: str,
    sqlite_path: str,
) -> VoiceOrderStateRepository:
    if backend == "sqlite":
        return SQLiteVoiceOrderStateRepository(sqlite_path)
    if backend == "supabase":
        return SupabaseVoiceOrderStateRepository()
    raise VoiceOrderStateError(
        "INVALID_VOICE_ORDER_STATE_BACKEND",
        "Orderlagringen är felkonfigurerad.",
    )


def get_state_repository() -> VoiceOrderStateRepository:
    backend = os.environ.get(STATE_BACKEND_ENV, "supabase").strip().casefold()
    sqlite_path = os.environ.get(
        SQLITE_PATH_ENV,
        "/tmp/libanon_voice_order_state.sqlite3",
    ).strip()
    return _state_repository(backend, sqlite_path)


@router.post(
    "/order-turn",
    response_model=LibanonOrderTurnResponse,
)
def libanon_order_turn(
    payload: LibanonOrderTurnRequest,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> LibanonOrderTurnResponse:
    """Test-only Libanon turn engine. It never submits a real order."""

    if not _is_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LIBANON_ORDER_ENGINE_DISABLED",
                "message": "Libanons testverktyg är inte aktiverat.",
            },
        )

    if (
        str(context.restaurant_id) != LIBANON_RESTAURANT_ID
        or context.restaurant_slug != LIBANON_RESTAURANT_SLUG
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LIBANON_RESTAURANT_MISMATCH",
                "message": "Verktyget tillhör en annan restaurang.",
            },
        )

    try:
        return process_libanon_order_turn(
            request=payload,
            repository=get_state_repository(),
        )
    except VoiceOrderStateError as error:
        raise HTTPException(
            status_code=(409 if error.code == "VOICE_ORDER_REVISION_CONFLICT" else 503),
            detail={"code": error.code, "message": error.message},
        ) from error
