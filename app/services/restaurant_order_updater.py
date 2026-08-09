from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    CalculateOrderTotalV2Request,
    UpdateOrderV2Request,
)
from app.services.restaurant_menu_pricing import (
    RestaurantMenuPricingError,
    calculate_restaurant_menu_total,
)
from app.services.supabase_client import get_client
from app.utils.phone import (
    normalize_phone,
    phone_suffix_match,
)


logger = logging.getLogger(__name__)


class RestaurantOrderUpdateError(Exception):
    """Safe error returned by the v2 order-update service."""

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
    "UPDATES_OBJECT_REQUIRED": (
        422,
        "Ändringsuppgifterna har ett ogiltigt format.",
    ),
    "AT_LEAST_ONE_UPDATE_REQUIRED": (
        422,
        "Minst en ändring måste anges.",
    ),
    "UNSUPPORTED_UPDATE_FIELD": (
        422,
        "Ändringen innehåller ett otillåtet fält.",
    ),
    "CUSTOMER_NAME_REQUIRED": (
        422,
        "Kundens namn får inte vara tomt.",
    ),
    "CUSTOMER_NAME_TOO_LONG": (
        422,
        "Kundens namn är för långt.",
    ),
    "INVALID_ORDER_TYPE": (
        422,
        "Beställningstypen är ogiltig.",
    ),
    "INVALID_PARTY_SIZE": (
        422,
        "Antalet gäster är ogiltigt.",
    ),
    "INVALID_DINE_IN_TIME": (
        422,
        "Ankomsttiden har ett ogiltigt format.",
    ),
    "INVALID_PICKUP_TIME": (
        422,
        "Hämtningstiden har ett ogiltigt format.",
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
    "MENU_ITEM_NAME_MISSING": (
        502,
        "En produkt i menyn saknar ett giltigt namn.",
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
    "PICKUP_TIME_REQUIRED": (
        422,
        "Hämtningstid krävs för avhämtning.",
    ),
    "DINE_IN_TIME_NOT_ALLOWED": (
        422,
        "Ankomsttid får inte finnas för avhämtning.",
    ),
    "DINE_IN_TIME_REQUIRED": (
        422,
        "Ankomsttid krävs för att äta på plats.",
    ),
    "PICKUP_TIME_NOT_ALLOWED": (
        422,
        "Hämtningstid får inte finnas för att äta på plats.",
    ),
}


RESULT_CODE_MAP: dict[str, tuple[int, str]] = {
    "ORDER_NOT_FOUND": (
        404,
        "Beställningen kunde inte hittas för restaurangen.",
    ),
    "ORDER_NOT_V2": (
        409,
        "Beställningen kan inte ändras genom v2-flödet.",
    ),
    "ORDER_CALLER_MISMATCH": (
        403,
        "Beställningen tillhör inte kundens telefonnummer.",
    ),
    "ORDER_NOT_UPDATABLE": (
        409,
        "Beställningen kan inte längre ändras.",
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
        raise RestaurantOrderUpdateError(
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
        raise RestaurantOrderUpdateError(
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
        raise RestaurantOrderUpdateError(
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
        raise RestaurantOrderUpdateError(
            code=code,
            message=message,
        ) from error

    if parsed_value < 0:
        raise RestaurantOrderUpdateError(
            code=code,
            message=message,
        )

    return parsed_value


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
        raise RestaurantOrderUpdateError(
            code=code,
            message=message,
        ) from error

    if parsed_value < 0:
        raise RestaurantOrderUpdateError(
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
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_ITEM_QUANTITY",
            message=(
                "Beställningen innehåller ett ogiltigt antal."
            ),
        ) from error

    if parsed_value < 1 or parsed_value > 100:
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_ITEM_QUANTITY",
            message=(
                "Beställningen innehåller ett ogiltigt antal."
            ),
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
            raise RestaurantOrderUpdateError(
                code="INVALID_ORDER_DATETIME_RESPONSE",
                message=(
                    f"Beställningen saknar tiden {field_name}."
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
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_DATETIME_RESPONSE",
            message=(
                "Beställningen returnerade en ogiltig tid."
            ),
        ) from error


def _load_current_order(
    *,
    context: ToolRestaurantContext,
    order_id: str,
    normalized_phone: str,
) -> dict[str, Any]:
    """
    Load the current v2 order and its revision before updating.

    The RPC performs the authoritative lock and repeats all
    restaurant, caller, status, and revision checks.
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
            "Could not read restaurant order before update",
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

        raise RestaurantOrderUpdateError(
            code="ORDER_READ_FAILED",
            message=(
                "Beställningen kunde inte läsas före ändringen."
            ),
            status_code=502,
        ) from error

    row = _extract_first_row(response.data)

    if row is None:
        raise RestaurantOrderUpdateError(
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
            "Restaurant isolation failure before order update",
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

        raise RestaurantOrderUpdateError(
            code="ORDER_RESTAURANT_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "restaurangen."
            ),
            status_code=502,
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
        raise RestaurantOrderUpdateError(
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

    if order_status != "new order":
        raise RestaurantOrderUpdateError(
            code="ORDER_NOT_UPDATABLE",
            message=(
                "Beställningen kan inte längre ändras."
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
        raise RestaurantOrderUpdateError(
            code="ORDER_NOT_V2",
            message=(
                "Beställningen kan inte ändras genom "
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


def _build_pricing_request(
    request: UpdateOrderV2Request,
) -> CalculateOrderTotalV2Request:
    if request.order_items is None:
        raise RestaurantOrderUpdateError(
            code="ORDER_ITEMS_REQUIRED",
            message=(
                "Beställningen måste innehålla minst en produkt."
            ),
            status_code=422,
        )

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
    request: UpdateOrderV2Request,
    pricing_result: dict[str, Any],
) -> list[dict[str, Any]]:
    if request.order_items is None:
        raise RestaurantOrderUpdateError(
            code="ORDER_ITEMS_REQUIRED",
            message=(
                "Beställningen måste innehålla minst en produkt."
            ),
            status_code=422,
        )

    verified_items = pricing_result.get("items")

    if not isinstance(verified_items, list):
        raise RestaurantOrderUpdateError(
            code="INVALID_PRICING_RESPONSE",
            message=(
                "Prisverifieringen gav ett ogiltigt svar."
            ),
        )

    if len(verified_items) != len(request.order_items):
        raise RestaurantOrderUpdateError(
            code="PRICING_ITEM_COUNT_MISMATCH",
            message=(
                "Prisverifieringen matchar inte "
                "beställningens produkter."
            ),
        )

    rpc_items: list[dict[str, Any]] = []

    for requested_item, verified_item in zip(
        request.order_items,
        verified_items,
        strict=True,
    ):
        if not isinstance(verified_item, dict):
            raise RestaurantOrderUpdateError(
                code="INVALID_PRICING_RESPONSE",
                message=(
                    "Prisverifieringen innehåller en "
                    "ogiltig produkt."
                ),
            )

        menu_item_id = _parse_uuid(
            verified_item.get("menu_item_id"),
            code="INVALID_VERIFIED_MENU_ITEM_ID",
            message=(
                "Prisverifieringen innehåller ett ogiltigt "
                "meny-ID."
            ),
        )

        verified_quantity = _parse_positive_integer(
            verified_item.get("quantity")
        )

        if verified_quantity != requested_item.quantity:
            raise RestaurantOrderUpdateError(
                code="PRICING_QUANTITY_MISMATCH",
                message=(
                    "Prisverifieringen matchar inte "
                    "produktens antal."
                ),
            )

        rpc_items.append(
            {
                "menu_item_id": str(menu_item_id),
                "requested_name": requested_item.name,
                "quantity": requested_item.quantity,
                "notes": requested_item.notes,
            }
        )

    return rpc_items


def _build_updates(
    *,
    context: ToolRestaurantContext,
    request: UpdateOrderV2Request,
) -> dict[str, Any]:
    """
    Build the exact update object from Pydantic's field-set
    tracking. Omitted fields are never changed.
    """

    fields_set = request.model_fields_set
    updates: dict[str, Any] = {}

    if "customer_name" in fields_set:
        updates["customer_name"] = (
            request.customer_name
        )

    if "order_type" in fields_set:
        updates["order_type"] = request.order_type

        # Switching order type must also clear the old,
        # incompatible time field.
        if request.order_type == "takeaway":
            updates["pickup_time"] = (
                request.pickup_time.isoformat()
                if request.pickup_time
                else None
            )
            updates["dine_in_time"] = None

        elif request.order_type == "dine_in":
            updates["dine_in_time"] = (
                request.dine_in_time.isoformat()
                if request.dine_in_time
                else None
            )
            updates["pickup_time"] = None

    else:
        if "dine_in_time" in fields_set:
            updates["dine_in_time"] = (
                request.dine_in_time.isoformat()
                if request.dine_in_time
                else None
            )

        if "pickup_time" in fields_set:
            updates["pickup_time"] = (
                request.pickup_time.isoformat()
                if request.pickup_time
                else None
            )

    if "party_size" in fields_set:
        updates["party_size"] = request.party_size

    if "notes" in fields_set:
        updates["notes"] = request.notes

    if "order_items" in fields_set:
        pricing_request = _build_pricing_request(
            request
        )

        try:
            pricing_result = calculate_restaurant_menu_total(
                context=context,
                request=pricing_request,
            )

        except RestaurantMenuPricingError as error:
            raise RestaurantOrderUpdateError(
                code=error.code,
                message=error.message,
                status_code=error.status_code,
            ) from error

        updates["order_items"] = (
            _build_verified_rpc_items(
                request=request,
                pricing_result=pricing_result,
            )
        )

    return updates


def _map_rpc_error(
    error: Exception,
) -> RestaurantOrderUpdateError:
    raw_message = (
        getattr(error, "message", None)
        or str(error)
    )

    for error_code, (
        status_code,
        safe_message,
    ) in SAFE_RPC_ERROR_MAP.items():
        if error_code in raw_message:
            return RestaurantOrderUpdateError(
                code=error_code,
                message=safe_message,
                status_code=status_code,
            )

    return RestaurantOrderUpdateError(
        code="RESTAURANT_ORDER_UPDATE_FAILED",
        message="Beställningen kunde inte ändras.",
        status_code=502,
    )


def _normalize_result_items(
    value: object,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_ORDER_ITEMS",
            message=(
                "Den uppdaterade beställningen returnerade "
                "inga giltiga produkter."
            ),
        )

    normalized_items: list[dict[str, Any]] = []

    for item in value:
        if not isinstance(item, dict):
            raise RestaurantOrderUpdateError(
                code="INVALID_UPDATED_ORDER_ITEMS",
                message=(
                    "Den uppdaterade beställningen innehåller "
                    "en ogiltig produkt."
                ),
            )

        menu_item_id = _parse_uuid(
            item.get("menu_item_id"),
            code="INVALID_UPDATED_MENU_ITEM_ID",
            message=(
                "Den uppdaterade beställningen innehåller "
                "ett ogiltigt meny-ID."
            ),
        )

        requested_name = _parse_non_empty_text(
            item.get("requested_name")
            or item.get("name"),
            code="INVALID_UPDATED_ITEM_NAME",
            message=(
                "Den uppdaterade beställningen innehåller "
                "ett ogiltigt produktnamn."
            ),
        )

        official_name = _parse_non_empty_text(
            item.get("name"),
            code="INVALID_UPDATED_ITEM_NAME",
            message=(
                "Den uppdaterade beställningen innehåller "
                "ett ogiltigt produktnamn."
            ),
        )

        quantity = _parse_positive_integer(
            item.get("quantity")
        )

        unit_price = _parse_non_negative_float(
            item.get("unit_price"),
            code="INVALID_UPDATED_ITEM_PRICE",
            message=(
                "Den uppdaterade beställningen innehåller "
                "ett ogiltigt pris."
            ),
        )

        line_total = _parse_non_negative_float(
            item.get("line_total"),
            code="INVALID_UPDATED_ITEM_TOTAL",
            message=(
                "Den uppdaterade beställningen innehåller "
                "en ogiltig radsumma."
            ),
        )

        currency = _parse_non_empty_text(
            item.get("currency"),
            code="INVALID_UPDATED_ITEM_CURRENCY",
            message=(
                "Den uppdaterade beställningen innehåller "
                "en ogiltig valuta."
            ),
        ).upper()

        if (
            len(currency) != 3
            or not currency.isalpha()
        ):
            raise RestaurantOrderUpdateError(
                code="INVALID_UPDATED_ITEM_CURRENCY",
                message=(
                    "Den uppdaterade beställningen innehåller "
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


def _normalize_updated_fields(
    value: object,
) -> list[str]:
    if not isinstance(value, list):
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_FIELDS_RESPONSE",
            message=(
                "Orderändringen returnerade ett ogiltigt "
                "fältresultat."
            ),
        )

    allowed_fields = {
        "customer_name",
        "order_type",
        "order_items",
        "party_size",
        "dine_in_time",
        "pickup_time",
        "notes",
        "total",
    }

    normalized_fields: list[str] = []

    for field_name in value:
        normalized_name = str(
            field_name or ""
        ).strip()

        if (
            not normalized_name
            or normalized_name not in allowed_fields
        ):
            raise RestaurantOrderUpdateError(
                code="INVALID_UPDATED_FIELDS_RESPONSE",
                message=(
                    "Orderändringen returnerade ett ogiltigt "
                    "fältresultat."
                ),
            )

        if normalized_name not in normalized_fields:
            normalized_fields.append(
                normalized_name
            )

    return normalized_fields


def update_restaurant_order(
    *,
    context: ToolRestaurantContext,
    request: UpdateOrderV2Request,
) -> dict[str, Any]:
    """
    Safely update one v2 order for the restaurant resolved from
    X-Svir-Tool-Token.

    The service reads the current revision, verifies optional
    replacement products against the restaurant menu, and lets
    the Supabase RPC perform the authoritative atomic update.
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

    updates = _build_updates(
        context=context,
        request=request,
    )

    if not updates:
        raise RestaurantOrderUpdateError(
            code="AT_LEAST_ONE_UPDATE_REQUIRED",
            message="Minst en ändring måste anges.",
            status_code=422,
        )

    try:
        response = get_client().rpc(
            "update_restaurant_order_v2",
            {
                "p_restaurant_id": str(
                    context.restaurant_id
                ),
                "p_order_id": order_id,
                "p_customer_phone": normalized_phone,
                "p_expected_revision": expected_revision,
                "p_updates": updates,
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Restaurant-scoped order update failed",
            extra={
                "restaurant_id": str(
                    context.restaurant_id
                ),
                "order_id": order_id,
                "expected_revision": expected_revision,
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
        raise RestaurantOrderUpdateError(
            code="EMPTY_ORDER_UPDATE_RESPONSE",
            message=(
                "Orderändringen gav inget giltigt svar."
            ),
        )

    required_fields = {
        "applied",
        "result_code",
        "result_restaurant_id",
        "result_order_id",
        "result_order_status",
        "result_order_revision",
        "result_updated_at",
        "result_order_type",
        "result_customer_name",
        "result_customer_phone",
        "result_created_at",
        "result_dine_in_time",
        "result_pickup_time",
        "result_currency",
        "result_total",
        "result_items",
        "result_notes",
        "result_updated_fields",
    }

    if not required_fields.issubset(result):
        raise RestaurantOrderUpdateError(
            code="INCOMPLETE_ORDER_UPDATE_RESPONSE",
            message=(
                "Orderändringen gav ett ofullständigt svar."
            ),
        )

    returned_restaurant_id = _parse_uuid(
        result.get("result_restaurant_id"),
        code="INVALID_UPDATED_RESTAURANT_ID",
        message=(
            "Orderändringen returnerade ett ogiltigt "
            "restaurang-ID."
        ),
    )

    if returned_restaurant_id != context.restaurant_id:
        logger.error(
            "Restaurant isolation failure in update response",
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

        raise RestaurantOrderUpdateError(
            code="ORDER_RESTAURANT_MISMATCH",
            message=(
                "Beställningen kunde inte verifieras mot "
                "restaurangen."
            ),
        )

    returned_order_id = _parse_non_empty_text(
        result.get("result_order_id"),
        code="INVALID_UPDATED_ORDER_ID",
        message=(
            "Orderändringen returnerade ett ogiltigt order-ID."
        ),
    )

    if returned_order_id != order_id:
        raise RestaurantOrderUpdateError(
            code="ORDER_ID_MISMATCH",
            message=(
                "Orderändringen returnerade fel beställning."
            ),
        )

    result_code = str(
        result.get("result_code")
        or ""
    ).strip()

    applied = bool(
        result.get("applied")
    )

    if result_code in RESULT_CODE_MAP:
        status_code, safe_message = (
            RESULT_CODE_MAP[result_code]
        )

        raise RestaurantOrderUpdateError(
            code=result_code,
            message=safe_message,
            status_code=status_code,
        )

    if result_code == "NO_CHANGES":
        if applied:
            raise RestaurantOrderUpdateError(
                code="INVALID_ORDER_UPDATE_RESPONSE",
                message=(
                    "Orderändringen gav ett motsägelsefullt "
                    "svar."
                ),
            )

        updated = False

    elif result_code == "ORDER_UPDATED":
        if not applied:
            raise RestaurantOrderUpdateError(
                code="INVALID_ORDER_UPDATE_RESPONSE",
                message=(
                    "Orderändringen gav ett motsägelsefullt "
                    "svar."
                ),
            )

        updated = True

    else:
        raise RestaurantOrderUpdateError(
            code=(
                result_code
                or "ORDER_UPDATE_NOT_APPLIED"
            ),
            message="Beställningen kunde inte ändras.",
            status_code=409,
        )

    result_revision = _parse_non_negative_integer(
        result.get("result_order_revision"),
        code="INVALID_UPDATED_ORDER_REVISION",
        message=(
            "Orderändringen returnerade ett ogiltigt "
            "revisionsnummer."
        ),
    )

    expected_result_revision = (
        expected_revision + 1
        if updated
        else expected_revision
    )

    if result_revision != expected_result_revision:
        raise RestaurantOrderUpdateError(
            code="UPDATED_ORDER_REVISION_MISMATCH",
            message=(
                "Orderändringens revisionsnummer kunde inte "
                "verifieras."
            ),
        )

    order_status = _parse_non_empty_text(
        result.get("result_order_status"),
        code="INVALID_UPDATED_ORDER_STATUS",
        message=(
            "Orderändringen returnerade en ogiltig status."
        ),
    ).lower()

    if order_status != "new order":
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_ORDER_STATUS",
            message=(
                "Orderändringen returnerade en ogiltig status."
            ),
        )

    order_type = _parse_non_empty_text(
        result.get("result_order_type"),
        code="INVALID_UPDATED_ORDER_TYPE",
        message=(
            "Orderändringen returnerade en ogiltig "
            "beställningstyp."
        ),
    )

    if order_type not in {
        "takeaway",
        "dine_in",
    }:
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_ORDER_TYPE",
            message=(
                "Orderändringen returnerade en ogiltig "
                "beställningstyp."
            ),
        )

    customer_name = _parse_non_empty_text(
        result.get("result_customer_name"),
        code="INVALID_UPDATED_CUSTOMER_NAME",
        message=(
            "Orderändringen returnerade ett ogiltigt kundnamn."
        ),
    )

    returned_phone = _parse_non_empty_text(
        result.get("result_customer_phone"),
        code="INVALID_UPDATED_CUSTOMER_PHONE",
        message=(
            "Orderändringen returnerade ett ogiltigt "
            "telefonnummer."
        ),
    )

    if not phone_suffix_match(
        returned_phone,
        normalized_phone,
    ):
        raise RestaurantOrderUpdateError(
            code="ORDER_CALLER_MISMATCH",
            message=(
                "Beställningen tillhör inte kundens "
                "telefonnummer."
            ),
            status_code=403,
        )

    created_at = _parse_datetime(
        result.get("result_created_at"),
        required=True,
        field_name="created_at",
    )

    if created_at is None:
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_DATETIME_RESPONSE",
            message=(
                "Orderändringen returnerade en ogiltig tid."
            ),
        )

    updated_at = _parse_datetime(
        result.get("result_updated_at"),
        required=True,
        field_name="updated_at",
    )

    if updated_at is None:
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_DATETIME_RESPONSE",
            message=(
                "Orderändringen returnerade en ogiltig tid."
            ),
        )

    dine_in_time = _parse_datetime(
        result.get("result_dine_in_time"),
        required=False,
        field_name="dine_in_time",
    )

    pickup_time = _parse_datetime(
        result.get("result_pickup_time"),
        required=False,
        field_name="pickup_time",
    )

    if (
        order_type == "dine_in"
        and dine_in_time is None
    ):
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_DINE_IN_TIME",
            message=(
                "Den uppdaterade beställningen saknar "
                "ankomsttid."
            ),
        )

    currency = _parse_non_empty_text(
        result.get("result_currency"),
        code="INVALID_UPDATED_CURRENCY",
        message=(
            "Orderändringen returnerade en ogiltig valuta."
        ),
    ).upper()

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise RestaurantOrderUpdateError(
            code="INVALID_UPDATED_CURRENCY",
            message=(
                "Orderändringen returnerade en ogiltig valuta."
            ),
        )

    total = _parse_non_negative_float(
        result.get("result_total"),
        code="INVALID_UPDATED_TOTAL",
        message=(
            "Orderändringen returnerade en ogiltig total."
        ),
    )

    updated_fields = _normalize_updated_fields(
        result.get("result_updated_fields")
    )

    if updated and not updated_fields:
        raise RestaurantOrderUpdateError(
            code="UPDATED_FIELDS_MISSING",
            message=(
                "Orderändringen returnerade inga ändrade fält."
            ),
        )

    if not updated and updated_fields:
        raise RestaurantOrderUpdateError(
            code="INVALID_ORDER_UPDATE_RESPONSE",
            message=(
                "Orderändringen gav ett motsägelsefullt svar."
            ),
        )

    notes_value = str(
        result.get("result_notes")
        or ""
    ).strip()

    # The current response schema does not expose revision and
    # updated_at yet. They are still validated internally here
    # so concurrent updates remain protected.
    return {
        "success": True,
        "updated": updated,
        "restaurant_id": context.restaurant_id,
        "restaurant_name": context.restaurant_name,
        "order_id": order_id,
        "order_status": order_status,
        "order_type": order_type,
        "customer_name": customer_name,
        "customer_phone": returned_phone,
        "updated_fields": updated_fields,
        "created_at": created_at,
        "dine_in_time": dine_in_time,
        "pickup_time": pickup_time,
        "currency": currency,
        "total": total,
        "items": _normalize_result_items(
            result.get("result_items")
        ),
        "notes": notes_value or None,
    }