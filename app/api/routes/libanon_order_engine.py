from __future__ import annotations

import hmac
import os
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.tool_auth import (
    TOOL_TOKEN_HEADER,
    ToolRestaurantContext,
    require_restaurant_tool_context,
)
from app.schemas.libanon_order_engine import (
    LibanonAgentOrderTurnResponse,
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
PREVIEW_TOKEN_ENV = "LIBANON_PREVIEW_TOOL_TOKEN"


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


def require_libanon_order_engine_context(
    tool_token: Annotated[
        str | None,
        Header(
            alias=TOOL_TOKEN_HEADER,
            convert_underscores=False,
        ),
    ] = None,
) -> ToolRestaurantContext:
    backend = os.environ.get(STATE_BACKEND_ENV, "supabase").strip().casefold()
    if _is_enabled() and backend == "sqlite":
        expected = os.environ.get(PREVIEW_TOKEN_ENV, "").strip()
        supplied = str(tool_token or "").strip()
        if len(expected) < 32:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "LIBANON_PREVIEW_AUTH_UNAVAILABLE",
                    "message": "Libanons testautentisering är inte konfigurerad.",
                },
            )
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INVALID_TOOL_TOKEN",
                    "message": "Verktygsanropet kunde inte autentiseras.",
                },
            )
        return ToolRestaurantContext(
            credential_id=UUID("00000000-0000-0000-0000-000000000001"),
            restaurant_id=UUID(LIBANON_RESTAURANT_ID),
            restaurant_name="Libanon Kolgrill",
            restaurant_slug=LIBANON_RESTAURANT_SLUG,
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )

    return require_restaurant_tool_context(tool_token)


@router.post(
    "/order-turn",
    response_model=LibanonOrderTurnResponse,
)
def libanon_order_turn(
    payload: LibanonOrderTurnRequest,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_libanon_order_engine_context),
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


@router.post(
    "/order-turn-agent",
    response_model=LibanonAgentOrderTurnResponse,
)
def libanon_agent_order_turn(
    payload: LibanonOrderTurnRequest,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_libanon_order_engine_context),
    ],
) -> LibanonAgentOrderTurnResponse:
    """Compact voice-agent view of the isolated, non-submitting engine."""

    result = libanon_order_turn(payload, context)
    return LibanonAgentOrderTurnResponse(
        success=result.success,
        action=result.action,
        say=result.say,
        idempotent_replay=result.idempotent_replay,
        state_revision=result.state_revision,
        order_ready=result.order_ready,
        submission_allowed=result.submission_allowed,
    )
