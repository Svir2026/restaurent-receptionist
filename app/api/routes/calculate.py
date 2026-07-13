from __future__ import annotations

from fastapi import APIRouter

from app.schemas.orders import (
    CalculateOrderTotalLine,
    CalculateOrderTotalRequest,
    CalculateOrderTotalResponse,
)
from app.services.order_total import compute_order_total

router = APIRouter(tags=["orders"])


@router.post(
    "/calculate-order-total",
    response_model=CalculateOrderTotalResponse,
)
def calculate_order_total(payload: CalculateOrderTotalRequest) -> CalculateOrderTotalResponse:
    items = [item.model_dump() for item in payload.order_items]
    total, breakdown = compute_order_total(items)
    return CalculateOrderTotalResponse(
        total=total,
        items=[CalculateOrderTotalLine.model_validate(line) for line in breakdown],
    )
