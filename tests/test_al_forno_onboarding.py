from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import UUID

from app.services.al_forno_onboarding import (
    AlFornoOnboardingError,
    build_al_forno_menu_import_request,
)
from app.services.menu_validator import validate_menu_import


RESTAURANT_ID = UUID("10000000-0000-0000-0000-000000000001")
JOB_ID = UUID("10000000-0000-0000-0000-000000000002")
IMPORT_ID = UUID("10000000-0000-0000-0000-000000000003")


class AlFornoOnboardingTests(unittest.TestCase):
    def _request(self):
        return build_al_forno_menu_import_request(
            restaurant_id=RESTAURANT_ID,
            provisioning_job_id=JOB_ID,
            idempotency_key=IMPORT_ID,
            allow_unverified_prices=True,
        )

    def test_requires_every_price_to_be_verified_by_default(self) -> None:
        with self.assertRaisesRegex(
            AlFornoOnboardingError,
            "Tropicana",
        ):
            build_al_forno_menu_import_request(
                restaurant_id=RESTAURANT_ID,
                provisioning_job_id=JOB_ID,
                idempotency_key=IMPORT_ID,
            )

    def test_builds_valid_generic_menu_import(self) -> None:
        request = self._request()
        result = validate_menu_import(request)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.category_count, 7)
        self.assertEqual(result.item_count, 113)
        self.assertEqual(result.option_group_count, 18)
        self.assertEqual(result.option_count, 53)
        self.assertEqual(result.alias_count, 206)

    def test_preserves_minor_unit_prices_exactly(self) -> None:
        request = self._request()
        items = {item.official_name: item for item in request.items}

        self.assertEqual(items["Margherita"].base_price, Decimal("125"))
        self.assertEqual(items["Pasta Romana"].base_price, Decimal("179"))

    def test_pan_pizza_defaults_to_medium(self) -> None:
        request = self._request()
        item = next(
            item
            for item in request.items
            if item.official_name == "Pan Pizza Rio"
        )
        size_group = item.option_groups[0]
        options = {option.name: option for option in size_group.options}

        self.assertTrue(size_group.is_required)
        self.assertEqual(options["Small"].price_delta, Decimal("0"))
        self.assertEqual(options["Medium"].price_delta, Decimal("20"))
        self.assertTrue(options["Medium"].is_default)
        self.assertEqual(options["Large"].price_delta, Decimal("100"))

    def test_required_side_choices_are_preserved(self) -> None:
        request = self._request()
        item = next(
            item
            for item in request.items
            if item.official_name == "Kebabtallrik"
        )
        side_group = item.option_groups[0]

        self.assertTrue(side_group.is_required)
        self.assertEqual(
            [option.name for option in side_group.options],
            ["Pommes", "Ris", "Bulgur"],
        )


if __name__ == "__main__":
    unittest.main()
