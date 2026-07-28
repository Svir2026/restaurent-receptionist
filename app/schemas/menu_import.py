from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


AliasType = Literal[
    "spoken",
    "spelling",
    "synonym",
    "customer_phrase",
    "other",
]

OptionGroupType = Literal[
    "protein",
    "size",
    "addon",
    "choice",
    "custom",
]

SelectionMode = Literal[
    "single",
    "multiple",
]

ItemType = Literal[
    "food",
    "drink",
    "sauce",
    "addon",
    "other",
]

PresenceType = Literal[
    "contains",
    "may_contain",
    "traces",
    "cross_contamination_unknown",
]

VerificationStatus = Literal[
    "unverified",
    "verified",
    "needs_review",
]

MenuSourceType = Literal[
    "manual",
    "csv",
    "xlsx",
    "pdf",
    "api",
    "other",
]


class MenuSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class MenuValidationIssue(MenuSchema):
    code: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=500)
    message: str = Field(..., min_length=1, max_length=500)


class MenuAlias(MenuSchema):
    alias: str = Field(..., min_length=1, max_length=200)
    alias_type: AliasType = "spoken"
    priority: int = Field(default=100, ge=0)


class MenuOption(MenuSchema):
    source_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    name: str = Field(..., min_length=1, max_length=200)
    kitchen_name: str = Field(..., min_length=1, max_length=200)

    price_delta: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    is_default: bool = False
    aliases: list[MenuAlias] = Field(
        default_factory=list,
        max_length=100,
    )
    sort_order: int = Field(default=0, ge=0)


class MenuOptionGroup(MenuSchema):
    source_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    name: str = Field(..., min_length=1, max_length=200)

    group_type: OptionGroupType = "custom"
    selection_mode: SelectionMode = "single"

    is_required: bool = False
    min_select: int = Field(default=0, ge=0)
    max_select: int | None = Field(default=None, ge=0)

    options: list[MenuOption] = Field(
        default_factory=list,
        max_length=100,
    )
    sort_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_selection_rules(self) -> "MenuOptionGroup":
        if (
            self.max_select is not None
            and self.max_select < self.min_select
        ):
            raise ValueError(
                "max_select cannot be smaller than min_select"
            )

        if self.is_required and self.min_select < 1:
            raise ValueError(
                "a required option group must have min_select of at least 1"
            )

        if (
            self.selection_mode == "single"
            and self.max_select is not None
            and self.max_select > 1
        ):
            raise ValueError(
                "a single-selection group cannot have max_select above 1"
            )

        return self


class MenuIngredient(MenuSchema):
    name: str = Field(..., min_length=1, max_length=200)
    can_remove: bool = False
    is_optional: bool = False
    notes: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0, ge=0)


class MenuAllergen(MenuSchema):
    allergen_name: str = Field(..., min_length=1, max_length=200)
    presence_type: PresenceType
    verification_status: VerificationStatus = "unverified"
    notes: str | None = Field(default=None, max_length=500)


class MenuCategory(MenuSchema):
    source_key: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MenuItem(MenuSchema):
    source_key: str = Field(..., min_length=1, max_length=120)
    category_source_key: str = Field(
        ...,
        min_length=1,
        max_length=120,
    )

    menu_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )

    official_name: str = Field(..., min_length=1, max_length=200)
    customer_display_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )
    kitchen_display_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )

    description: str | None = Field(default=None, max_length=2000)
    item_type: ItemType = "food"

    base_price: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    currency: str = Field(
        default="SEK",
        min_length=3,
        max_length=3,
    )

    is_active: bool = True
    allow_customer_notes: bool = True
    sort_order: int = Field(default=0, ge=0)

    aliases: list[MenuAlias] = Field(
        default_factory=list,
        max_length=100,
    )
    option_groups: list[MenuOptionGroup] = Field(
        default_factory=list,
        max_length=30,
    )
    ingredients: list[MenuIngredient] = Field(
        default_factory=list,
        max_length=100,
    )
    allergens: list[MenuAllergen] = Field(
        default_factory=list,
        max_length=50,
    )

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()

        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError(
                "currency must contain exactly three letters"
            )

        return normalized


class ValidateMenuImportRequest(MenuSchema):
    restaurant_id: UUID
    provisioning_job_id: UUID
    idempotency_key: UUID

    source_type: MenuSourceType = "manual"
    source_filename: str | None = Field(
        default=None,
        max_length=255,
    )

    categories: list[MenuCategory] = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    items: list[MenuItem] = Field(
        ...,
        min_length=1,
        max_length=2000,
    )


class ValidateMenuImportResponse(MenuSchema):
    valid: bool

    restaurant_id: UUID
    provisioning_job_id: UUID

    category_count: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)
    alias_count: int = Field(default=0, ge=0)
    option_group_count: int = Field(default=0, ge=0)
    option_count: int = Field(default=0, ge=0)
    ingredient_count: int = Field(default=0, ge=0)
    allergen_count: int = Field(default=0, ge=0)

    warnings: list[MenuValidationIssue] = Field(default_factory=list)
    errors: list[MenuValidationIssue] = Field(default_factory=list)


class ImportMenuResponse(MenuSchema):
    success: bool = True

    restaurant_id: UUID
    provisioning_job_id: UUID
    import_id: UUID

    idempotent_replay: bool

    category_count: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)
    alias_count: int = Field(default=0, ge=0)
    option_group_count: int = Field(default=0, ge=0)
    option_count: int = Field(default=0, ge=0)
    ingredient_count: int = Field(default=0, ge=0)
    allergen_count: int = Field(default=0, ge=0)

    next_step: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    warnings: list[MenuValidationIssue] = Field(
        default_factory=list,
    )
