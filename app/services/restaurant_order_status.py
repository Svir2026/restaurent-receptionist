from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    CheckOrderStatusV2Request,
)
from app.services.supabase_client import get_client
from app.utils.phone import (
    digits_only,
    normalize_phone,
    phone_suffix_match,
)
from app.utils.time import (
    coerce_to_tz,
    make_window,
    tz_now,
)


logger = logging.getLogger(__name__)

PHONE_SUFFIX_LENGTH = 10
MAX_CANDIDATE_ROWS = 100


class RestaurantOrderStatusError(Exception):
    """Safe error returned by the v2 order-status service."""

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


def _safe_log_value(
    value: object,
    max_length: int = 500,
) -> str | None:
    if value is None:
        return None

    return str(value)[:max_length]


def _normalize_customer_phone(
    value: str,
) -> str:
    try:
        return normalize_phone(value)

    except ValueError as error:
        raise RestaurantOrderStatusError(
            code="INVALID_CUSTOMER_PHONE",
            message=(
                "Kundens telefonnummer har ett ogiltigt format."
            ),
            status_code=422,
        ) from error


def _parse_datetime(
    value: object,
    *,
    field_name: str,
    required: bool,
) -> datetime | None:
    if value is None:
        if required:
            raise RestaurantOrderStatusError(
                code="INVALID_ORDER_DATETIME",
                message=(
                    "En beställning innehåller en ogiltig tid."
                ),
            )

        return None

    if isinstance(value, datetime):
        parsed_value = value

    else:
        normalized_value = str(value).strip()

        if not normalized_value:
            if required:
                raise RestaurantOrderStatusError(
                    code="INVALID_ORDER_DATETIME",
                    message=(
                        "En beställning innehåller en ogiltig tid."
                    ),
                )

            return None

        try:
            parsed_value = datetime.fromisoformat(
                normalized_value.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError as error:
            logger.error(
                "Invalid order datetime returned by Supabase",
                extra={
                    "field_name": field_name,
                },
            )

            raise RestaurantOrderStatusError(
                code="INVALID_ORDER_DATETIME",
                message=(
                    "En beställning innehåller en ogiltig tid."
                ),
            ) from error

    return coerce_to_tz(
        parsed_value,
        settings.restaurant_timezone,
    )


def _parse_uuid(
    value: object,
    *,
    error_code: str,
) -> UUID:
    try:
        return UUID(str(value))

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as error:
        raise RestaurantOrderStatusError(
            code=error_code,
            message=(
                "En beställning innehåller ett ogiltigt ID."
            ),
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
        raise RestaurantOrderStatusError(
            code=code,
            message=message,
        )

    return normalized_value


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
        raise RestaurantOrderStatusError(
            code=code,
            message=message,
        ) from error

    if parsed_value < 0:
        raise RestaurantOrderStatusError(
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
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_ITEM_QUANTITY",
            message=(
                "En beställning innehåller ett ogiltigt antal."
            ),
        ) from error

    if parsed_value < 1 or parsed_value > 100:
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_ITEM_QUANTITY",
            message=(
                "En beställning innehåller ett ogiltigt antal."
            ),
        )

    return parsed_value


def _coerce_order_items(
    value: object,
) -> list[dict[str, Any]]:
    parsed_value: object

    if isinstance(value, list):
        parsed_value = value

    elif isinstance(value, str):
        normalized_value = value.strip()

        if not normalized_value:
            parsed_value = []

        else:
            try:
                parsed_value = json.loads(
                    normalized_value
                )

            except json.JSONDecodeError as error:
                raise RestaurantOrderStatusError(
                    code="INVALID_ORDER_ITEMS",
                    message=(
                        "En beställning innehåller ogiltiga "
                        "produkter."
                    ),
                ) from error

    else:
        parsed_value = value

    if not isinstance(parsed_value, list):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_ITEMS",
            message=(
                "En beställning innehåller ogiltiga produkter."
            ),
        )

    rows: list[dict[str, Any]] = []

    for item in parsed_value:
        if not isinstance(item, dict):
            raise RestaurantOrderStatusError(
                code="INVALID_ORDER_ITEMS",
                message=(
                    "En beställning innehåller en ogiltig "
                    "produkt."
                ),
            )

        rows.append(item)

    if not rows:
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_ITEMS",
            message=(
                "En beställning innehåller inga produkter."
            ),
        )

    return rows


def _normalize_order_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    menu_item_id = _parse_uuid(
        item.get("menu_item_id"),
        error_code="INVALID_ORDER_MENU_ITEM_ID",
    )

    requested_name = _parse_non_empty_text(
        item.get("requested_name")
        or item.get("name"),
        code="INVALID_ORDER_ITEM_NAME",
        message=(
            "En beställning innehåller ett ogiltigt "
            "produktnamn."
        ),
    )

    official_name = _parse_non_empty_text(
        item.get("name"),
        code="INVALID_ORDER_ITEM_NAME",
        message=(
            "En beställning innehåller ett ogiltigt "
            "produktnamn."
        ),
    )

    quantity = _parse_positive_integer(
        item.get("quantity")
    )

    unit_price = _parse_non_negative_float(
        item.get("unit_price"),
        code="INVALID_ORDER_ITEM_PRICE",
        message=(
            "En beställning innehåller ett ogiltigt pris."
        ),
    )

    line_total = _parse_non_negative_float(
        item.get("line_total"),
        code="INVALID_ORDER_ITEM_TOTAL",
        message=(
            "En beställning innehåller en ogiltig radsumma."
        ),
    )

    currency = _parse_non_empty_text(
        item.get("currency"),
        code="INVALID_ORDER_CURRENCY",
        message=(
            "En beställning innehåller en ogiltig valuta."
        ),
    ).upper()

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_CURRENCY",
            message=(
                "En beställning innehåller en ogiltig valuta."
            ),
        )

    notes_value = str(
        item.get("notes")
        or ""
    ).strip()

    return {
        "menu_item_id": menu_item_id,
        "requested_name": requested_name,
        "official_name": official_name,
        "quantity": quantity,
        "notes": notes_value or None,
        "unit_price": unit_price,
        "line_total": line_total,
        "currency": currency,
    }


def _row_relevant_time(
    row: dict[str, Any],
) -> datetime:
    order_type = str(
        row.get("order_type")
        or ""
    ).strip()

    if order_type == "takeaway":
        relevant_time = _parse_datetime(
            row.get("pickup_time"),
            field_name="pickup_time",
            required=False,
        )

    elif order_type == "dine_in":
        relevant_time = _parse_datetime(
            row.get("dine_in_time"),
            field_name="dine_in_time",
            required=False,
        )

    else:
        relevant_time = None

    if relevant_time is not None:
        return relevant_time

    created_at = _parse_datetime(
        row.get("created_at"),
        field_name="created_at",
        required=True,
    )

    if created_at is None:
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_DATETIME",
            message=(
                "En beställning innehåller en ogiltig tid."
            ),
        )

    return created_at


def _normalize_order_row(
    *,
    row: dict[str, Any],
    expected_restaurant_id: UUID,
    normalized_phone: str,
) -> dict[str, Any]:
    returned_restaurant_id = _parse_uuid(
        row.get("restaurant_id"),
        error_code="INVALID_ORDER_RESTAURANT_ID",
    )

    if returned_restaurant_id != expected_restaurant_id:
        logger.error(
            "Restaurant isolation failure in v2 status query",
            extra={
                "expected_restaurant_id": str(
                    expected_restaurant_id
                ),
                "returned_restaurant_id": str(
                    returned_restaurant_id
                ),
            },
        )

        raise RestaurantOrderStatusError(
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
            "En beställning innehåller ett ogiltigt "
            "telefonnummer."
        ),
    )

    if not phone_suffix_match(
        returned_phone,
        normalized_phone,
    ):
        logger.error(
            "Caller isolation failure in v2 status query",
            extra={
                "restaurant_id": str(
                    expected_restaurant_id
                ),
            },
        )

        raise RestaurantOrderStatusError(
            code="ORDER_CALLER_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "kundens telefonnummer."
            ),
        )

    order_id = _parse_non_empty_text(
        row.get("order_id"),
        code="INVALID_ORDER_ID",
        message=(
            "En beställning innehåller ett ogiltigt order-ID."
        ),
    )

    order_status = _parse_non_empty_text(
        row.get("order_status"),
        code="INVALID_ORDER_STATUS",
        message=(
            "En beställning innehåller en ogiltig status."
        ),
    )

    order_type = _parse_non_empty_text(
        row.get("order_type"),
        code="INVALID_ORDER_TYPE",
        message=(
            "En beställning innehåller en ogiltig "
            "beställningstyp."
        ),
    )

    if order_type not in {
        "takeaway",
        "dine_in",
    }:
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_TYPE",
            message=(
                "En beställning innehåller en ogiltig "
                "beställningstyp."
            ),
        )

    customer_name = _parse_non_empty_text(
        row.get("customer_name"),
        code="INVALID_ORDER_CUSTOMER_NAME",
        message=(
            "En beställning innehåller ett ogiltigt kundnamn."
        ),
    )

    created_at = _parse_datetime(
        row.get("created_at"),
        field_name="created_at",
        required=True,
    )

    if created_at is None:
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_DATETIME",
            message=(
                "En beställning innehåller en ogiltig tid."
            ),
        )

    dine_in_time = _parse_datetime(
        row.get("dine_in_time"),
        field_name="dine_in_time",
        required=False,
    )

    pickup_time = _parse_datetime(
        row.get("pickup_time"),
        field_name="pickup_time",
        required=False,
    )

    if (
        order_type == "takeaway"
        and pickup_time is None
    ):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_PICKUP_TIME",
            message=(
                "En avhämtningsorder saknar giltig "
                "hämtningstid."
            ),
        )

    if (
        order_type == "dine_in"
        and dine_in_time is None
    ):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_DINE_IN_TIME",
            message=(
                "En beställning för att äta på plats saknar "
                "giltig ankomsttid."
            ),
        )

    currency = _parse_non_empty_text(
        row.get("currency"),
        code="INVALID_ORDER_CURRENCY",
        message=(
            "En beställning innehåller en ogiltig valuta."
        ),
    ).upper()

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_CURRENCY",
            message=(
                "En beställning innehåller en ogiltig valuta."
            ),
        )

    total = _parse_non_negative_float(
        row.get("total"),
        code="INVALID_ORDER_TOTAL",
        message=(
            "En beställning innehåller en ogiltig total."
        ),
    )

    raw_items = _coerce_order_items(
        row.get("order_items")
    )

    normalized_items = [
        _normalize_order_item(item)
        for item in raw_items
    ]

    notes_value = str(
        row.get("notes")
        or ""
    ).strip()

    cancellation_reason_value = str(
        row.get("cancellation_reason")
        or ""
    ).strip()

    return {
        "order_id": order_id,
        "order_status": order_status,
        "order_type": order_type,
        "customer_name": customer_name,
        "customer_phone": returned_phone,
        "created_at": created_at,
        "dine_in_time": dine_in_time,
        "pickup_time": pickup_time,
        "currency": currency,
        "total": total,
        "items": normalized_items,
        "notes": notes_value or None,
        "cancellation_reason": (
            cancellation_reason_value
            or None
        ),
    }


def _load_restaurant_orders(
    *,
    restaurant_id: UUID,
    normalized_phone: str,
) -> list[dict[str, Any]]:
    phone_digits = digits_only(
        normalized_phone
    )

    suffix = (
        phone_digits[-PHONE_SUFFIX_LENGTH:]
        if len(phone_digits) >= PHONE_SUFFIX_LENGTH
        else phone_digits
    )

    if not suffix:
        return []

    try:
        response = (
            get_client()
            .table("orders")
            .select(
                (
                    "order_id,"
                    "restaurant_id,"
                    "customer_name,"
                    "customer_phone,"
                    "order_status,"
                    "created_at,"
                    "order_type,"
                    "order_items,"
                    "dine_in_time,"
                    "pickup_time,"
                    "total,"
                    "notes,"
                    "cancellation_reason,"
                    "currency"
                )
            )
            .eq(
                "restaurant_id",
                str(restaurant_id),
            )
            .like(
                "customer_phone",
                f"%{suffix}",
            )
            .limit(MAX_CANDIDATE_ROWS)
            .execute()
        )

    except Exception as error:
        logger.error(
            "Could not read restaurant-scoped order status",
            extra={
                "restaurant_id": str(
                    restaurant_id
                ),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise RestaurantOrderStatusError(
            code="ORDER_STATUS_READ_FAILED",
            message=(
                "Beställningsstatusen kunde inte läsas."
            ),
            status_code=502,
        ) from error

    data = response.data

    if data is None:
        return []

    if not isinstance(data, list):
        raise RestaurantOrderStatusError(
            code="INVALID_ORDER_STATUS_RESPONSE",
            message=(
                "Beställningsstatusen gav ett ogiltigt svar."
            ),
            status_code=502,
        )

    rows: list[dict[str, Any]] = []

    for value in data:
        if not isinstance(value, dict):
            raise RestaurantOrderStatusError(
                code="INVALID_ORDER_STATUS_RESPONSE",
                message=(
                    "Beställningsstatusen innehåller ogiltiga "
                    "orderuppgifter."
                ),
                status_code=502,
            )

        rows.append(value)

    return rows


def check_restaurant_order_status(
    *,
    context: ToolRestaurantContext,
    request: CheckOrderStatusV2Request,
) -> dict[str, Any]:
    """
    Read recent orders for one authenticated restaurant and one
    normalized caller phone.

    No database rows are created or changed.
    """

    normalized_phone = _normalize_customer_phone(
        request.customer_phone
    )

    rows = _load_restaurant_orders(
        restaurant_id=context.restaurant_id,
        normalized_phone=normalized_phone,
    )

    now = tz_now(
        settings.restaurant_timezone
    )

    window_start, window_end = make_window(
        now,
        settings.lookahead_hours,
    )

    candidates: list[
        tuple[datetime, dict[str, Any]]
    ] = []

    for row in rows:
        returned_phone = str(
            row.get("customer_phone")
            or ""
        ).strip()

        if not phone_suffix_match(
            returned_phone,
            normalized_phone,
        ):
            continue

        relevant_time = _row_relevant_time(
            row
        )

        if (
            relevant_time < window_start
            or relevant_time > window_end
        ):
            continue

        candidates.append(
            (
                relevant_time,
                row,
            )
        )

    candidates.sort(
        key=lambda candidate: candidate[0],
        reverse=True,
    )

    normalized_orders = [
        _normalize_order_row(
            row=row,
            expected_restaurant_id=(
                context.restaurant_id
            ),
            normalized_phone=normalized_phone,
        )
        for _, row in candidates
    ]

    return {
        "success": True,
        "restaurant_id": context.restaurant_id,
        "restaurant_name": context.restaurant_name,
        "customer_phone": normalized_phone,
        "timezone": settings.restaurant_timezone,
        "orders": normalized_orders,
        "order_count": len(normalized_orders),
    }
