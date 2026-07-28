from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.internal_auth import require_svir_internal_secret
from app.schemas.menu_import import (
    ImportMenuResponse,
    ValidateMenuImportRequest,
    ValidateMenuImportResponse,
)
from app.services.menu_importer import (
    MenuImportError,
    import_structured_menu,
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

    This endpoint never writes to Supabase.
    """

    result = validate_menu_import(payload)

    if not result.valid:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(result),
        )

    return result


@router.post(
    "/menu/import",
    response_model=ImportMenuResponse,
)
def import_validated_menu(
    payload: ValidateMenuImportRequest,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> ImportMenuResponse | JSONResponse:
    """
    Validate and import a structured restaurant menu.

    The Supabase RPC performs the complete import atomically
    and advances the provisioning job to duplicate_agent.
    """

    validation = validate_menu_import(payload)

    if not validation.valid:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(validation),
        )

    try:
        result = import_structured_menu(payload)

    except MenuImportError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return ImportMenuResponse(
        success=True,
        restaurant_id=payload.restaurant_id,
        provisioning_job_id=payload.provisioning_job_id,
        import_id=result["import_id"],
        idempotent_replay=bool(
            result["idempotent_replay"]
        ),
        category_count=int(result["category_count"]),
        item_count=int(result["item_count"]),
        alias_count=int(result["alias_count"]),
        option_group_count=int(
            result["option_group_count"]
        ),
        option_count=int(result["option_count"]),
        ingredient_count=int(
            result["ingredient_count"]
        ),
        allergen_count=int(
            result["allergen_count"]
        ),
        next_step=str(result["next_step"]),
        warnings=validation.warnings,
    )
