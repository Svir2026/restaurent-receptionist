from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


OrderType = Literal["dine_in", "takeaway"]
PizzaSize = Literal["small", "large"]


class OrderItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)
    size: PizzaSize | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, ge=0)


class SubmitOrderRequest(BaseModel):
    customer_name: str | None = Field(default=None, max_length=120)
    customer_phone: str | None = Field(default=None, max_length=32)
    order_type: OrderType | None = None
    order_items: list[OrderItem] | str = Field(..., min_length=1)
    total: float | None = Field(default=None, ge=0)

    party_size: int | None = Field(default=None, ge=1)
    dine_in_time: datetime | None = None

    pickup_time: datetime | None = None

    notes: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default="elevenlabs", max_length=80)

    @model_validator(mode="after")
    def _validate_by_type(self) -> "SubmitOrderRequest":
        if isinstance(self.order_items, str):
            raw = self.order_items.strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError("order_items must be a JSON array or a JSON-stringified array") from e
            if not isinstance(parsed, list):
                raise ValueError("order_items must be a JSON array")
            self.order_items = [OrderItem.model_validate(x) for x in parsed]

        if self.total is None:
            raise ValueError("total is required")
        return self


class SubmitOrderResponse(BaseModel):
    message: str | None = None
    order_id: str | None = None
    order_status: str | None = None
    created_at: datetime | None = None
    total: float | None = None


class CheckOrderStatusResponseItem(BaseModel):
    order_id: str
    order_status: str
    order_type: OrderType
    customer_name: str | None = None
    customer_phone: str | None = None
    order_items: str | None = None
    party_size: int | None = None
    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None
    scheduled_time: datetime | None = None
    created_at: datetime
    total: float | None = None
    notes: str | None = None
    source: str | None = None
    cancellation_reason: str | None = None


class CheckOrderStatusResponse(BaseModel):
    message: str | None = None
    caller_number: str | None = None
    timezone: str
    window_start: datetime
    window_end: datetime
    orders: list[CheckOrderStatusResponseItem]


class UpdateOrderRequest(BaseModel):
    caller_number: str | None = Field(default=None, max_length=32)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)

    customer_name: str | None = Field(default=None, max_length=120)
    order_type: OrderType | None = None
    order_items: list[OrderItem] | str | None = Field(default=None, min_length=1)
    total: float | None = Field(default=None, ge=0)
    party_size: int | None = Field(default=None, ge=1)
    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_has_update(self) -> "UpdateOrderRequest":
        update_fields = {
            "customer_name",
            "order_type",
            "order_items",
            "total",
            "party_size",
            "dine_in_time",
            "pickup_time",
            "notes",
        }
        if not (self.model_fields_set & update_fields):
            raise ValueError("at least one order field must be provided to update")
        if "order_type" in self.model_fields_set and self.order_type is None:
            raise ValueError("order_type cannot be null when provided")
        if "order_items" in self.model_fields_set and self.order_items is None:
            raise ValueError("order_items cannot be null when provided")
        if "order_items" in self.model_fields_set and isinstance(self.order_items, str):
            raw = self.order_items.strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError("order_items must be a JSON array or a JSON-stringified array") from e
            if not isinstance(parsed, list):
                raise ValueError("order_items must be a JSON array")
            self.order_items = [OrderItem.model_validate(x) for x in parsed]
        if "order_items" in self.model_fields_set and "total" not in self.model_fields_set:
            raise ValueError("total is required when order_items is updated")
        if "total" in self.model_fields_set and self.total is None:
            raise ValueError("total cannot be null when provided")
        return self


class UpdateOrderResponse(BaseModel):
    message: str | None = None
    updated: bool
    order_id: str | None = None
    order_status: str | None = None
    updated_fields: list[str] = Field(default_factory=list)
    total: float | None = None


class CancelOrderRequest(BaseModel):
    caller_number: str | None = Field(default=None, max_length=32)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=500)


class CancelOrderResponse(BaseModel):
    message: str | None = None
    cancelled: bool
    cancelled_orders: list[dict[str, Any]]


class CalculateOrderTotalItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)
    price: float = Field(..., ge=0)


class CalculateOrderTotalRequest(BaseModel):
    order_items: list[CalculateOrderTotalItem] | str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _parse_order_items(self) -> "CalculateOrderTotalRequest":
        if isinstance(self.order_items, str):
            raw = self.order_items.strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError("order_items must be a JSON array or a JSON-stringified array") from e
            if not isinstance(parsed, list):
                raise ValueError("order_items must be a JSON array")
            self.order_items = [CalculateOrderTotalItem.model_validate(x) for x in parsed]

        if not self.order_items:
            raise ValueError("order_items must contain at least one item")
        return self


class CalculateOrderTotalLine(BaseModel):
    name: str
    quantity: int
    price: float
    line_total: float


class CalculateOrderTotalResponse(BaseModel):
    total: float
    items: list[CalculateOrderTotalLine]
