from __future__ import annotations

from datetime import datetime
import json
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


OrderTypeV2 = Literal["dine_in", "takeaway"]


class CalculateOrderTotalV2ItemRequest(BaseModel):
    """
    One menu item requested by the restaurant agent.

    The agent may provide only the item name and quantity.
    Price and restaurant identity are resolved by Railway.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )

    quantity: int = Field(
        default=1,
        ge=1,
        le=100,
    )


class CalculateOrderTotalV2Request(BaseModel):
    """
    Secure request for restaurant-specific price calculation.

    restaurant_id, price, currency, and total must never be
    accepted from the agent.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_items: (
        list[CalculateOrderTotalV2ItemRequest] | str
    ) = Field(
        ...,
        min_length=1,
    )

    @model_validator(mode="after")
    def parse_order_items(
        self,
    ) -> "CalculateOrderTotalV2Request":
        if isinstance(self.order_items, str):
            raw_value = self.order_items.strip()

            if not raw_value:
                raise ValueError(
                    "order_items must not be empty"
                )

            try:
                parsed_value = json.loads(raw_value)

            except json.JSONDecodeError as error:
                raise ValueError(
                    "order_items must be a JSON array or "
                    "a JSON-stringified array"
                ) from error

            if not isinstance(parsed_value, list):
                raise ValueError(
                    "order_items must be a JSON array"
                )

            self.order_items = [
                CalculateOrderTotalV2ItemRequest.model_validate(
                    item
                )
                for item in parsed_value
            ]

        if not self.order_items:
            raise ValueError(
                "order_items must contain at least one item"
            )

        return self


class CalculateOrderTotalV2Line(BaseModel):
    """
    One verified price line resolved from the restaurant menu.
    """

    model_config = ConfigDict(extra="forbid")

    menu_item_id: UUID
    requested_name: str
    official_name: str
    quantity: int = Field(ge=1)

    unit_price: float = Field(ge=0)
    line_total: float = Field(ge=0)

    currency: str = Field(
        min_length=3,
        max_length=3,
    )


class CalculateOrderTotalV2Response(BaseModel):
    """
    Verified total returned by Railway after reading prices from
    the authenticated restaurant's Supabase menu.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool

    restaurant_id: UUID
    restaurant_name: str = Field(min_length=1)

    currency: str = Field(
        min_length=3,
        max_length=3,
    )

    total: float = Field(ge=0)

    items: list[CalculateOrderTotalV2Line] = Field(
        min_length=1
    )


class SubmitOrderV2ItemRequest(BaseModel):
    """
    One order item submitted by the restaurant agent.

    Price fields are forbidden. Railway verifies the item and
    reads its price from the authenticated restaurant's menu.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )

    quantity: int = Field(
        default=1,
        ge=1,
        le=100,
    )

    notes: str | None = Field(
        default=None,
        max_length=500,
    )


class SubmitOrderV2Request(BaseModel):
    """
    Restaurant-scoped order request.

    restaurant_id, prices, total, currency, and order status
    are always resolved or created by Railway.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    conversation_id: str = Field(
        ...,
        min_length=8,
        max_length=200,
    )

    customer_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
    )

    customer_phone: str = Field(
        ...,
        min_length=5,
        max_length=32,
    )

    order_type: OrderTypeV2

    order_items: (
        list[SubmitOrderV2ItemRequest] | str
    ) = Field(
        ...,
        min_length=1,
    )

    party_size: int | None = Field(
        default=None,
        ge=1,
        le=100,
    )

    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_submit_order(
        self,
    ) -> "SubmitOrderV2Request":
        if isinstance(self.order_items, str):
            raw_value = self.order_items.strip()

            if not raw_value:
                raise ValueError(
                    "order_items must not be empty"
                )

            try:
                parsed_value = json.loads(raw_value)

            except json.JSONDecodeError as error:
                raise ValueError(
                    "order_items must be a JSON array or "
                    "a JSON-stringified array"
                ) from error

            if not isinstance(parsed_value, list):
                raise ValueError(
                    "order_items must be a JSON array"
                )

            self.order_items = [
                SubmitOrderV2ItemRequest.model_validate(
                    item
                )
                for item in parsed_value
            ]

        if not self.order_items:
            raise ValueError(
                "order_items must contain at least one item"
            )

        if self.order_type == "takeaway":
            if self.pickup_time is None:
                raise ValueError(
                    "pickup_time is required for takeaway"
                )

            if self.dine_in_time is not None:
                raise ValueError(
                    "dine_in_time is not allowed for takeaway"
                )

        if self.order_type == "dine_in":
            if self.dine_in_time is None:
                raise ValueError(
                    "dine_in_time is required for dine_in"
                )

            if self.pickup_time is not None:
                raise ValueError(
                    "pickup_time is not allowed for dine_in"
                )

        return self


class SubmitOrderV2Response(BaseModel):
    """
    Safe response after a restaurant-scoped order is stored.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    idempotent_replay: bool

    restaurant_id: UUID
    restaurant_name: str = Field(min_length=1)

    order_id: str = Field(
        min_length=1,
        max_length=64,
    )

    order_status: Literal["new order"]
    order_type: OrderTypeV2

    customer_name: str = Field(min_length=1)

    created_at: datetime
    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None

    currency: str = Field(
        min_length=3,
        max_length=3,
    )

    total: float = Field(ge=0)

    items: list[CalculateOrderTotalV2Line] = Field(
        min_length=1
    )
