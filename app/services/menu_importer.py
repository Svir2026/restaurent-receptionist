from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.schemas.menu_import import ValidateMenuImportRequest
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)


class MenuImportError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


SAFE_ERROR_MAP: dict[str, tuple[int, str]] = {
    "PROVISIONING_JOB_NOT_FOUND": (
        404,
        "Installationsjobbet kunde inte hittas.",
    ),
    "PROVISIONING_RESTAURANT_MISMATCH": (
        409,
        "Restaurangen matchar inte installationsjobbet.",
    ),
    "PROVISIONING_CANCELLED": (
        409,
        "Installationsjobbet har avbrutits.",
    ),
    "MENU_IMPORT_IDENTITY_MISMATCH": (
        409,
        "Menyimporten tillhör en annan restaurang eller installation.",
    ),
    "IDEMPOTENCY_PAYLOAD_MISMATCH": (
        409,
        "Samma importnyckel har redan använts för en annan meny.",
    ),
    "PROVISIONING_STEP_MISMATCH": (
        409,
        "Installationen är inte redo för menyimport.",
    ),
    "IMPORT_MENU_STEP_NOT_AVAILABLE": (
        409,
        "Menyimportsteget kan inte köras just nu.",
    ),
    "UNKNOWN_CATEGORY_SOURCE_KEY": (
        422,
        "En maträtt hänvisar till en okänd kategori.",
    ),
}


def _build_menu_payload(
    request: ValidateMenuImportRequest,
) -> dict[str, Any]:
    dumped = request.model_dump(mode="json")

    return {
        "categories": dumped["categories"],
        "items": dumped["items"],
    }


def _calculate_payload_hash(
    menu_payload: dict[str, Any],
) -> str:
    canonical_json = json.dumps(
        menu_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


def _safe_log_value(
    value: object,
    max_length: int = 500,
) -> str | None:
    if value is None:
        return None

    return str(value)[:max_length]


def _map_import_error(error: Exception) -> MenuImportError:
    raw_message = (
        getattr(error, "message", None)
        or str(error)
    )

    for error_code, (
        status_code,
        safe_message,
    ) in SAFE_ERROR_MAP.items():
        if error_code in raw_message:
            return MenuImportError(
                code=error_code,
                message=safe_message,
                status_code=status_code,
            )

    return MenuImportError(
        code="MENU_IMPORT_FAILED",
        message="Menyn kunde inte importeras.",
        status_code=502,
    )


def import_structured_menu(
    request: ValidateMenuImportRequest,
) -> dict[str, Any]:
    """
    Import a previously validated menu through the secure
    Supabase RPC function.

    The Supabase function performs the complete import
    atomically and advances the provisioning job.
    """

    menu_payload = _build_menu_payload(request)
    payload_hash = _calculate_payload_hash(menu_payload)

    try:
        response = get_client().rpc(
            "import_structured_menu",
            {
                "p_restaurant_id": str(
                    request.restaurant_id
                ),
                "p_provisioning_job_id": str(
                    request.provisioning_job_id
                ),
                "p_idempotency_key": str(
                    request.idempotency_key
                ),
                "p_source_type": request.source_type,
                "p_source_filename": (
                    request.source_filename
                ),
                "p_payload_hash": payload_hash,
                "p_payload": menu_payload,
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Supabase structured menu import failed",
            extra={
                "error_type": type(error).__name__,
                "code": _safe_log_value(
                    getattr(error, "code", None),
                    100,
                ),
                "message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error),
                    500,
                ),
                "details": _safe_log_value(
                    getattr(error, "details", None),
                    500,
                ),
                "hint": _safe_log_value(
                    getattr(error, "hint", None),
                    500,
                ),
            },
        )

        raise _map_import_error(error) from error

    data = response.data

    if isinstance(data, list):
        row = data[0] if data else None
    elif isinstance(data, dict):
        row = data
    else:
        row = None

    if not isinstance(row, dict):
        raise MenuImportError(
            code="EMPTY_MENU_IMPORT_RESPONSE",
            message="Menyimporten gav inget giltigt svar.",
            status_code=502,
        )

    required_fields = {
        "import_id",
        "idempotent_replay",
        "category_count",
        "item_count",
        "alias_count",
        "option_group_count",
        "option_count",
        "ingredient_count",
        "allergen_count",
        "next_step",
    }

    if not required_fields.issubset(row):
        raise MenuImportError(
            code="INVALID_MENU_IMPORT_RESPONSE",
            message="Menyimporten gav ett ofullständigt svar.",
            status_code=502,
        )

    return row
    class ImportMenuResponse(MenuSchema):
    success: bool = True

    restaurant_id: UUID
    provisioning_job_id: UUID
    import_id: UUID

    idempotent_replay: bool

    category_count: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)
    alias_count: int = Field(default=0, ge=0)
    option_group_count: int = Field(default=0, ge=0)
    option_count: int = Field(default=0, ge=0)
    ingredient_count: int = Field(default=0, ge=0)
    allergen_count: int = Field(default=0, ge=0)

    next_step: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    warnings: list[MenuValidationIssue] = Field(
        default_factory=list,
    )
