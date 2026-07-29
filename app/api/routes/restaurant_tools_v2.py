from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.tool_auth import (
    ToolRestaurantContext,
    require_restaurant_tool_context,
)
from app.schemas.restaurant_tools_v2 import (
    CalculateOrderTotalV2Request,
    CalculateOrderTotalV2Response,
    SubmitOrderV2Request,
    SubmitOrderV2Response,
)
from app.services.restaurant_menu_pricing import (
    RestaurantMenuPricingError,
    calculate_restaurant_menu_total,
)
from app.services.restaurant_order_submitter import (
    RestaurantOrderSubmissionError,
    submit_restaurant_order,
)


router = APIRouter(
    prefix="/v2",
    tags=["restaurant-tools-v2"],
)


@router.post(
    "/calculate-order-total",
    response_model=CalculateOrderTotalV2Response,
)
def calculate_order_total_v2(
    payload: CalculateOrderTotalV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> CalculateOrderTotalV2Response:
    """
    Calculate a verified order total using prices from the
    authenticated restaurant's active Supabase menu.

    The caller cannot provide restaurant_id, price, currency,
    or total. Railway resolves the restaurant from the secure
    tool token and reads all prices from Supabase.
    """

    try:
        result = calculate_restaurant_menu_total(
            context=context,
            request=payload,
        )

    except RestaurantMenuPricingError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return CalculateOrderTotalV2Response.model_validate(
        result
    )


@router.post(
    "/submit-order",
    response_model=SubmitOrderV2Response,
)
def submit_order_v2(
    payload: SubmitOrderV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> SubmitOrderV2Response:
    """
    Verify and submit one restaurant-scoped order.

    Railway resolves the restaurant from X-Svir-Tool-Token,
    verifies every product and price against that restaurant's
    active Supabase menu, and saves the order atomically.

    The caller cannot provide restaurant_id, price, total,
    currency, or order status.
    """

    try:
        result = submit_restaurant_order(
            context=context,
            request=payload,
        )

    except RestaurantOrderSubmissionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return SubmitOrderV2Response.model_validate(
        result
    )
