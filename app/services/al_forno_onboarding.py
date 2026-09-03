from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from app.schemas.menu_import import ValidateMenuImportRequest
from app.services.libanon_menu_catalog import DEFAULT_CATALOG_PATH


class AlFornoOnboardingError(RuntimeError):
    pass


def _minor_to_major(value: object, *, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlFornoOnboardingError(
            f"{path} must contain an integer minor-unit amount"
        )
    if value < 0:
        raise AlFornoOnboardingError(
            f"{path} must not be negative"
        )
    return Decimal(value) / Decimal(100)


def _aliases(values: object) -> list[dict[str, object]]:
    if values is None:
        return []
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise AlFornoOnboardingError(
            "menu aliases must be a list of strings"
        )
    return [
        {
            "alias": value,
            "alias_type": "spoken",
            "priority": 100,
        }
        for value in values
    ]


def _option_group(
    raw: dict[str, Any],
    *,
    item_index: int,
    group_index: int,
) -> dict[str, object]:
    options = raw.get("options")
    if not isinstance(options, list):
        raise AlFornoOnboardingError(
            "option group options must be a list"
        )

    converted_options = []
    for option_index, option in enumerate(options):
        if not isinstance(option, dict):
            raise AlFornoOnboardingError(
                "menu option must be an object"
            )
        converted_options.append(
            {
                "source_key": option.get("source_key"),
                "name": option.get("name"),
                "kitchen_name": option.get("kitchen_name"),
                "price_delta": _minor_to_major(
                    option.get("price_delta_minor"),
                    path=(
                        f"items[{item_index}].option_groups"
                        f"[{group_index}].options[{option_index}]"
                        ".price_delta_minor"
                    ),
                ),
                "is_default": option.get("is_default", False),
                "aliases": _aliases(option.get("aliases")),
                "sort_order": option.get("sort_order", option_index),
            }
        )

    return {
        "source_key": raw.get("source_key"),
        "name": raw.get("name"),
        "group_type": raw.get("group_type", "custom"),
        "selection_mode": raw.get("selection_mode", "single"),
        "is_required": raw.get("is_required", False),
        "min_select": raw.get("min_select", 0),
        "max_select": raw.get("max_select"),
        "options": converted_options,
        "sort_order": raw.get("sort_order", group_index),
    }


def _unverified_prices(raw: dict[str, Any]) -> tuple[str, ...]:
    items = raw.get("items")
    if not isinstance(items, list):
        raise AlFornoOnboardingError(
            "Al Forno catalog items must be a list"
        )

    unresolved = []
    for item in items:
        if not isinstance(item, dict):
            raise AlFornoOnboardingError(
                "Al Forno catalog item must be an object"
            )
        metadata = item.get("metadata")
        status = (
            metadata.get("price_verification_status")
            if isinstance(metadata, dict)
            else None
        )
        if status not in {"provided_by_user", "verified"}:
            unresolved.append(str(item.get("official_name") or "unknown"))
    return tuple(unresolved)


def build_al_forno_menu_import_request(
    *,
    restaurant_id: UUID,
    provisioning_job_id: UUID,
    idempotency_key: UUID,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    allow_unverified_prices: bool = False,
) -> ValidateMenuImportRequest:
    """Build the generic v2 menu-import payload for one Al Forno tenant."""

    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlFornoOnboardingError(
            "Al Forno catalog could not be loaded"
        ) from error

    if not isinstance(raw, dict):
        raise AlFornoOnboardingError(
            "Al Forno catalog root must be an object"
        )

    unresolved = _unverified_prices(raw)
    if unresolved and not allow_unverified_prices:
        raise AlFornoOnboardingError(
            "Al Forno has prices requiring review: "
            + ", ".join(unresolved)
        )

    categories = raw.get("categories")
    items = raw.get("items")
    if not isinstance(categories, list) or not isinstance(items, list):
        raise AlFornoOnboardingError(
            "Al Forno catalog is missing categories or items"
        )

    converted_items = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AlFornoOnboardingError(
                "Al Forno catalog item must be an object"
            )
        groups = item.get("option_groups") or []
        if not isinstance(groups, list):
            raise AlFornoOnboardingError(
                "item option_groups must be a list"
            )

        converted_groups = []
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise AlFornoOnboardingError(
                    "menu option group must be an object"
                )
            converted_groups.append(
                _option_group(
                    group,
                    item_index=item_index,
                    group_index=group_index,
                )
            )

        converted_items.append(
            {
                "source_key": item.get("source_key"),
                "category_source_key": item.get("category_source_key"),
                "menu_number": item.get("menu_number"),
                "official_name": item.get("official_name"),
                "customer_display_name": item.get("customer_display_name"),
                "kitchen_display_name": item.get("kitchen_display_name"),
                "description": item.get("description"),
                "item_type": item.get("item_type", "food"),
                "base_price": _minor_to_major(
                    item.get("base_price_minor"),
                    path=f"items[{item_index}].base_price_minor",
                ),
                "currency": item.get("currency", raw.get("currency", "SEK")),
                "is_active": item.get("is_active", True),
                "allow_customer_notes": item.get(
                    "allow_customer_notes", True
                ),
                "sort_order": item.get("sort_order", item_index),
                "aliases": _aliases(item.get("aliases")),
                "option_groups": converted_groups,
                "ingredients": [],
                "allergens": [],
                "metadata": item.get("metadata") or {},
            }
        )

    return ValidateMenuImportRequest(
        restaurant_id=restaurant_id,
        provisioning_job_id=provisioning_job_id,
        idempotency_key=idempotency_key,
        source_type="other",
        source_filename=catalog_path.name,
        categories=categories,
        items=converted_items,
    )
