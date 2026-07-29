from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

TOOL_TOKEN_PATTERN = re.compile(
    r"^svir_tool_[0-9a-f]{64}$"
)


class RestaurantToolTokenProviderError(Exception):
    """
    Safe error returned when a restaurant token
    cannot be loaded from Vault.
    """

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


def _extract_exactly_one_row(
    data: object,
) -> dict[str, Any]:
    if isinstance(data, list):
        if len(data) != 1:
            raise RestaurantToolTokenProviderError(
                code="INVALID_TOOL_TOKEN_RESULT_COUNT",
                message=(
                    "Tokenhämtningen returnerade inte exakt "
                    "ett giltigt resultat."
                ),
            )

        row = data[0]

    elif isinstance(data, dict):
        row = data

    else:
        row = None

    if not isinstance(row, dict):
        raise RestaurantToolTokenProviderError(
            code="INVALID_TOOL_TOKEN_RESPONSE",
            message=(
                "Tokenhämtningen gav ett ogiltigt svar."
            ),
        )

    return row


def _parse_uuid(
    value: object,
    *,
    code: str,
    message: str,
) -> UUID:
    try:
        return UUID(str(value))

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as error:
        raise RestaurantToolTokenProviderError(
            code=code,
            message=message,
        ) from error


def get_restaurant_tool_token_from_vault(
    restaurant_id: UUID,
) -> str:
    """
    Load one hash-verified restaurant tool token
    from Supabase Vault.

    The RPC is executable only by service_role.

    The plaintext token is returned only to the
    calling server-side code. This function never
    logs, caches or persists the token.
    """

    if not isinstance(restaurant_id, UUID):
        raise RestaurantToolTokenProviderError(
            code="INVALID_RESTAURANT_ID",
            message=(
                "Restaurang-ID har ett ogiltigt format."
            ),
            status_code=422,
        )

    try:
        response = get_client().rpc(
            "get_restaurant_tool_token_from_vault",
            {
                "p_restaurant_id": str(
                    restaurant_id
                ),
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Could not load restaurant tool token "
            "from Vault",
            extra={
                "restaurant_id": str(
                    restaurant_id
                ),
                "error_type": type(error).__name__,
            },
        )

        raise RestaurantToolTokenProviderError(
            code="TOOL_TOKEN_VAULT_READ_FAILED",
            message=(
                "Restaurangens verktygstoken kunde "
                "inte hämtas från den säkra lagringen."
            ),
            status_code=502,
        ) from error

    row = _extract_exactly_one_row(
        response.data
    )

    required_fields = {
        "restaurant_id",
        "credential_id",
        "vault_secret_id",
        "tool_token",
        "token_last4",
    }

    if not required_fields.issubset(row):
        raise RestaurantToolTokenProviderError(
            code="INCOMPLETE_TOOL_TOKEN_RESPONSE",
            message=(
                "Tokenhämtningen gav ett "
                "ofullständigt svar."
            ),
        )

    returned_restaurant_id = _parse_uuid(
        row.get("restaurant_id"),
        code="INVALID_TOOL_TOKEN_RESTAURANT_ID",
        message=(
            "Tokenhämtningen returnerade ett "
            "ogiltigt restaurang-ID."
        ),
    )

    if returned_restaurant_id != restaurant_id:
        logger.error(
            "Restaurant isolation failure in "
            "Vault token response",
            extra={
                "expected_restaurant_id": str(
                    restaurant_id
                ),
                "returned_restaurant_id": str(
                    returned_restaurant_id
                ),
            },
        )

        raise RestaurantToolTokenProviderError(
            code="TOOL_TOKEN_RESTAURANT_MISMATCH",
            message=(
                "Verktygstoken kunde inte verifieras "
                "mot restaurangen."
            ),
        )

    _parse_uuid(
        row.get("credential_id"),
        code="INVALID_TOOL_TOKEN_CREDENTIAL_ID",
        message=(
            "Tokenhämtningen returnerade ett "
            "ogiltigt credential-ID."
        ),
    )

    _parse_uuid(
        row.get("vault_secret_id"),
        code="INVALID_TOOL_TOKEN_VAULT_SECRET_ID",
        message=(
            "Tokenhämtningen returnerade ett "
            "ogiltigt Vault-secret-ID."
        ),
    )

    tool_token = str(
        row.get("tool_token") or ""
    ).strip()

    token_last4 = str(
        row.get("token_last4") or ""
    ).strip()

    if (
        TOOL_TOKEN_PATTERN.fullmatch(
            tool_token
        )
        is None
    ):
        raise RestaurantToolTokenProviderError(
            code="INVALID_TOOL_TOKEN_FORMAT",
            message=(
                "Den hämtade verktygstoken har "
                "ett ogiltigt format."
            ),
        )

    if len(token_last4) != 4:
        raise RestaurantToolTokenProviderError(
            code="INVALID_TOOL_TOKEN_LAST4",
            message=(
                "Tokenhämtningen returnerade en "
                "ogiltig kontrollkod."
            ),
        )

    if not tool_token.endswith(token_last4):
        raise RestaurantToolTokenProviderError(
            code="TOOL_TOKEN_LAST4_MISMATCH",
            message=(
                "Den hämtade verktygstoken kunde "
                "inte verifieras."
            ),
        )

    return tool_token
