from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


OrderTypeV2 = Literal["dine_in", "takeaway"]

MenuResolutionStatus = Literal[
    "MATCH",
    "NO_MATCH",
    "AMBIGUOUS",
]

MenuResolutionAction = Literal[
    "continue",
    "repeat",
    "not_on_menu",
    "technical_stop",
    "clarify",
]

MenuResolutionSource = Literal[
    "canonical",
    "alias",
]


class ResolveMenuItemsV2Request(BaseModel):
    """
    Resolve products from the raw ElevenLabs conversation history.

    No candidate product name is accepted from the LLM. The backend
    extracts the latest user utterance and matches it deterministically.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    conversation_history: list[dict] | dict[str, Any] | str = Field(...)

    @model_validator(mode="after")
    def parse_conversation_history(
        self,
    ) -> "ResolveMenuItemsV2Request":
        if isinstance(self.conversation_history, str):
            raw_value = self.conversation_history.strip()

            if not raw_value:
                raise ValueError(
                    "conversation_history must not be empty"
                )

            try:
                parsed_value = json.loads(raw_value)

            except json.JSONDecodeError as error:
                raise ValueError(
                    "conversation_history must be a JSON array or "
                    "a JSON-stringified array"
                ) from error

            self.conversation_history = parsed_value

        if isinstance(self.conversation_history, dict):
            entries = self.conversation_history.get("entries")
            if not isinstance(entries, list):
                raise ValueError(
                    "conversation_history object must contain an "
                    "entries array"
                )
            self.conversation_history = entries

        if not isinstance(self.conversation_history, list):
            raise ValueError(
                "conversation_history must be an array or an object "
                "with an entries array"
            )

        if not self.conversation_history:
            raise ValueError(
                "conversation_history must contain at least one entry"
            )

        return self


class ResolveMenuItemsV2Match(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_item_id: UUID
    official_name: str = Field(min_length=1)
    customer_display_name: str = Field(min_length=1)
    matched_text: str = Field(min_length=1)
    match_source: MenuResolutionSource


class ResolveMenuItemsV2Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: MenuResolutionStatus
    action: MenuResolutionAction
    unresolved_attempt: int = Field(ge=0, le=3)
    stop_recovery: bool
    customer_message: str | None = None
    matches: list[ResolveMenuItemsV2Match]


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
        default="Telefonkund",
        min_length=1,
        max_length=120,
    )

    customer_phone: str = Field(
        ...,
        min_length=5,
        max_length=32,
    )

    order_type: OrderTypeV2 = "takeaway"

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

class CheckOrderStatusV2Request(BaseModel):
    """
    Restaurant-scoped status request.

    The restaurant is resolved from X-Svir-Tool-Token.
    The request may therefore contain only the caller's phone.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    customer_phone: str = Field(
        ...,
        min_length=5,
        max_length=32,
    )


class CheckOrderStatusV2Item(BaseModel):
    """
    One verified product stored in a restaurant order.
    """

    model_config = ConfigDict(extra="forbid")

    menu_item_id: UUID
    requested_name: str = Field(
        min_length=1,
        max_length=200,
    )
    official_name: str = Field(
        min_length=1,
        max_length=200,
    )

    quantity: int = Field(
        ge=1,
        le=100,
    )

    notes: str | None = Field(
        default=None,
        max_length=500,
    )

    unit_price: float = Field(ge=0)
    line_total: float = Field(ge=0)

    currency: str = Field(
        min_length=3,
        max_length=3,
    )


class CheckOrderStatusV2Order(BaseModel):
    """
    One restaurant-isolated order returned to the agent.

    order_id is returned for later update or cancellation tool
    calls, but must never be spoken to the customer.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(
        min_length=1,
        max_length=64,
    )

    order_status: str = Field(
        min_length=1,
        max_length=80,
    )

    order_type: OrderTypeV2

    customer_name: str = Field(
        min_length=1,
        max_length=120,
    )

    customer_phone: str = Field(
        min_length=5,
        max_length=32,
    )

    created_at: datetime

    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None

    currency: str = Field(
        min_length=3,
        max_length=3,
    )

    total: float = Field(ge=0)

    items: list[CheckOrderStatusV2Item] = Field(
        min_length=1,
    )

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    cancellation_reason: str | None = Field(
        default=None,
        max_length=500,
    )


class CheckOrderStatusV2Response(BaseModel):
    """
    Recent orders belonging only to the authenticated
    restaurant and normalized caller phone.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool

    restaurant_id: UUID
    restaurant_name: str = Field(min_length=1)

    customer_phone: str = Field(
        min_length=5,
        max_length=32,
    )

    timezone: str = Field(min_length=1)

    orders: list[CheckOrderStatusV2Order]

    order_count: int = Field(ge=0)

class UpdateOrderV2ItemRequest(BaseModel):
    """
    One product in the complete updated order.

    Price fields are forbidden. Railway resolves all prices
    again from the authenticated restaurant's active menu.
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


class UpdateOrderV2Request(BaseModel):
    """
    Restaurant-scoped order update request.

    restaurant_id, price, total, currency, and order status
    are never accepted from the agent.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
    )

    customer_phone: str = Field(
        ...,
        min_length=5,
        max_length=32,
    )

    customer_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    order_type: OrderTypeV2 | None = None

    order_items: (
        list[UpdateOrderV2ItemRequest] | str | None
    ) = Field(
        default=None,
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
    def validate_update_order(
        self,
    ) -> "UpdateOrderV2Request":
        update_fields = {
            "customer_name",
            "order_type",
            "order_items",
            "party_size",
            "dine_in_time",
            "pickup_time",
            "notes",
        }

        provided_updates = (
            self.model_fields_set
            & update_fields
        )

        if not provided_updates:
            raise ValueError(
                "at least one order field must be provided"
            )

        if (
            "customer_name" in self.model_fields_set
            and self.customer_name is None
        ):
            raise ValueError(
                "customer_name cannot be null when provided"
            )

        if (
            "order_type" in self.model_fields_set
            and self.order_type is None
        ):
            raise ValueError(
                "order_type cannot be null when provided"
            )

        if "order_items" in self.model_fields_set:
            if self.order_items is None:
                raise ValueError(
                    "order_items cannot be null when provided"
                )

            if isinstance(self.order_items, str):
                raw_value = self.order_items.strip()

                if not raw_value:
                    raise ValueError(
                        "order_items must not be empty"
                    )

                try:
                    parsed_value = json.loads(
                        raw_value
                    )

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
                    UpdateOrderV2ItemRequest.model_validate(
                        item
                    )
                    for item in parsed_value
                ]

            if not self.order_items:
                raise ValueError(
                    "order_items must contain at least one item"
                )

        if (
            self.dine_in_time is not None
            and self.pickup_time is not None
        ):
            raise ValueError(
                "dine_in_time and pickup_time cannot both "
                "be provided"
            )

        if (
            "order_type" in self.model_fields_set
            and self.order_type == "takeaway"
        ):
            if self.dine_in_time is not None:
                raise ValueError(
                    "dine_in_time is not allowed for takeaway"
                )

        if (
            "order_type" in self.model_fields_set
            and self.order_type == "dine_in"
        ):
            if self.dine_in_time is None:
                raise ValueError(
                    "dine_in_time is required when changing "
                    "order_type to dine_in"
                )

            if self.pickup_time is not None:
                raise ValueError(
                    "pickup_time is not allowed for dine_in"
                )

        return self


class UpdateOrderV2Response(BaseModel):
    """
    Safe response after one restaurant-scoped order update.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    updated: bool

    restaurant_id: UUID
    restaurant_name: str = Field(min_length=1)

    order_id: str = Field(
        min_length=1,
        max_length=64,
    )

    order_status: Literal["new order"]
    order_type: OrderTypeV2

    customer_name: str = Field(
        min_length=1,
        max_length=120,
    )

    customer_phone: str = Field(
        min_length=5,
        max_length=32,
    )

    updated_fields: list[str]

    created_at: datetime
    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None

    currency: str = Field(
        min_length=3,
        max_length=3,
    )

    total: float = Field(ge=0)

    items: list[CheckOrderStatusV2Item] = Field(
        min_length=1,
    )

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

class CancelOrderV2Request(BaseModel):
    """
    Restaurant-scoped cancellation request.

    Railway resolves restaurant_id from X-Svir-Tool-Token.
    The agent cannot provide restaurant identity, status,
    revision, price, or total.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
    )

    customer_phone: str = Field(
        ...,
        min_length=5,
        max_length=32,
    )

    reason: str | None = Field(
        default=None,
        max_length=500,
    )


class CancelOrderV2Response(BaseModel):
    """
    Safe response after one restaurant-scoped order has been
    cancelled without deleting the order row.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    cancelled: bool

    restaurant_id: UUID
    restaurant_name: str = Field(min_length=1)

    order_id: str = Field(
        min_length=1,
        max_length=64,
    )

    order_status: Literal["cancelled"]

    order_revision: int = Field(
        ge=1,
    )

    customer_name: str = Field(
        min_length=1,
        max_length=120,
    )

    customer_phone: str = Field(
        min_length=5,
        max_length=32,
    )

    order_type: OrderTypeV2

    created_at: datetime
    updated_at: datetime

    dine_in_time: datetime | None = None
    pickup_time: datetime | None = None

    currency: str = Field(
        min_length=3,
        max_length=3,
    )

    total: float = Field(ge=0)

    items: list[CheckOrderStatusV2Item] = Field(
        min_length=1,
    )

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    cancellation_reason: str | None = Field(
        default=None,
        max_length=500,
    )
