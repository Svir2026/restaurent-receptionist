from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.tool_auth import (
    ToolRestaurantContext,
    require_restaurant_tool_context,
)


router = APIRouter(
    prefix="/v2",
    tags=["restaurant-tools-v2"],
)


@router.get("/tool-context/check")
def check_restaurant_tool_context(
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> dict[str, object]:
    """
    Verify a restaurant tool token and return the safe,
    server-resolved restaurant context.

    This endpoint is read-only. It does not return the token,
    change the restaurant, or write anything to Supabase.
    """

    return {
        "success": True,
        "read_only": True,
        "restaurant": {
            "restaurant_id": str(
                context.restaurant_id
            ),
            "name": context.restaurant_name,
            "slug": context.restaurant_slug,
            "is_active": (
                context.restaurant_is_active
            ),
        },
        "provisioning": {
            "job_id": (
                str(context.provisioning_job_id)
                if context.provisioning_job_id
                else None
            ),
            "status": (
                context.provisioning_job_status
            ),
            "current_step": (
                context.provisioning_current_step
            ),
        },
    }
