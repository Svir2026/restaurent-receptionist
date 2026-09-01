from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


RESTAURANT_ID = "613079d4-7680-40b0-a5cc-465e813a5267"
RESTAURANT_SLUG = "lebanon-kolgrill"
SOURCE_URL = "https://wolt.com/sv/swe/marsta/restaurant/" "lebanon-kolgrill-pizzeria"

# These are the aliases explicitly present in the current Libanon agent
# instructions or its attached knowledge base. The snapshot builder must not
# invent additional semantic aliases.
APPROVED_ITEM_ALIASES: dict[str, list[str]] = {
    "Capricciosa": [
        "carpacciosa",
        "kapricciosa",
        "kaprichosa",
        "capuchosa",
    ],
    "Vesuvio": ["vescivio", "vesovio"],
    "Margherita": ["margarita"],
    "Hawaii": ["havaj"],
    "Kebabpizza": ["kebab pizza"],
    "Favorit": [
        "favorite",
        "kycklingpizza",
        "chickenpizza",
        "kycklingcurrypizza",
        "curry kycklingpizza",
        "kycklingpizza med curry",
    ],
    "Vitlökssås (I Burk)": [
        "vitlöksdip",
        "vitlöksdipp",
        "vitlökssås",
    ],
    "Bearnaisesås": [
        "bea",
        "bearnaise",
        "bearnaisedipp",
    ],
    "Shish Taouk": ["kycklingspett", "shish tawook"],
    "Shish Kafta": ["köttfärsspett", "nötfärsspett"],
    "Lamm Kafta": ["lammfärsspett"],
    "Coca-Cola Original Taste 33 cl": ["cola", "coca cola"],
    "Coca-Cola Zero Sugar 33 cl": ["cola zero", "coca cola zero"],
    "Fanta Orange 33cl - Fanta": ["fanta"],
    "Kebabtallrik Barn": [
        "kebabtallrik för barn",
        "barn kebabtallrik",
    ],
    "Shawarmatallrik Barn": [
        "shawarmatallrik för barn",
        "barn shawarmatallrik",
    ],
    "Chicken Bits Barn": [
        "chicken bits för barn",
        "barn chicken bits",
    ],
    "Hamburgertallrik Barn": [
        "hamburgertallrik för barn",
        "barn hamburgertallrik",
    ],
}


# Name corrections are verified against the current ElevenLabs knowledge base
# and matching Wolt ingredient descriptions. Wolt's source name remains the
# kitchen display name and an accepted source alias.
KB_CANONICAL_NAMES: dict[str, str] = {
    "Baba Ganoush": "Baba Ganouch",
    "Muhammara": "Mehammara",
    "Mixsallad": "Blandad sallad",
    "Kastaletta Ghanam": "Ghanam Castle",
    "Vegetarisk Halloumispett": "Vegetarisk Halloumi",
    "Libanesisk Mixtallrik": "Libanesisk mix tallrik",
    "Shawrmatallrik": "Shawarmatallrik",
    "Orientalet": "Oriental",
    "Gorgozola": "Gorgonzola",
    "Vegetariana": "Vegetarisk",
    "Calzone Special": "Bussola inbakad",
    "Flygande Tefat": "Flying Saucer",
    "Kärlek": "Love",
    "Favorit": "Favorite",
    "Kebabbåt": "Kebab Boat",
    "Josef": "Josef inbakad",
    "Kebabpizza": "Kebab Pizza",
}


def _canonical_name(item_name: str, category_name: str) -> str:
    if item_name == "Crème Toum" and category_name == "Kalla Meze":
        return "Vitlökskräm"
    return KB_CANONICAL_NAMES.get(item_name, item_name)


def _group_type(name: str) -> str:
    normalized = name.casefold()
    if normalized == "storlek":
        return "size"
    if normalized == "kött":
        return "protein"
    if normalized in {
        "extra ingredienser",
        "extra ingredienser familjepizza",
        "glutenfri",
    }:
        return "addon"
    return "choice"


def _item_type(category_name: str) -> str:
    normalized = category_name.casefold()
    if normalized == "dryck":
        return "drink"
    if normalized == "tillbehör & sås":
        return "sauce"
    return "food"


def _is_default_option(
    *,
    group: dict[str, Any],
    attachment: dict[str, Any],
    option_value: dict[str, Any],
) -> bool:
    minimum = int(attachment["multi_choice_config"]["total_range"]["min"])
    return (
        group["name"].casefold() == "storlek"
        and minimum > 0
        and option_value["id"] == group.get("default_value")
    )


def build_snapshot(raw: dict[str, Any], *, raw_sha256: str) -> dict[str, Any]:
    options_by_id = {value["id"]: value for value in raw["options"]}
    items_by_id = {value["id"]: value for value in raw["items"]}

    categories: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for category_order, category in enumerate(raw["categories"]):
        categories.append(
            {
                "source_key": category["id"],
                "name": category["name"],
                "description": category.get("description") or None,
                "sort_order": category_order,
                "is_active": True,
            }
        )

        for item_order, item_id in enumerate(category["item_ids"]):
            item = items_by_id[item_id]
            canonical_name = _canonical_name(item["name"], category["name"])
            aliases = list(APPROVED_ITEM_ALIASES.get(item["name"], []))
            if canonical_name != item["name"]:
                aliases.append(item["name"])
            item_option_groups: list[dict[str, Any]] = []

            for group_order, attachment in enumerate(item.get("options", [])):
                group = options_by_id[attachment["option_id"]]
                selection_range = attachment["multi_choice_config"]["total_range"]
                minimum = int(selection_range["min"])
                maximum = int(selection_range["max"])

                group_options = []
                for option_order, option in enumerate(group["values"]):
                    group_options.append(
                        {
                            "source_key": option["id"],
                            "name": option["name"],
                            "kitchen_name": option["name"],
                            "price_delta_minor": int(option["price"]),
                            "is_default": _is_default_option(
                                group=group,
                                attachment=attachment,
                                option_value=option,
                            ),
                            "aliases": [],
                            "sort_order": option_order,
                        }
                    )

                item_option_groups.append(
                    {
                        "source_key": attachment["id"],
                        "catalog_group_source_key": group["id"],
                        "name": group["name"],
                        "group_type": _group_type(group["name"]),
                        "selection_mode": (
                            "single" if group["type"] == "choice" else "multiple"
                        ),
                        "is_required": minimum > 0,
                        "min_select": minimum,
                        "max_select": maximum,
                        "prerequisite_option_source_keys": attachment.get(
                            "prerequisite_values", []
                        ),
                        "options": group_options,
                        "sort_order": group_order,
                    }
                )

            items.append(
                {
                    "source_key": item["id"],
                    "category_source_key": category["id"],
                    "official_name": canonical_name,
                    "customer_display_name": canonical_name,
                    "kitchen_display_name": item["name"],
                    "description": item.get("description") or None,
                    "item_type": _item_type(category["name"]),
                    "base_price_minor": int(item["price"]),
                    "currency": "SEK",
                    "is_active": item.get("disabled_info") is None,
                    "allow_customer_notes": True,
                    "sort_order": item_order,
                    "aliases": aliases,
                    "option_groups": item_option_groups,
                    "metadata": {
                        "category_name": category["name"],
                        "source_checksum": item.get("checksum"),
                        "source_name": item["name"],
                        "price_verification_status": "needs_review",
                    },
                }
            )

    return {
        "schema_version": 1,
        "restaurant_id": RESTAURANT_ID,
        "restaurant_slug": RESTAURANT_SLUG,
        "currency": "SEK",
        "verification_status": "needs_review",
        "source": {
            "type": "delivery_platform_api_snapshot",
            "provider": "Wolt",
            "url": SOURCE_URL,
            "captured_at": "2026-09-01",
            "raw_sha256": raw_sha256,
            "warning": (
                "Prices conflict with the current ElevenLabs knowledge "
                "base and require restaurant approval before production."
            ),
        },
        "categories": categories,
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    raw_bytes = args.input.read_bytes()
    raw = json.loads(raw_bytes)
    snapshot = build_snapshot(
        raw,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
