from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.internal_auth import require_svir_internal_secret
from app.schemas.menu_import import (
    ValidateMenuImportRequest,
    ValidateMenuImportResponse,
)
from app.services.menu_validator import validate_menu_import


router = APIRouter(
    prefix="/internal/provisioning",
    tags=["internal-provisioning"],
)


@router.post(
    "/menu/validate",
    response_model=ValidateMenuImportResponse,
)
def validate_structured_menu(
    payload: ValidateMenuImportRequest,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> ValidateMenuImportResponse | JSONResponse:
    """
    Validate a structured restaurant menu.

    This endpoint does not write to Supabase and does not call
    ElevenLabs, Telnyx, Railway services, or any other external system.
    """

    result = validate_menu_import(payload)

    if not result.valid:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(result),
        )

    return result
