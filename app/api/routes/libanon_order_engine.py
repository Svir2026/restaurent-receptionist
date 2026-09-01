from __future__ import annotations

import os
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
    SupabaseVoiceOrderStateRepository,
    VoiceOrderStateError,
)


router = APIRouter(
    prefix="/v2/libanon",
    tags=["libanon-order-engine-test"],
)

ENABLE_ENV = "LIBANON_ORDER_ENGINE_TEST_ENABLED"


def _is_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
            repository=SupabaseVoiceOrderStateRepository(),
        )
    except VoiceOrderStateError as error:
        raise HTTPException(
            status_code=(409 if error.code == "VOICE_ORDER_REVISION_CONFLICT" else 503),
            detail={"code": error.code, "message": error.message},
        ) from error
