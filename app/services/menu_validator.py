from __future__ import annotations

from app.schemas.menu_import import (
    MenuValidationIssue,
    ValidateMenuImportRequest,
    ValidateMenuImportResponse,
)


def _normalize(value: str) -> str:
    """
    Normalize text for duplicate checks.

    Examples:
    "  Cola Zero " -> "cola zero"
    "COLA   ZERO"  -> "cola zero"
    """
    return " ".join(value.strip().casefold().split())


def _issue(
    code: str,
    path: str,
    message: str,
) -> MenuValidationIssue:
    return MenuValidationIssue(
        code=code,
        path=path,
        message=message,
    )


def validate_menu_import(
    payload: ValidateMenuImportRequest,
) -> ValidateMenuImportResponse:
    """
    Perform semantic validation of a structured restaurant menu.

    This function never writes to Supabase and never calls an
    external service.
    """

    errors: list[MenuValidationIssue] = []
    warnings: list[MenuValidationIssue] = []

    alias_count = 0
    option_group_count = 0
    option_count = 0
    ingredient_count = 0
    allergen_count = 0

    # ---------------------------------------------------------
    # Categories
    # ---------------------------------------------------------

    category_keys: dict[str, int] = {}

    for category_index, category in enumerate(payload.categories):
        normalized_key = _normalize(category.source_key)

        if normalized_key in category_keys:
            first_index = category_keys[normalized_key]

            errors.append(
                _issue(
                    code="DUPLICATE_CATEGORY_SOURCE_KEY",
                    path=f"categories[{category_index}].source_key",
                    message=(
                        "Kategorins source_key används redan av "
                        f"categories[{first_index}]."
                    ),
                )
            )
        else:
            category_keys[normalized_key] = category_index

    # ---------------------------------------------------------
    # Menu items
    # ---------------------------------------------------------

    item_keys: dict[str, int] = {}
    menu_numbers: dict[str, int] = {}

    for item_index, item in enumerate(payload.items):
        item_path = f"items[{item_index}]"

        normalized_item_key = _normalize(item.source_key)

        if normalized_item_key in item_keys:
            first_index = item_keys[normalized_item_key]

            errors.append(
                _issue(
                    code="DUPLICATE_ITEM_SOURCE_KEY",
                    path=f"{item_path}.source_key",
                    message=(
                        "Artikelns source_key används redan av "
                        f"items[{first_index}]."
                    ),
                )
            )
        else:
            item_keys[normalized_item_key] = item_index

        # Every item must point to an existing category.
        normalized_category_key = _normalize(
            item.category_source_key
        )

        if normalized_category_key not in category_keys:
            errors.append(
                _issue(
                    code="UNKNOWN_CATEGORY",
                    path=f"{item_path}.category_source_key",
                    message=(
                        "Artikeln hänvisar till en kategori "
                        "som inte finns."
                    ),
                )
            )

        # Menu numbers must be unique when supplied.
        if item.menu_number is not None:
            normalized_menu_number = _normalize(
                item.menu_number
            )

            if normalized_menu_number in menu_numbers:
                first_index = menu_numbers[
                    normalized_menu_number
                ]

                errors.append(
                    _issue(
                        code="DUPLICATE_MENU_NUMBER",
                        path=f"{item_path}.menu_number",
                        message=(
                            "Menynumret används redan av "
                            f"items[{first_index}]."
                        ),
                    )
                )
            else:
                menu_numbers[
                    normalized_menu_number
                ] = item_index

        if item.base_price is None:
            warnings.append(
                _issue(
                    code="MISSING_BASE_PRICE",
                    path=f"{item_path}.base_price",
                    message=(
                        "Artikeln saknar grundpris och behöver "
                        "kontrolleras före publicering."
                    ),
                )
            )

        # -----------------------------------------------------
        # Item aliases
        # -----------------------------------------------------

        seen_item_aliases: dict[str, int] = {}

        for alias_index, alias in enumerate(item.aliases):
            alias_count += 1

            normalized_alias = _normalize(alias.alias)

            if normalized_alias in seen_item_aliases:
                first_index = seen_item_aliases[
                    normalized_alias
                ]

                errors.append(
                    _issue(
                        code="DUPLICATE_ITEM_ALIAS",
                        path=(
                            f"{item_path}.aliases"
                            f"[{alias_index}].alias"
                        ),
                        message=(
                            "Aliaset finns redan på samma artikel "
                            f"i aliases[{first_index}]."
                        ),
                    )
                )
            else:
                seen_item_aliases[
                    normalized_alias
                ] = alias_index

        # -----------------------------------------------------
        # Option groups
        # -----------------------------------------------------

        seen_option_groups: dict[str, int] = {}

        for group_index, group in enumerate(
            item.option_groups
        ):
            option_group_count += 1

            group_path = (
                f"{item_path}.option_groups[{group_index}]"
            )

            group_identity = _normalize(
                group.source_key or group.name
            )

            if group_identity in seen_option_groups:
                first_index = seen_option_groups[
                    group_identity
                ]

                errors.append(
                    _issue(
                        code="DUPLICATE_OPTION_GROUP",
                        path=f"{group_path}.name",
                        message=(
                            "Valgruppen finns redan på samma "
                            f"artikel i option_groups[{first_index}]."
                        ),
                    )
                )
            else:
                seen_option_groups[
                    group_identity
                ] = group_index

            if not group.options:
                errors.append(
                    _issue(
                        code="OPTION_GROUP_WITHOUT_OPTIONS",
                        path=f"{group_path}.options",
                        message=(
                            "En valgrupp måste innehålla minst "
                            "ett val."
                        ),
                    )
                )

            if (
                group.max_select is not None
                and group.max_select > len(group.options)
            ):
                errors.append(
                    _issue(
                        code="MAX_SELECT_EXCEEDS_OPTIONS",
                        path=f"{group_path}.max_select",
                        message=(
                            "max_select får inte vara större än "
                            "antalet tillgängliga val."
                        ),
                    )
                )

            seen_options: dict[str, int] = {}
            default_option_count = 0

            for option_index, option in enumerate(
                group.options
            ):
                option_count += 1

                option_path = (
                    f"{group_path}.options[{option_index}]"
                )

                option_identity = _normalize(
                    option.source_key or option.name
                )

                if option_identity in seen_options:
                    first_index = seen_options[
                        option_identity
                    ]

                    errors.append(
                        _issue(
                            code="DUPLICATE_OPTION",
                            path=f"{option_path}.name",
                            message=(
                                "Valet finns redan i samma "
                                f"valgrupp i options[{first_index}]."
                            ),
                        )
                    )
                else:
                    seen_options[
                        option_identity
                    ] = option_index

                if option.is_default:
                    default_option_count += 1

                seen_option_aliases: dict[str, int] = {}

                for option_alias_index, option_alias in enumerate(
                    option.aliases
                ):
                    alias_count += 1

                    normalized_option_alias = _normalize(
                        option_alias.alias
                    )

                    if (
                        normalized_option_alias
                        in seen_option_aliases
                    ):
                        first_index = seen_option_aliases[
                            normalized_option_alias
                        ]

                        errors.append(
                            _issue(
                                code="DUPLICATE_OPTION_ALIAS",
                                path=(
                                    f"{option_path}.aliases"
                                    f"[{option_alias_index}].alias"
                                ),
                                message=(
                                    "Aliaset finns redan på samma "
                                    "val i "
                                    f"aliases[{first_index}]."
                                ),
                            )
                        )
                    else:
                        seen_option_aliases[
                            normalized_option_alias
                        ] = option_alias_index

            if (
                group.selection_mode == "single"
                and default_option_count > 1
            ):
                errors.append(
                    _issue(
                        code="MULTIPLE_DEFAULT_OPTIONS",
                        path=f"{group_path}.options",
                        message=(
                            "En valgrupp med single-val kan inte "
                            "ha flera standardval."
                        ),
                    )
                )

            if (
                group.is_required
                and group.selection_mode == "single"
                and group.max_select not in (None, 1)
            ):
                errors.append(
                    _issue(
                        code="INVALID_REQUIRED_SINGLE_GROUP",
                        path=f"{group_path}.max_select",
                        message=(
                            "En obligatorisk single-valgrupp ska "
                            "ha max_select satt till ett eller null."
                        ),
                    )
                )

        # -----------------------------------------------------
        # Ingredients
        # -----------------------------------------------------

        seen_ingredients: dict[str, int] = {}

        for ingredient_index, ingredient in enumerate(
            item.ingredients
        ):
            ingredient_count += 1

            normalized_ingredient = _normalize(
                ingredient.name
            )

            if normalized_ingredient in seen_ingredients:
                first_index = seen_ingredients[
                    normalized_ingredient
                ]

                warnings.append(
                    _issue(
                        code="DUPLICATE_INGREDIENT",
                        path=(
                            f"{item_path}.ingredients"
                            f"[{ingredient_index}].name"
                        ),
                        message=(
                            "Ingrediensen förekommer flera gånger "
                            "på samma artikel, första förekomsten "
                            f"är ingredients[{first_index}]."
                        ),
                    )
                )
            else:
                seen_ingredients[
                    normalized_ingredient
                ] = ingredient_index

        # -----------------------------------------------------
        # Allergens
        # -----------------------------------------------------

        seen_allergens: dict[
            tuple[str, str],
            int,
        ] = {}

        for allergen_index, allergen in enumerate(
            item.allergens
        ):
            allergen_count += 1

            allergen_path = (
                f"{item_path}.allergens[{allergen_index}]"
            )

            allergen_identity = (
                _normalize(allergen.allergen_name),
                allergen.presence_type,
            )

            if allergen_identity in seen_allergens:
                first_index = seen_allergens[
                    allergen_identity
                ]

                errors.append(
                    _issue(
                        code="DUPLICATE_ALLERGEN",
                        path=f"{allergen_path}.allergen_name",
                        message=(
                            "Samma allergen och presence_type "
                            "finns redan på artikeln i "
                            f"allergens[{first_index}]."
                        ),
                    )
                )
            else:
                seen_allergens[
                    allergen_identity
                ] = allergen_index

            if allergen.verification_status != "verified":
                warnings.append(
                    _issue(
                        code="ALLERGEN_NOT_VERIFIED",
                        path=(
                            f"{allergen_path}."
                            "verification_status"
                        ),
                        message=(
                            "Allergeninformationen är inte "
                            "verifierad och får inte presenteras "
                            "som säker för kunden."
                        ),
                    )
                )

    return ValidateMenuImportResponse(
        valid=not errors,
        restaurant_id=payload.restaurant_id,
        provisioning_job_id=payload.provisioning_job_id,
        category_count=len(payload.categories),
        item_count=len(payload.items),
        alias_count=alias_count,
        option_group_count=option_group_count,
        option_count=option_count,
        ingredient_count=ingredient_count,
        allergen_count=allergen_count,
        warnings=warnings,
        errors=errors,
    )
