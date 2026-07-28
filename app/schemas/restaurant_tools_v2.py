from __future__ import annotations

import json
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class CalculateOrderTotalV2ItemRequest(BaseModel):
    """
    One menu item requested by the restaurant agent.

    The agent may provide only the item name and quantity.
    Price and restaurant identity are resolved by Railway.
    """

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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
