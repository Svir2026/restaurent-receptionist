from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    CancelOrderV2Request,
)
from app.services.supabase_client import get_client
from app.utils.phone import (
    normalize_phone,
    phone_suffix_match,
)


logger = logging.getLogger(__name__)


class RestaurantOrderCancellationError(Exception):
    """Safe error returned by the v2 cancellation service."""

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


RPC_EXCEPTION_MAP: dict[str, tuple[int, str]] = {
    "RESTAURANT_ID_REQUIRED": (
        422,
        "Restaurangkopplingen saknas.",
    ),
    "ORDER_ID_REQUIRED": (
        422,
        "Order-ID saknas.",
    ),
    "INVALID_CUSTOMER_PHONE": (
        422,
        "Kundens telefonnummer har ett ogiltigt format.",
    ),
    "INVALID_EXPECTED_REVISION": (
        409,
        "Beställningens revisionsnummer är ogiltigt.",
    ),
}


RPC_RESULT_MAP: dict[str, tuple[int, str]] = {
    "ORDER_NOT_FOUND": (
        404,
        "Beställningen kunde inte hittas för restaurangen.",
    ),
    "ORDER_NOT_V2": (
        409,
        "Beställningen kan inte avbokas genom v2-flödet.",
    ),
    "ORDER_CALLER_MISMATCH": (
        403,
        "Beställningen tillhör inte kundens telefonnummer.",
    ),
    "ORDER_ALREADY_CANCELLED": (
        409,
        "Beställningen är redan avbokad.",
    ),
    "ORDER_NOT_CANCELLABLE": (
        409,
        "Beställningen kan inte längre avbokas.",
    ),
    "ORDER_REVISION_MISMATCH": (
        409,
        (
            "Beställningen ändrades av en annan process. "
            "Hämta aktuell status och försök igen."
        ),
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
        raise RestaurantOrderCancellationError(
            code="INVALID_CUSTOMER_PHONE",
            message=(
                "Kundens telefonnummer har ett ogiltigt format."
            ),
            status_code=422,
        ) from error


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
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        ) from error


def _parse_non_empty_text(
    value: object,
    *,
    code: str,
    message: str,
) -> str:
    normalized_value = str(
        value or ""
    ).strip()

    if not normalized_value:
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        )

    return normalized_value


def _parse_non_negative_integer(
    value: object,
    *,
    code: str,
    message: str,
) -> int:
    try:
        parsed_value = int(value)

    except (
        TypeError,
        ValueError,
    ) as error:
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        ) from error

    if parsed_value < 0:
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        )

    return parsed_value


def _parse_positive_integer(
    value: object,
) -> int:
    try:
        parsed_value = int(value)

    except (
        TypeError,
        ValueError,
    ) as error:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ITEM_QUANTITY",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt antal."
            ),
        ) from error

    if parsed_value < 1 or parsed_value > 100:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ITEM_QUANTITY",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt antal."
            ),
        )

    return parsed_value


def _parse_non_negative_float(
    value: object,
    *,
    code: str,
    message: str,
) -> float:
    try:
        parsed_value = float(value)

    except (
        TypeError,
        ValueError,
    ) as error:
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        ) from error

    if parsed_value < 0:
        raise RestaurantOrderCancellationError(
            code=code,
            message=message,
        )

    return parsed_value


def _parse_datetime(
    value: object,
    *,
    required: bool,
    field_name: str,
) -> datetime | None:
    if value is None or not str(value).strip():
        if required:
            raise RestaurantOrderCancellationError(
                code="INVALID_CANCELLED_ORDER_DATETIME",
                message=(
                    f"Den avbokade beställningen saknar "
                    f"tiden {field_name}."
                ),
            )

        return None

    if isinstance(value, datetime):
        return value

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

    except ValueError as error:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ORDER_DATETIME",
            message=(
                "Den avbokade beställningen returnerade "
                "en ogiltig tid."
            ),
        ) from error


def _load_current_order(
    *,
    context: ToolRestaurantContext,
    order_id: str,
    normalized_phone: str,
) -> dict[str, Any]:
    """
    Load and verify the current v2 order before cancellation.

    The Supabase RPC repeats all authoritative checks while
    holding the database row lock.
    """

    try:
        response = (
            get_client()
            .table("orders")
            .select(
                (
                    "order_id,"
                    "restaurant_id,"
                    "customer_phone,"
                    "order_status,"
                    "order_revision,"
                    "conversation_id,"
                    "request_hash"
                )
            )
            .eq(
                "restaurant_id",
                str(context.restaurant_id),
            )
            .eq(
                "order_id",
                order_id,
            )
            .limit(1)
            .execute()
        )

    except Exception as error:
        logger.error(
            "Could not read restaurant order before cancellation",
            extra={
                "restaurant_id": str(
                    context.restaurant_id
                ),
                "order_id": order_id,
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise RestaurantOrderCancellationError(
            code="ORDER_READ_FAILED",
            message=(
                "Beställningen kunde inte läsas före "
                "avbokningen."
            ),
            status_code=502,
        ) from error

    row = _extract_first_row(
        response.data
    )

    if row is None:
        raise RestaurantOrderCancellationError(
            code="ORDER_NOT_FOUND",
            message=(
                "Beställningen kunde inte hittas för "
                "restaurangen."
            ),
            status_code=404,
        )

    returned_restaurant_id = _parse_uuid(
        row.get("restaurant_id"),
        code="INVALID_ORDER_RESTAURANT_ID",
        message=(
            "Beställningen innehåller ett ogiltigt "
            "restaurang-ID."
        ),
    )

    if returned_restaurant_id != context.restaurant_id:
        logger.error(
            "Restaurant isolation failure before cancellation",
            extra={
                "expected_restaurant_id": str(
                    context.restaurant_id
                ),
                "returned_restaurant_id": str(
                    returned_restaurant_id
                ),
                "order_id": order_id,
            },
        )

        raise RestaurantOrderCancellationError(
            code="ORDER_RESTAURANT_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "restaurangen."
            ),
        )

    returned_phone = _parse_non_empty_text(
        row.get("customer_phone"),
        code="INVALID_ORDER_CUSTOMER_PHONE",
        message=(
            "Beställningen innehåller ett ogiltigt "
            "telefonnummer."
        ),
    )

    if not phone_suffix_match(
        returned_phone,
        normalized_phone,
    ):
        raise RestaurantOrderCancellationError(
            code="ORDER_CALLER_MISMATCH",
            message=(
                "Beställningen tillhör inte kundens "
                "telefonnummer."
            ),
            status_code=403,
        )

    order_status = _parse_non_empty_text(
        row.get("order_status"),
        code="INVALID_ORDER_STATUS",
        message=(
            "Beställningen innehåller en ogiltig status."
        ),
    ).lower()

    if order_status == "cancelled":
        raise RestaurantOrderCancellationError(
            code="ORDER_ALREADY_CANCELLED",
            message="Beställningen är redan avbokad.",
            status_code=409,
        )

    if order_status != "new order":
        raise RestaurantOrderCancellationError(
            code="ORDER_NOT_CANCELLABLE",
            message=(
                "Beställningen kan inte längre avbokas."
            ),
            status_code=409,
        )

    conversation_id = str(
        row.get("conversation_id")
        or ""
    ).strip()

    request_hash = str(
        row.get("request_hash")
        or ""
    ).strip()

    if (
        not order_id.startswith("v2_")
        or not conversation_id
        or not request_hash
    ):
        raise RestaurantOrderCancellationError(
            code="ORDER_NOT_V2",
            message=(
                "Beställningen kan inte avbokas genom "
                "v2-flödet."
            ),
            status_code=409,
        )

    order_revision = _parse_non_negative_integer(
        row.get("order_revision"),
        code="INVALID_ORDER_REVISION",
        message=(
            "Beställningen innehåller ett ogiltigt "
            "revisionsnummer."
        ),
    )

    return {
        "order_revision": order_revision,
    }


def _map_rpc_exception(
    error: Exception,
) -> RestaurantOrderCancellationError:
    raw_message = (
        getattr(error, "message", None)
        or str(error)
    )

    for error_code, (
        status_code,
        safe_message,
    ) in RPC_EXCEPTION_MAP.items():
        if error_code in raw_message:
            return RestaurantOrderCancellationError(
                code=error_code,
                message=safe_message,
                status_code=status_code,
            )

    return RestaurantOrderCancellationError(
        code="RESTAURANT_ORDER_CANCELLATION_FAILED",
        message="Beställningen kunde inte avbokas.",
        status_code=502,
    )


def _normalize_result_items(
    value: object,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ORDER_ITEMS",
            message=(
                "Den avbokade beställningen returnerade "
                "inga giltiga produkter."
            ),
        )

    normalized_items: list[
        dict[str, Any]
    ] = []

    for item in value:
        if not isinstance(item, dict):
            raise RestaurantOrderCancellationError(
                code="INVALID_CANCELLED_ORDER_ITEMS",
                message=(
                    "Den avbokade beställningen innehåller "
                    "en ogiltig produkt."
                ),
            )

        menu_item_id = _parse_uuid(
            item.get("menu_item_id"),
            code="INVALID_CANCELLED_MENU_ITEM_ID",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt meny-ID."
            ),
        )

        requested_name = _parse_non_empty_text(
            item.get("requested_name")
            or item.get("name"),
            code="INVALID_CANCELLED_ITEM_NAME",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt produktnamn."
            ),
        )

        official_name = _parse_non_empty_text(
            item.get("name"),
            code="INVALID_CANCELLED_ITEM_NAME",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt produktnamn."
            ),
        )

        quantity = _parse_positive_integer(
            item.get("quantity")
        )

        unit_price = _parse_non_negative_float(
            item.get("unit_price"),
            code="INVALID_CANCELLED_ITEM_PRICE",
            message=(
                "Den avbokade beställningen innehåller "
                "ett ogiltigt pris."
            ),
        )

        line_total = _parse_non_negative_float(
            item.get("line_total"),
            code="INVALID_CANCELLED_ITEM_TOTAL",
            message=(
                "Den avbokade beställningen innehåller "
                "en ogiltig radsumma."
            ),
        )

        currency = _parse_non_empty_text(
            item.get("currency"),
            code="INVALID_CANCELLED_ITEM_CURRENCY",
            message=(
                "Den avbokade beställningen innehåller "
                "en ogiltig valuta."
            ),
        ).upper()

        if (
            len(currency) != 3
            or not currency.isalpha()
        ):
            raise RestaurantOrderCancellationError(
                code="INVALID_CANCELLED_ITEM_CURRENCY",
                message=(
                    "Den avbokade beställningen innehåller "
                    "en ogiltig valuta."
                ),
            )

        notes_value = str(
            item.get("notes")
            or ""
        ).strip()

        normalized_items.append(
            {
                "menu_item_id": menu_item_id,
                "requested_name": requested_name,
                "official_name": official_name,
                "quantity": quantity,
                "notes": notes_value or None,
                "unit_price": unit_price,
                "line_total": line_total,
                "currency": currency,
            }
        )

    return normalized_items


def cancel_restaurant_order(
    *,
    context: ToolRestaurantContext,
    request: CancelOrderV2Request,
) -> dict[str, Any]:
    """
    Safely cancel one v2 order for the restaurant resolved from
    X-Svir-Tool-Token.

    The order row is retained. Supabase changes only its status,
    cancellation reason, revision, and updated timestamp.
    """

    normalized_phone = _normalize_customer_phone(
        request.customer_phone
    )

    order_id = request.order_id.strip()

    current_order = _load_current_order(
        context=context,
        order_id=order_id,
        normalized_phone=normalized_phone,
    )

    expected_revision = int(
        current_order["order_revision"]
    )

    try:
        response = get_client().rpc(
            "cancel_restaurant_order_v2",
            {
                "p_restaurant_id": str(
                    context.restaurant_id
                ),
                "p_order_id": order_id,
                "p_customer_phone": normalized_phone,
                "p_expected_revision": (
                    expected_revision
                ),
                "p_reason": request.reason,
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Restaurant-scoped order cancellation failed",
            extra={
                "restaurant_id": str(
                    context.restaurant_id
                ),
                "order_id": order_id,
                "expected_revision": (
                    expected_revision
                ),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise _map_rpc_exception(
            error
        ) from error

    result = _extract_first_row(
        response.data
    )

    if result is None:
        raise RestaurantOrderCancellationError(
            code="EMPTY_ORDER_CANCELLATION_RESPONSE",
            message=(
                "Avbokningen gav inget giltigt svar."
            ),
        )

    required_fields = {
        "applied",
        "result_code",
        "result_restaurant_id",
        "result_order_id",
        "result_order_status",
        "result_order_revision",
        "result_customer_name",
        "result_customer_phone",
        "result_order_type",
        "result_created_at",
        "result_updated_at",
        "result_dine_in_time",
        "result_pickup_time",
        "result_currency",
        "result_total",
        "result_items",
        "result_notes",
        "result_cancellation_reason",
    }

    if not required_fields.issubset(
        result
    ):
        raise RestaurantOrderCancellationError(
            code="INCOMPLETE_ORDER_CANCELLATION_RESPONSE",
            message=(
                "Avbokningen gav ett ofullständigt svar."
            ),
        )

    returned_restaurant_id = _parse_uuid(
        result.get(
            "result_restaurant_id"
        ),
        code="INVALID_CANCELLED_RESTAURANT_ID",
        message=(
            "Avbokningen returnerade ett ogiltigt "
            "restaurang-ID."
        ),
    )

    if returned_restaurant_id != context.restaurant_id:
        logger.error(
            "Restaurant isolation failure in cancellation response",
            extra={
                "expected_restaurant_id": str(
                    context.restaurant_id
                ),
                "returned_restaurant_id": str(
                    returned_restaurant_id
                ),
                "order_id": order_id,
            },
        )

        raise RestaurantOrderCancellationError(
            code="ORDER_RESTAURANT_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "restaurangen."
            ),
        )

    returned_order_id = _parse_non_empty_text(
        result.get("result_order_id"),
        code="INVALID_CANCELLED_ORDER_ID",
        message=(
            "Avbokningen returnerade ett ogiltigt order-ID."
        ),
    )

    if returned_order_id != order_id:
        raise RestaurantOrderCancellationError(
            code="ORDER_ID_MISMATCH",
            message=(
                "Avbokningen returnerade fel beställning."
            ),
        )

    result_code = str(
        result.get("result_code")
        or ""
    ).strip()

    applied = bool(
        result.get("applied")
    )

    if result_code in RPC_RESULT_MAP:
        status_code, safe_message = (
            RPC_RESULT_MAP[result_code]
        )

        raise RestaurantOrderCancellationError(
            code=result_code,
            message=safe_message,
            status_code=status_code,
        )

    if (
        result_code != "ORDER_CANCELLED"
        or not applied
    ):
        raise RestaurantOrderCancellationError(
            code=(
                result_code
                or "ORDER_CANCELLATION_NOT_APPLIED"
            ),
            message="Beställningen kunde inte avbokas.",
            status_code=409,
        )

    order_status = _parse_non_empty_text(
        result.get(
            "result_order_status"
        ),
        code="INVALID_CANCELLED_ORDER_STATUS",
        message=(
            "Avbokningen returnerade en ogiltig status."
        ),
    ).lower()

    if order_status != "cancelled":
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ORDER_STATUS",
            message=(
                "Avbokningen returnerade en ogiltig status."
            ),
        )

    order_revision = _parse_non_negative_integer(
        result.get(
            "result_order_revision"
        ),
        code="INVALID_CANCELLED_ORDER_REVISION",
        message=(
            "Avbokningen returnerade ett ogiltigt "
            "revisionsnummer."
        ),
    )

    if order_revision != expected_revision + 1:
        raise RestaurantOrderCancellationError(
            code="CANCELLED_ORDER_REVISION_MISMATCH",
            message=(
                "Avbokningens revisionsnummer kunde inte "
                "verifieras."
            ),
        )

    customer_name = _parse_non_empty_text(
        result.get(
            "result_customer_name"
        ),
        code="INVALID_CANCELLED_CUSTOMER_NAME",
        message=(
            "Avbokningen returnerade ett ogiltigt kundnamn."
        ),
    )

    returned_phone = _parse_non_empty_text(
        result.get(
            "result_customer_phone"
        ),
        code="INVALID_CANCELLED_CUSTOMER_PHONE",
        message=(
            "Avbokningen returnerade ett ogiltigt "
            "telefonnummer."
        ),
    )

    if not phone_suffix_match(
        returned_phone,
        normalized_phone,
    ):
        raise RestaurantOrderCancellationError(
            code="ORDER_CALLER_MISMATCH",
            message=(
                "Beställningen tillhör inte kundens "
                "telefonnummer."
            ),
            status_code=403,
        )

    order_type = _parse_non_empty_text(
        result.get(
            "result_order_type"
        ),
        code="INVALID_CANCELLED_ORDER_TYPE",
        message=(
            "Avbokningen returnerade en ogiltig "
            "beställningstyp."
        ),
    )

    if order_type not in {
        "takeaway",
        "dine_in",
    }:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ORDER_TYPE",
            message=(
                "Avbokningen returnerade en ogiltig "
                "beställningstyp."
            ),
        )

    created_at = _parse_datetime(
        result.get(
            "result_created_at"
        ),
        required=True,
        field_name="created_at",
    )

    updated_at = _parse_datetime(
        result.get(
            "result_updated_at"
        ),
        required=True,
        field_name="updated_at",
    )

    if created_at is None or updated_at is None:
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_ORDER_DATETIME",
            message=(
                "Avbokningen returnerade en ogiltig tid."
            ),
        )

    dine_in_time = _parse_datetime(
        result.get(
            "result_dine_in_time"
        ),
        required=False,
        field_name="dine_in_time",
    )

    pickup_time = _parse_datetime(
        result.get(
            "result_pickup_time"
        ),
        required=False,
        field_name="pickup_time",
    )

    if (
        order_type == "dine_in"
        and dine_in_time is None
    ):
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_DINE_IN_TIME",
            message=(
                "Den avbokade beställningen saknar "
                "ankomsttid."
            ),
        )

    currency = _parse_non_empty_text(
        result.get(
            "result_currency"
        ),
        code="INVALID_CANCELLED_CURRENCY",
        message=(
            "Avbokningen returnerade en ogiltig valuta."
        ),
    ).upper()

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise RestaurantOrderCancellationError(
            code="INVALID_CANCELLED_CURRENCY",
            message=(
                "Avbokningen returnerade en ogiltig valuta."
            ),
        )

    total = _parse_non_negative_float(
        result.get("result_total"),
        code="INVALID_CANCELLED_TOTAL",
        message=(
            "Avbokningen returnerade en ogiltig total."
        ),
    )

    notes_value = str(
        result.get("result_notes")
        or ""
    ).strip()

    cancellation_reason_value = str(
        result.get(
            "result_cancellation_reason"
        )
        or ""
    ).strip()

    return {
        "success": True,
        "cancelled": True,
        "restaurant_id": (
            context.restaurant_id
        ),
        "restaurant_name": (
            context.restaurant_name
        ),
        "order_id": order_id,
        "order_status": "cancelled",
        "order_revision": order_revision,
        "customer_name": customer_name,
        "customer_phone": returned_phone,
        "order_type": order_type,
        "created_at": created_at,
        "updated_at": updated_at,
        "dine_in_time": dine_in_time,
        "pickup_time": pickup_time,
        "currency": currency,
        "total": total,
        "items": _normalize_result_items(
            result.get("result_items")
        ),
        "notes": notes_value or None,
        "cancellation_reason": (
            cancellation_reason_value
            or None
        ),
    }