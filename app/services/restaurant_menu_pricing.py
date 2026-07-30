from __future__ import annotations

import logging
import unicodedata
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from typing import Any
from uuid import UUID

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    CalculateOrderTotalV2Request,
)
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

MONEY_QUANTUM = Decimal("0.01")

MENU_ITEM_NAME_FIELDS = (
    "official_name",
    "customer_display_name",
    "kitchen_display_name",
)

# YZ restaurant ID for chilimajonnäs alias support
YZ_RESTAURANT_ID = UUID("fc032c24-1dd6-4f94-9a4e-872a50c2487a")


class RestaurantMenuPricingError(Exception):
    """
    Safe error returned by the restaurant menu pricing service.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _safe_log_value(
    value: object,
    max_length: int = 300,
) -> str | None:
    if value is None:
        return None

    return str(value)[:max_length]


def _normalize_menu_name(
    value: object,
) -> str:
    """
    Normalize a menu name for exact, case-insensitive matching.

    Unicode characters are preserved. Repeated whitespace is
    converted to one space.
    """

    if value is None:
        return ""

    normalized = unicodedata.normalize(
        "NFKC",
        str(value),
    )

    return " ".join(
        normalized.strip().casefold().split()
    )


# YZ chilimajonnäs aliases that map to canonical "Extra chilimajonnäs"
YZ_CHILIMAJONNÄS_ALIASES = {
    _normalize_menu_name("Chilimajonnäs"): "extra chilimajonnäs",
    _normalize_menu_name("Chili majonnäs"): "extra chilimajonnäs",
    _normalize_menu_name("Chilimayo"): "extra chilimajonnäs",
    _normalize_menu_name("Chili mayo"): "extra chilimajonnäs",
    _normalize_menu_name("Extra chili mayo"): "extra chilimajonnäs",
}


def _parse_menu_item_id(
    value: object,
) -> UUID:
    try:
        return UUID(str(value))

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as error:
        raise RestaurantMenuPricingError(
            code="INVALID_MENU_ITEM_ID",
            message=(
                "Menyn innehåller en produkt med ett "
                "ogiltigt produkt-ID."
            ),
            status_code=502,
        ) from error


def _parse_menu_price(
    value: object,
    *,
    item_name: str,
) -> Decimal:
    """
    Parse and normalize a trusted price read from Supabase.
    """

    try:
        price = Decimal(str(value))

    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as error:
        raise RestaurantMenuPricingError(
            code="INVALID_MENU_ITEM_PRICE",
            message=(
                f"Produkten {item_name} har inget "
                "giltigt pris i menyn."
            ),
            status_code=502,
        ) from error

    if not price.is_finite() or price < 0:
        raise RestaurantMenuPricingError(
            code="INVALID_MENU_ITEM_PRICE",
            message=(
                f"Produkten {item_name} har inget "
                "giltigt pris i menyn."
            ),
            status_code=502,
        )

    return price.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _parse_currency(
    value: object,
    *,
    item_name: str,
) -> str:
    currency = str(value or "").strip().upper()

    if len(currency) != 3 or not currency.isalpha():
        raise RestaurantMenuPricingError(
            code="INVALID_MENU_ITEM_CURRENCY",
            message=(
                f"Produkten {item_name} har ingen "
                "giltig valuta i menyn."
            ),
            status_code=502,
        )

    return currency


def _load_active_menu_items(
    restaurant_id: UUID,
) -> list[dict[str, Any]]:
    """
    Read only active menu items belonging to one restaurant.

    restaurant_id comes from the authenticated tool token and
    never from the request body.
    """

    try:
        response = (
            get_client()
            .table("menu_items")
            .select(
                (
                    "id,"
                    "restaurant_id,"
                    "official_name,"
                    "customer_display_name,"
                    "kitchen_display_name,"
                    "base_price,"
                    "currency,"
                    "is_active"
                )
            )
            .eq(
                "restaurant_id",
                str(restaurant_id),
            )
            .eq(
                "is_active",
                True,
            )
            .execute()
        )

    except Exception as error:
        logger.error(
            "Could not read restaurant menu for pricing",
            extra={
                "restaurant_id": str(restaurant_id),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise RestaurantMenuPricingError(
            code="RESTAURANT_MENU_READ_FAILED",
            message=(
                "Restaurangens meny kunde inte läsas."
            ),
            status_code=502,
        ) from error

    data = response.data

    if data is None:
        return []

    if not isinstance(data, list):
        raise RestaurantMenuPricingError(
            code="INVALID_RESTAURANT_MENU_RESPONSE",
            message=(
                "Restaurangens meny gav ett ogiltigt svar."
            ),
            status_code=502,
        )

    rows: list[dict[str, Any]] = []

    for value in data:
        if not isinstance(value, dict):
            raise RestaurantMenuPricingError(
                code="INVALID_RESTAURANT_MENU_RESPONSE",
                message=(
                    "Restaurangens meny innehåller "
                    "ogiltiga produktuppgifter."
                ),
                status_code=502,
            )

        returned_restaurant_id = str(
            value.get("restaurant_id") or ""
        ).strip()

        if returned_restaurant_id != str(restaurant_id):
            logger.error(
                "Restaurant menu isolation failure",
                extra={
                    "expected_restaurant_id": str(
                        restaurant_id
                    ),
                    "returned_restaurant_id": (
                        returned_restaurant_id
                    ),
                },
            )

            raise RestaurantMenuPricingError(
                code="MENU_RESTAURANT_MISMATCH",
                message=(
                    "Menyn kunde inte verifieras mot "
                    "restaurangen."
                ),
                status_code=502,
            )

        rows.append(value)

    return rows


def _build_menu_name_index(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Build an exact-name lookup index.

    The same product is deduplicated when its official,
    customer, and kitchen display names are identical.
    """

    indexed_rows: dict[
        str,
        dict[str, dict[str, Any]],
    ] = {}

    for row in rows:
        item_id = str(
            _parse_menu_item_id(row.get("id"))
        )

        for field_name in MENU_ITEM_NAME_FIELDS:
            normalized_name = _normalize_menu_name(
                row.get(field_name)
            )

            if not normalized_name:
                continue

            matches_by_item_id = indexed_rows.setdefault(
                normalized_name,
                {},
            )

            matches_by_item_id[item_id] = row

    return {
        normalized_name: list(
            matches_by_item_id.values()
        )
        for (
            normalized_name,
            matches_by_item_id,
        ) in indexed_rows.items()
    }


def _resolve_requested_menu_item(
    *,
    requested_name: str,
    name_index: dict[
        str,
        list[dict[str, Any]],
    ],
    restaurant_id: UUID,
) -> dict[str, Any]:
    normalized_name = _normalize_menu_name(
        requested_name
    )

    if not normalized_name:
        raise RestaurantMenuPricingError(
            code="MENU_ITEM_NAME_REQUIRED",
            message=(
                "Produktnamnet får inte vara tomt."
            ),
            status_code=422,
        )

    matches = name_index.get(
        normalized_name,
        [],
    )

    # If no direct match and restaurant is YZ, try chilimajonnäs aliases
    if not matches and restaurant_id == YZ_RESTAURANT_ID:
        canonical_name = YZ_CHILIMAJONNÄS_ALIASES.get(normalized_name)
        if canonical_name:
            matches = name_index.get(
                canonical_name,
                [],
            )

    if not matches:
        raise RestaurantMenuPricingError(
            code="MENU_ITEM_NOT_FOUND",
            message=(
                f"Produkten {requested_name.strip()} "
                "finns inte i restaurangens aktiva meny."
            ),
            status_code=422,
        )

    if len(matches) > 1:
        raise RestaurantMenuPricingError(
            code="AMBIGUOUS_MENU_ITEM_NAME",
            message=(
                f"Flera produkter matchar namnet "
                f"{requested_name.strip()}. "
                "Menyn behöver kontrolleras."
            ),
            status_code=409,
        )

    return matches[0]


def calculate_restaurant_menu_total(
    *,
    context: ToolRestaurantContext,
    request: CalculateOrderTotalV2Request,
) -> dict[str, Any]:
    """
    Calculate an order total using only prices read from the
    authenticated restaurant's active Supabase menu.

    The request cannot provide restaurant_id, price, currency,
    or total.
    """

    menu_rows = _load_active_menu_items(
        context.restaurant_id
    )

    if not menu_rows:
        raise RestaurantMenuPricingError(
            code="RESTAURANT_MENU_EMPTY",
            message=(
                "Restaurangen har inga aktiva produkter "
                "i menyn."
            ),
            status_code=422,
        )

    name_index = _build_menu_name_index(
        menu_rows
    )

    if not name_index:
        raise RestaurantMenuPricingError(
            code="RESTAURANT_MENU_NAMES_MISSING",
            message=(
                "Restaurangens aktiva produkter saknar "
                "giltiga produktnamn."
            ),
            status_code=502,
        )

    verified_currency: str | None = None
    verified_total = Decimal("0.00")

    verified_lines: list[dict[str, Any]] = []

    for requested_item in request.order_items:
        requested_name = (
            requested_item.name.strip()
        )

        menu_item = _resolve_requested_menu_item(
            requested_name=requested_name,
            name_index=name_index,
            restaurant_id=context.restaurant_id,
        )

        menu_item_id = _parse_menu_item_id(
            menu_item.get("id")
        )

        official_name = str(
            menu_item.get("official_name")
            or menu_item.get(
                "customer_display_name"
            )
            or menu_item.get(
                "kitchen_display_name"
            )
            or ""
        ).strip()

        if not official_name:
            raise RestaurantMenuPricingError(
                code="MENU_ITEM_OFFICIAL_NAME_MISSING",
                message=(
                    "En produkt i restaurangens meny "
                    "saknar officiellt namn."
                ),
                status_code=502,
            )

        unit_price = _parse_menu_price(
            menu_item.get("base_price"),
            item_name=official_name,
        )

        item_currency = _parse_currency(
            menu_item.get("currency"),
            item_name=official_name,
        )

        if verified_currency is None:
            verified_currency = item_currency

        elif verified_currency != item_currency:
            raise RestaurantMenuPricingError(
                code="MIXED_MENU_CURRENCIES",
                message=(
                    "Beställningen innehåller produkter "
                    "med olika valutor."
                ),
                status_code=409,
            )

        quantity = requested_item.quantity

        line_total = (
            unit_price * Decimal(quantity)
        ).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

        verified_total += line_total

        verified_lines.append(
            {
                "menu_item_id": menu_item_id,
                "requested_name": requested_name,
                "official_name": official_name,
                "quantity": quantity,
                "unit_price": float(unit_price),
                "line_total": float(line_total),
                "currency": item_currency,
            }
        )

    if verified_currency is None:
        raise RestaurantMenuPricingError(
            code="ORDER_ITEMS_EMPTY",
            message=(
                "Beställningen innehåller inga produkter."
            ),
            status_code=422,
        )

    verified_total = verified_total.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )

    return {
        "success": True,
        "restaurant_id": context.restaurant_id,
        "restaurant_name": (
            context.restaurant_name
        ),
        "currency": verified_currency,
        "total": float(verified_total),
        "items": verified_lines,
    }
