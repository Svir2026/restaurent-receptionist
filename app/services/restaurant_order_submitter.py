from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    CalculateOrderTotalV2Request,
    SubmitOrderV2Request,
)
from app.services.restaurant_menu_pricing import (
    RestaurantMenuPricingError,
    calculate_restaurant_menu_total,
)
from app.services.supabase_client import get_client
from app.utils.phone import normalize_phone


logger = logging.getLogger(__name__)


class RestaurantOrderSubmissionError(Exception):
    """Safe error returned by the v2 order submission service."""

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


SAFE_RPC_ERROR_MAP: dict[str, tuple[int, str]] = {
    "RESTAURANT_NOT_FOUND": (
        404,
        "Restaurangen kunde inte hittas.",
    ),
    "CONVERSATION_ID_REQUIRED": (
        422,
        "Samtals-ID saknas.",
    ),
    "INVALID_CONVERSATION_ID": (
        422,
        "Samtals-ID har ett ogiltigt format.",
    ),
    "CUSTOMER_NAME_REQUIRED": (
        422,
        "Kundens namn saknas.",
    ),
    "CUSTOMER_PHONE_REQUIRED": (
        422,
        "Kundens telefonnummer saknas.",
    ),
    "INVALID_ORDER_TYPE": (
        422,
        "Beställningstypen är ogiltig.",
    ),
    "PICKUP_TIME_REQUIRED": (
        422,
        "Hämtningstid krävs för avhämtning.",
    ),
    "DINE_IN_TIME_REQUIRED": (
        422,
        "Ankomsttid krävs för att äta på plats.",
    ),
    "DINE_IN_TIME_NOT_ALLOWED": (
        422,
        "Ankomsttid får inte skickas för avhämtning.",
    ),
    "PICKUP_TIME_NOT_ALLOWED": (
        422,
        "Hämtningstid får inte skickas för att äta på plats.",
    ),
    "INVALID_PARTY_SIZE": (
        422,
        "Antalet gäster är ogiltigt.",
    ),
    "ORDER_ITEMS_REQUIRED": (
        422,
        "Beställningen måste innehålla minst en produkt.",
    ),
    "INVALID_ORDER_ITEM": (
        422,
        "Beställningen innehåller en ogiltig produkt.",
    ),
    "INVALID_MENU_ITEM_ID": (
        422,
        "En produkt har ett ogiltigt meny-ID.",
    ),
    "INVALID_ORDER_ITEM_QUANTITY": (
        422,
        "En produkt har ett ogiltigt antal.",
    ),
    "MENU_ITEM_NOT_FOUND": (
        422,
        "En produkt finns inte i restaurangens aktiva meny.",
    ),
    "INVALID_MENU_ITEM_PRICE": (
        502,
        "En produkt i menyn saknar ett giltigt pris.",
    ),
    "INVALID_MENU_ITEM_CURRENCY": (
        502,
        "En produkt i menyn saknar en giltig valuta.",
    ),
    "MIXED_MENU_CURRENCIES": (
        409,
        "Beställningen innehåller produkter med olika valutor.",
    ),
}


def _safe_log_value(
    value: object,
    max_length: int = 500,
) -> str | None:
    if value is None:
        return None
    return str(value)[:max_length]


def _extract_first_row(
    data: object,
) -> dict[str, Any] | None:
    if isinstance(data, list):
        row = data[0] if data else None
    elif isinstance(data, dict):
        row = data
    else:
        row = None

    return row if isinstance(row, dict) else None


def _normalize_customer_phone(
    value: str,
) -> str:
    try:
        return normalize_phone(value)
    except ValueError as error:
        raise RestaurantOrderSubmissionError(
            code="INVALID_CUSTOMER_PHONE",
            message=(
                "Kundens telefonnummer har ett ogiltigt format."
            ),
            status_code=422,
        ) from error


def _build_pricing_request(
    request: SubmitOrderV2Request,
) -> CalculateOrderTotalV2Request:
    return CalculateOrderTotalV2Request.model_validate(
        {
            "order_items": [
                {
                    "name": item.name,
                    "quantity": item.quantity,
                }
                for item in request.order_items
            ]
        }
    )


def _build_verified_rpc_items(
    *,
    request: SubmitOrderV2Request,
    pricing_result: dict[str, Any],
) -> list[dict[str, Any]]:
    verified_items = pricing_result.get("items")

    if not isinstance(verified_items, list):
        raise RestaurantOrderSubmissionError(
            code="INVALID_PRICING_RESPONSE",
            message="Prisverifieringen gav ett ogiltigt svar.",
        )

    if len(verified_items) != len(request.order_items):
        raise RestaurantOrderSubmissionError(
            code="PRICING_ITEM_COUNT_MISMATCH",
            message=(
                "Prisverifieringen matchar inte beställningens "
                "produkter."
            ),
        )

    rpc_items: list[dict[str, Any]] = []

    for requested_item, verified_item in zip(
        request.order_items,
        verified_items,
        strict=True,
    ):
        if not isinstance(verified_item, dict):
            raise RestaurantOrderSubmissionError(
                code="INVALID_PRICING_RESPONSE",
                message=(
                    "Prisverifieringen innehåller en ogiltig "
                    "produkt."
                ),
            )

        try:
            menu_item_id = str(
                UUID(str(verified_item.get("menu_item_id")))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as error:
            raise RestaurantOrderSubmissionError(
                code="INVALID_VERIFIED_MENU_ITEM_ID",
                message=(
                    "Prisverifieringen innehåller ett ogiltigt "
                    "meny-ID."
                ),
            ) from error

        if (
            verified_item.get("quantity")
            != requested_item.quantity
        ):
            raise RestaurantOrderSubmissionError(
                code="PRICING_QUANTITY_MISMATCH",
                message=(
                    "Prisverifieringen matchar inte produktens "
                    "antal."
                ),
            )

        rpc_items.append(
            {
                "menu_item_id": menu_item_id,
                "requested_name": requested_item.name,
                "quantity": requested_item.quantity,
                "notes": requested_item.notes,
            }
        )

    return rpc_items


def _map_rpc_error(
    error: Exception,
) -> RestaurantOrderSubmissionError:
    raw_message = (
        getattr(error, "message", None)
        or str(error)
    )

    for error_code, (
        status_code,
        safe_message,
    ) in SAFE_RPC_ERROR_MAP.items():
        if error_code in raw_message:
            return RestaurantOrderSubmissionError(
                code=error_code,
                message=safe_message,
                status_code=status_code,
            )

    return RestaurantOrderSubmissionError(
        code="RESTAURANT_ORDER_SUBMISSION_FAILED",
        message="Beställningen kunde inte sparas.",
        status_code=502,
    )


def _parse_datetime(
    value: object,
    *,
    required: bool,
    field_name: str,
) -> datetime | None:
    if value is None or not str(value).strip():
        if required:
            raise RestaurantOrderSubmissionError(
                code="MISSING_ORDER_DATETIME",
                message=(
                    f"Beställningen saknar tiden {field_name}."
                ),
            )
        return None

    if isinstance(value, datetime):
        return value

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise RestaurantOrderSubmissionError(
            code="INVALID_ORDER_DATETIME_RESPONSE",
            message=(
                "Beställningen returnerade en ogiltig tid."
            ),
        ) from error


def _normalize_result_items(
    value: object,
) -> list[dict[str, Any]]:
    """
    Convert Supabase's stored item shape into the public v2
    response shape. Internal notes are intentionally omitted.
    """

    if not isinstance(value, list) or not value:
        raise RestaurantOrderSubmissionError(
            code="INVALID_ORDER_ITEMS_RESPONSE",
            message=(
                "Beställningen returnerade inga giltiga "
                "produkter."
            ),
        )

    normalized_items: list[dict[str, Any]] = []

    for item in value:
        if not isinstance(item, dict):
            raise RestaurantOrderSubmissionError(
                code="INVALID_ORDER_ITEMS_RESPONSE",
                message=(
                    "Beställningen returnerade en ogiltig "
                    "produkt."
                ),
            )

        try:
            menu_item_id = UUID(
                str(item.get("menu_item_id"))
            )
            quantity = int(item.get("quantity"))
            unit_price = float(item.get("unit_price"))
            line_total = float(item.get("line_total"))
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as error:
            raise RestaurantOrderSubmissionError(
                code="INVALID_ORDER_ITEMS_RESPONSE",
                message=(
                    "Beställningen returnerade en ogiltig "
                    "prisrad."
                ),
            ) from error

        requested_name = str(
            item.get("requested_name")
            or item.get("name")
            or ""
        ).strip()

        official_name = str(
            item.get("name")
            or ""
        ).strip()

        currency = str(
            item.get("currency")
            or ""
        ).strip().upper()

        if (
            not requested_name
            or not official_name
            or quantity < 1
            or unit_price < 0
            or line_total < 0
            or len(currency) != 3
        ):
            raise RestaurantOrderSubmissionError(
                code="INVALID_ORDER_ITEMS_RESPONSE",
                message=(
                    "Beställningen returnerade en ofullständig "
                    "prisrad."
                ),
            )

        normalized_items.append(
            {
                "menu_item_id": menu_item_id,
                "requested_name": requested_name,
                "official_name": official_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "currency": currency,
            }
        )

    return normalized_items


def submit_restaurant_order(
    *,
    context: ToolRestaurantContext,
    request: SubmitOrderV2Request,
) -> dict[str, Any]:
    """
    Verify prices and atomically submit one order for the
    restaurant resolved from X-Svir-Tool-Token.
    """

    normalized_phone = _normalize_customer_phone(
        request.customer_phone
    )

    pricing_request = _build_pricing_request(
        request
    )

    try:
        pricing_result = calculate_restaurant_menu_total(
            context=context,
            request=pricing_request,
        )
    except RestaurantMenuPricingError as error:
        raise RestaurantOrderSubmissionError(
            code=error.code,
            message=error.message,
            status_code=error.status_code,
        ) from error

    rpc_items = _build_verified_rpc_items(
        request=request,
        pricing_result=pricing_result,
    )

    try:
        response = get_client().rpc(
            "submit_restaurant_order_v2",
            {
                "p_restaurant_id": str(
                    context.restaurant_id
                ),
                "p_conversation_id": (
                    request.conversation_id
                ),
                "p_customer_name": (
                    request.customer_name
                ),
                "p_customer_phone": normalized_phone,
                "p_order_type": request.order_type,
                "p_order_items": rpc_items,
                "p_party_size": request.party_size,
                "p_dine_in_time": (
                    request.dine_in_time.isoformat()
                    if request.dine_in_time
                    else None
                ),
                "p_pickup_time": (
                    request.pickup_time.isoformat()
                    if request.pickup_time
                    else None
                ),
                "p_notes": request.notes,
                "p_source": "elevenlabs_v2",
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Restaurant-scoped order submission failed",
            extra={
                "restaurant_id": str(
                    context.restaurant_id
                ),
                "conversation_id": (
                    request.conversation_id
                ),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )
        raise _map_rpc_error(error) from error

    result = _extract_first_row(response.data)

    if result is None:
        raise RestaurantOrderSubmissionError(
            code="EMPTY_ORDER_SUBMISSION_RESPONSE",
            message="Beställningen gav inget giltigt svar.",
        )

    required_fields = {
        "applied",
        "idempotent_replay",
        "result_code",
        "result_restaurant_id",
        "result_order_id",
        "result_order_status",
        "result_created_at",
        "result_order_type",
        "result_customer_name",
        "result_currency",
        "result_total",
        "result_items",
    }

    if not required_fields.issubset(result):
        raise RestaurantOrderSubmissionError(
            code="INCOMPLETE_ORDER_SUBMISSION_RESPONSE",
            message=(
                "Beställningen gav ett ofullständigt svar."
            ),
        )

    returned_restaurant_id = str(
        result.get("result_restaurant_id") or ""
    )

    if returned_restaurant_id != str(
        context.restaurant_id
    ):
        logger.error(
            "Restaurant isolation failure in order response",
            extra={
                "expected_restaurant_id": str(
                    context.restaurant_id
                ),
                "returned_restaurant_id": (
                    returned_restaurant_id
                ),
                "conversation_id": (
                    request.conversation_id
                ),
            },
        )
        raise RestaurantOrderSubmissionError(
            code="ORDER_RESTAURANT_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "restaurangen."
            ),
        )

    applied = bool(result.get("applied"))
    idempotent_replay = bool(
        result.get("idempotent_replay")
    )
    result_code = str(
        result.get("result_code") or ""
    )

    if result_code == "CONVERSATION_PAYLOAD_MISMATCH":
        raise RestaurantOrderSubmissionError(
            code=result_code,
            message=(
                "Samma samtal har redan använts för en annan "
                "version av beställningen."
            ),
            status_code=409,
        )

    if not applied and not idempotent_replay:
        raise RestaurantOrderSubmissionError(
            code=(
                result_code
                or "ORDER_SUBMISSION_NOT_APPLIED"
            ),
            message="Beställningen kunde inte sparas.",
            status_code=409,
        )

    order_id = str(
        result.get("result_order_id") or ""
    ).strip()

    order_status = str(
        result.get("result_order_status") or ""
    ).strip()

    order_type = str(
        result.get("result_order_type") or ""
    ).strip()

    customer_name = str(
        result.get("result_customer_name") or ""
    ).strip()

    currency = str(
        result.get("result_currency") or ""
    ).strip().upper()

    try:
        total = float(result.get("result_total"))
    except (TypeError, ValueError) as error:
        raise RestaurantOrderSubmissionError(
            code="INVALID_ORDER_TOTAL_RESPONSE",
            message=(
                "Beställningen returnerade en ogiltig total."
            ),
        ) from error

    if (
        not order_id
        or order_status != "new order"
        or order_type not in {"takeaway", "dine_in"}
        or not customer_name
        or len(currency) != 3
        or total < 0
    ):
        raise RestaurantOrderSubmissionError(
            code="INVALID_ORDER_SUBMISSION_RESPONSE",
            message=(
                "Beställningen returnerade ett ogiltigt svar."
            ),
        )

    return {
        "success": True,
        "idempotent_replay": idempotent_replay,
        "restaurant_id": context.restaurant_id,
        "restaurant_name": context.restaurant_name,
        "order_id": order_id,
        "order_status": order_status,
        "order_type": order_type,
        "customer_name": customer_name,
        "created_at": _parse_datetime(
            result.get("result_created_at"),
            required=True,
            field_name="created_at",
        ),
        "dine_in_time": _parse_datetime(
            result.get("result_dine_in_time"),
            required=False,
            field_name="dine_in_time",
        ),
        "pickup_time": _parse_datetime(
            result.get("result_pickup_time"),
            required=False,
            field_name="pickup_time",
        ),
        "currency": currency,
        "total": total,
        "items": _normalize_result_items(
            result.get("result_items")
        ),
    }
