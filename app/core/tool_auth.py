from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Header, HTTPException

from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

TOOL_TOKEN_HEADER = "X-Svir-Tool-Token"
TOOL_TOKEN_PREFIX = "svir_tool_"

ALLOWED_PROVISIONING_STATUSES = {
    "pending",
    "in_progress",
}


@dataclass(frozen=True)
class ToolRestaurantContext:
    """
    Server-resolved restaurant context for a v2 tool request.

    The caller never supplies restaurant_id directly.
    Railway resolves it from X-Svir-Tool-Token.
    """

    credential_id: UUID
    restaurant_id: UUID
    restaurant_name: str
    restaurant_slug: str

    restaurant_is_active: bool

    provisioning_job_id: UUID | None
    provisioning_job_status: str | None
    provisioning_current_step: str | None


def _unauthorized_tool_token() -> HTTPException:
    """
    Return the same response for missing, malformed, revoked,
    or unknown tokens.

    This avoids revealing whether a particular token exists.
    """

    return HTTPException(
        status_code=401,
        detail={
            "code": "INVALID_TOOL_TOKEN",
            "message": "Verktygsanropet kunde inte autentiseras.",
        },
    )


def _tool_auth_unavailable() -> HTTPException:
    """
    Used when Railway cannot safely verify the token because
    Supabase or the authentication configuration is unavailable.
    """

    return HTTPException(
        status_code=503,
        detail={
            "code": "TOOL_AUTH_UNAVAILABLE",
            "message": (
                "Verktygsautentiseringen är tillfälligt "
                "otillgänglig."
            ),
        },
    )


def _restaurant_not_available() -> HTTPException:
    """
    Used when a valid token belongs to a restaurant that is
    neither active nor undergoing a valid provisioning job.
    """

    return HTTPException(
        status_code=403,
        detail={
            "code": "RESTAURANT_NOT_AVAILABLE",
            "message": (
                "Restaurangen är inte tillgänglig för "
                "verktygsanrop."
            ),
        },
    )


def _extract_first_row(
    data: object,
) -> dict[str, Any] | None:
    """
    Normalize a Supabase RPC or table response into one row.
    """

    if isinstance(data, list):
        row = data[0] if data else None
    elif isinstance(data, dict):
        row = data
    else:
        row = None

    return row if isinstance(row, dict) else None


def _parse_uuid(
    value: object,
    *,
    field_name: str,
) -> UUID:
    """
    Parse a UUID returned by the trusted Supabase backend.

    Invalid backend data is treated as an authentication-system
    failure rather than as an invalid caller token.
    """

    try:
        return UUID(str(value))

    except (TypeError, ValueError, AttributeError) as error:
        logger.error(
            "Invalid UUID returned by tool authentication backend",
            extra={
                "field_name": field_name,
                "error_type": type(error).__name__,
            },
        )

        raise _tool_auth_unavailable() from error


def _parse_optional_uuid(
    value: object,
    *,
    field_name: str,
) -> UUID | None:
    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    return _parse_uuid(
        normalized,
        field_name=field_name,
    )


def _resolve_token_row(
    tool_token: str,
) -> dict[str, Any]:
    """
    Resolve the plaintext token through the secure Supabase RPC.

    The plaintext token is never written to logs or returned.
    """

    try:
        response = get_client().rpc(
            "resolve_restaurant_tool_token",
            {
                "p_tool_token": tool_token,
            },
        ).execute()

    except Exception as error:
        logger.exception(
            "Restaurant tool token resolution failed"
        )

        raise _tool_auth_unavailable() from error

    row = _extract_first_row(response.data)

    if row is None:
        raise _unauthorized_tool_token()

    required_fields = {
        "resolved_credential_id",
        "resolved_restaurant_id",
        "resolved_restaurant_name",
        "resolved_restaurant_slug",
        "resolved_is_active",
        "resolved_provisioning_job_id",
    }

    if not required_fields.issubset(row):
        logger.error(
            "Incomplete restaurant tool token response",
            extra={
                "returned_fields": sorted(
                    str(key) for key in row.keys()
                ),
            },
        )

        raise _tool_auth_unavailable()

    return row


def _load_provisioning_job(
    provisioning_job_id: UUID,
) -> dict[str, Any] | None:
    """
    Read the provisioning job used to temporarily authorize an
    inactive restaurant during setup and testing.
    """

    try:
        response = (
            get_client()
            .table("provisioning_jobs")
            .select(
                "id,restaurant_id,status,current_step"
            )
            .eq("id", str(provisioning_job_id))
            .limit(1)
            .execute()
        )

    except Exception as error:
        logger.exception(
            "Could not read provisioning job for tool auth",
            extra={
                "provisioning_job_id": str(
                    provisioning_job_id
                ),
            },
        )

        raise _tool_auth_unavailable() from error

    return _extract_first_row(response.data)


def _build_restaurant_context(
    resolved_row: dict[str, Any],
) -> ToolRestaurantContext:
    credential_id = _parse_uuid(
        resolved_row.get("resolved_credential_id"),
        field_name="resolved_credential_id",
    )

    restaurant_id = _parse_uuid(
        resolved_row.get("resolved_restaurant_id"),
        field_name="resolved_restaurant_id",
    )

    restaurant_name = str(
        resolved_row.get("resolved_restaurant_name")
        or ""
    ).strip()

    restaurant_slug = str(
        resolved_row.get("resolved_restaurant_slug")
        or ""
    ).strip()

    if not restaurant_name or not restaurant_slug:
        logger.error(
            "Restaurant identity missing from tool auth response",
            extra={
                "credential_id": str(credential_id),
                "restaurant_id": str(restaurant_id),
            },
        )

        raise _tool_auth_unavailable()

    restaurant_is_active = (
        resolved_row.get("resolved_is_active") is True
    )

    provisioning_job_id = _parse_optional_uuid(
        resolved_row.get(
            "resolved_provisioning_job_id"
        ),
        field_name="resolved_provisioning_job_id",
    )

    # Active restaurants may use their tools without requiring
    # an active provisioning job.
    if restaurant_is_active:
        return ToolRestaurantContext(
            credential_id=credential_id,
            restaurant_id=restaurant_id,
            restaurant_name=restaurant_name,
            restaurant_slug=restaurant_slug,
            restaurant_is_active=True,
            provisioning_job_id=provisioning_job_id,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )

    # Inactive restaurants are allowed only during a valid,
    # ongoing provisioning job.
    if provisioning_job_id is None:
        raise _restaurant_not_available()

    job = _load_provisioning_job(
        provisioning_job_id
    )

    if job is None:
        raise _restaurant_not_available()

    job_restaurant_id = _parse_uuid(
        job.get("restaurant_id"),
        field_name="provisioning_job.restaurant_id",
    )

    if job_restaurant_id != restaurant_id:
        logger.warning(
            "Provisioning restaurant mismatch in tool auth",
            extra={
                "credential_id": str(credential_id),
                "restaurant_id": str(restaurant_id),
                "provisioning_job_id": str(
                    provisioning_job_id
                ),
                "job_restaurant_id": str(
                    job_restaurant_id
                ),
            },
        )

        raise _restaurant_not_available()

    job_status = str(
        job.get("status") or ""
    ).strip()

    current_step_value = job.get("current_step")

    current_step = (
        str(current_step_value).strip()
        if current_step_value is not None
        else None
    )

    if job_status not in ALLOWED_PROVISIONING_STATUSES:
        raise _restaurant_not_available()

    return ToolRestaurantContext(
        credential_id=credential_id,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_name,
        restaurant_slug=restaurant_slug,
        restaurant_is_active=False,
        provisioning_job_id=provisioning_job_id,
        provisioning_job_status=job_status,
        provisioning_current_step=current_step,
    )


def require_restaurant_tool_context(
    tool_token: Annotated[
        str | None,
        Header(
            alias=TOOL_TOKEN_HEADER,
            convert_underscores=False,
        ),
    ] = None,
) -> ToolRestaurantContext:
    """
    FastAPI dependency for all restaurant-specific v2 tools.

    Security flow:
    1. Read X-Svir-Tool-Token.
    2. Resolve its SHA-256 hash through Supabase.
    3. Obtain restaurant_id server-side.
    4. Allow an active restaurant, or an inactive restaurant
       with a valid ongoing provisioning job.
    5. Never accept restaurant_id from the request body.
    """

    normalized_token = (
        str(tool_token).strip()
        if tool_token is not None
        else ""
    )

    if (
        not normalized_token
        or not normalized_token.startswith(
            TOOL_TOKEN_PREFIX
        )
        or len(normalized_token) < 32
        or len(normalized_token) > 200
    ):
        raise _unauthorized_tool_token()

    resolved_row = _resolve_token_row(
        normalized_token
    )

    return _build_restaurant_context(
        resolved_row
    )
