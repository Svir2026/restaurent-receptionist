from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID


os.environ.setdefault("RESTAURANT_TIMEZONE", "Europe/Stockholm")
os.environ.setdefault("ELEVENLABS_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault(
    "SVIR_INTERNAL_API_SECRET",
    "test-internal-secret-that-is-long-enough",
)
os.environ.setdefault("ELEVENLABS_API_KEY", "test-api-key")
os.environ.setdefault("ELEVENLABS_TEMPLATE_AGENT_ID", "test-agent")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    ResolveMenuItemsV2Request,
    ResolveMenuItemsV2Response,
)
from app.services.restaurant_menu_resolver import (
    YZ_MENU_RESOLVER_TOOL_NAME,
    _clear_resolver_catalog_cache,
    _load_menu_item_aliases,
    resolve_restaurant_menu_items,
)
from app.services import restaurant_menu_resolver as resolver_module
from app.services.elevenlabs_tool_definitions import (
    YZ_MENU_RESOLVER_V2_TOOL_NAME,
    YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
    build_yz_test_menu_resolver_v2_tool_config,
)


RESTAURANT_ID = UUID("11111111-1111-4111-8111-111111111111")
YAKINIKU_ID = UUID("22222222-2222-4222-8222-222222222222")
PAD_THAI_ID = UUID("33333333-3333-4333-8333-333333333333")
COLA_ID = UUID("44444444-4444-4444-8444-444444444444")
COLA_ZERO_ID = UUID("55555555-5555-4555-8555-555555555555")
SATAY_ID = UUID("88888888-8888-4888-8888-888888888888")
EXTRA_CASHEW_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASHEW_SUSHI_COMBO_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
REGULAR_SUSHI_15_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CUSTOM_SUSHI_15_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CASHEW_IDS = {
    protein: UUID(f"99999999-9999-4999-8999-{index:012d}")
    for index, protein in enumerate(
        ("Anka", "Biff", "Bläckfisk", "Fläsk", "Kyckling", "Räkor", "Tofu"),
        start=1,
    )
}


def _item(
    item_id: UUID,
    official_name: str,
    customer_name: str | None = None,
) -> dict[str, object]:
    return {
        "id": str(item_id),
        "restaurant_id": str(RESTAURANT_ID),
        "official_name": official_name,
        "customer_display_name": customer_name or official_name,
        "kitchen_display_name": official_name,
        "base_price": 100,
        "currency": "SEK",
        "is_active": True,
    }


def _alias(
    item_id: UUID,
    value: str,
) -> dict[str, object]:
    return {
        "id": "66666666-6666-4666-8666-666666666666",
        "restaurant_id": str(RESTAURANT_ID),
        "menu_item_id": str(item_id),
        "alias": value,
        "normalized_alias": value.casefold(),
        "alias_type": "spoken",
        "priority": 100,
    }


class RestaurantMenuResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_resolver_catalog_cache()
        self.context = ToolRestaurantContext(
            credential_id=UUID(
                "77777777-7777-4777-8777-777777777777"
            ),
            restaurant_id=RESTAURANT_ID,
            restaurant_name="YZ Thai Wok & Sushi",
            restaurant_slug="yz-thai-wok-sushi",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )
        self.menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(
                SATAY_ID,
                "23. Satay Gai",
                "Kycklingspett med jordnötssås",
            ),
            _item(
                PAD_THAI_ID,
                "Pad Thai - Kyckling",
                "Pad Thai med kyckling",
            ),
        ]
        self.aliases = [
            _alias(YAKINIKU_ID, "yakisoba"),
            _alias(YAKINIKU_ID, "yakinaki"),
        ]

    def tearDown(self) -> None:
        _clear_resolver_catalog_cache()

    @staticmethod
    def _request(
        utterance: str,
        prior_statuses: list[str] | None = None,
    ) -> ResolveMenuItemsV2Request:
        history: list[dict[str, object]] = []
        for status in prior_statuses or []:
            history.append(
                {
                    "role": "tool",
                    "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                    "result_value": json.dumps({"status": status}),
                }
            )
        history.append({"role": "user", "message": utterance})
        return ResolveMenuItemsV2Request(
            conversation_history=history
        )

    def _resolve(
        self,
        utterance: str,
        prior_statuses: list[str] | None = None,
        *,
        menu: list[dict[str, object]] | None = None,
        aliases: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=menu if menu is not None else self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=(
                aliases if aliases is not None else self.aliases
            ),
        ):
            return resolve_restaurant_menu_items(
                context=self.context,
                request=self._request(utterance, prior_statuses),
            )

    def _resolve_history(
        self,
        history: list[dict[str, object]],
        *,
        menu: list[dict[str, object]] | None = None,
        aliases: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=menu if menu is not None else self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=(
                aliases if aliases is not None else self.aliases
            ),
        ):
            return resolve_restaurant_menu_items(
                context=self.context,
                request=ResolveMenuItemsV2Request(
                    conversation_history=history
                ),
            )

    def test_valid_canonical_name_continues_without_fallback(self) -> None:
        result = self._resolve("Jag vill ha en Yakiniku.")
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["action"], "continue")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertEqual(result["matches"][0]["match_source"], "canonical")

    def test_valid_approved_alias_continues_without_fallback(self) -> None:
        result = self._resolve("Jag tar en yakisoba.")
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matches"][0]["official_name"], "24. Yakiniku")
        self.assertEqual(result["matches"][0]["match_source"], "alias")

    def test_unique_high_confidence_menu_fuzz_resolves_spoken_soup(self) -> None:
        tom_kha_id = UUID("00000000-0000-0000-0000-000000000006")
        menu = [
            *self.menu,
            _item(tom_kha_id, "5. Tom Kha Gai", "Tom Kha Gai"),
        ]
        result = self._resolve(
            "Jag vill ha tom ka gai",
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matches"][0]["official_name"], "5. Tom Kha Gai")
        self.assertEqual(result["matches"][0]["match_source"], "fuzzy")

    def test_menu_fuzz_rejects_close_tie_instead_of_guessing(self) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("00000000-0000-0000-0000-000000000007"),
                "Tom Kha Gai",
                "Tom Kha Gai",
            ),
            _item(
                UUID("00000000-0000-0000-0000-000000000008"),
                "Tom Ka Gai",
                "Tom Ka Gai",
            ),
        ]
        result = self._resolve(
            "Jag vill ha tom kha gay",
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["matches"], [])

    def test_yakinaki_is_an_approved_yakiniku_alias(self) -> None:
        result = self._resolve(
            "Jag tar en yakinaki",
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matches"][0]["official_name"], "24. Yakiniku")
        self.assertEqual(result["matches"][0]["match_source"], "alias")

    def test_yakiniki_is_an_approved_yakiniku_alias(self) -> None:
        result = self._resolve(
            "Yakiniki",
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matches"][0]["official_name"], "24. Yakiniku")
        self.assertEqual(result["matches"][0]["match_source"], "alias")

    def test_yakniki_is_an_approved_yakiniku_alias(self) -> None:
        result = self._resolve(
            "Jag tar en yakniki",
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matches"][0]["official_name"], "24. Yakiniku")
        self.assertEqual(result["matches"][0]["match_source"], "alias")

    def test_yakinaki_override_is_scoped_to_yz(self) -> None:
        other_context = ToolRestaurantContext(
            credential_id=self.context.credential_id,
            restaurant_id=self.context.restaurant_id,
            restaurant_name="Other Restaurant",
            restaurant_slug="other-restaurant",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=[],
        ):
            result = resolve_restaurant_menu_items(
                context=other_context,
                request=self._request("Jag tar en yakinaki"),
            )
        self.assertEqual(result["status"], "NO_MATCH")

    def test_yakniki_override_is_scoped_to_yz(self) -> None:
        other_context = ToolRestaurantContext(
            credential_id=self.context.credential_id,
            restaurant_id=self.context.restaurant_id,
            restaurant_name="Other Restaurant",
            restaurant_slug="other-restaurant",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=[],
        ):
            result = resolve_restaurant_menu_items(
                context=other_context,
                request=self._request("Jag tar en yakniki"),
            )
        self.assertEqual(result["status"], "NO_MATCH")

    def test_chicken_skewer_aliases_resolve_only_to_satay_gai(self) -> None:
        satay_menu = [
            *self.menu,
        ]
        satay_aliases = [
            *self.aliases,
        ]
        for utterance in (
            "Jag vill ha kycklingspett",
            "En kycklingspett med jordnötsås",
            "Jag tar en kycklingpsett",
            "Jag vill ha en kycklingpasett med jordnötssås",
        ):
            with self.subTest(utterance=utterance):
                result = self._resolve(
                    utterance,
                    menu=satay_menu,
                    aliases=satay_aliases,
                )
                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(
                    result["matches"][0]["official_name"],
                    "23. Satay Gai",
                )

    def test_unknown_item_uses_three_step_recovery(self) -> None:
        expected = [
            (
                [],
                1,
                "repeat",
                False,
                "Ursäkta, kan du upprepa vilken rätt du ville ha?",
            ),
            (
                ["NO_MATCH"],
                2,
                "not_on_menu",
                False,
                "Tyvärr, det finns inte på menyn. "
                "Testa gärna att beställa något annat.",
            ),
            (
                ["NO_MATCH", "NO_MATCH"],
                3,
                "technical_stop",
                True,
                "Det verkar vara ett tekniskt fel just nu. "
                "Kom gärna in i restaurangen och beställ.",
            ),
            (
                ["NO_MATCH", "NO_MATCH", "NO_MATCH"],
                3,
                "technical_stop",
                True,
                "Det verkar vara ett tekniskt fel just nu. "
                "Kom gärna in i restaurangen och beställ.",
            ),
        ]
        for prior, attempt, action, stopped, message in expected:
            with self.subTest(attempt=attempt):
                result = self._resolve("Jag vill ha en månpizza", prior)
                self.assertEqual(result["status"], "NO_MATCH")
                self.assertEqual(result["unresolved_attempt"], attempt)
                self.assertEqual(result["action"], action)
                self.assertEqual(result["stop_recovery"], stopped)
                self.assertEqual(result["customer_message"], message)
                self.assertEqual(result["matches"], [])

    def test_match_resets_previous_no_match_counter(self) -> None:
        result = self._resolve(
            "Jag vill ha något som inte finns",
            ["NO_MATCH", "NO_MATCH", "MATCH"],
        )
        self.assertEqual(result["unresolved_attempt"], 1)
        self.assertEqual(result["action"], "repeat")

    def test_valid_item_after_failures_resets_immediately(self) -> None:
        result = self._resolve(
            "Okej, då tar jag Pad Thai med kyckling",
            ["NO_MATCH", "NO_MATCH"],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertEqual(result["action"], "continue")

    def test_bare_pad_thai_asks_for_protein_without_fallback(self) -> None:
        result = self._resolve("Jag vill ha en Pad Thai")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertFalse(result["stop_recovery"])
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_bare_pad_thai_resets_previous_fallback_attempts(self) -> None:
        result = self._resolve(
            "Okej, då tar jag en Pad Thai",
            ["NO_MATCH", "NO_MATCH"],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertEqual(result["action"], "clarify")

    def test_menu_number_18_asks_for_pad_thai_protein(self) -> None:
        chicken = _item(
            PAD_THAI_ID,
            "Pad Thai - Kyckling",
            "Pad Thai med kyckling",
        )
        chicken["kitchen_display_name"] = "18. Pad Thai / Kyckling"
        beef = _item(
            COLA_ID,
            "Pad Thai - Biff",
            "Pad Thai med biff",
        )
        beef["kitchen_display_name"] = "18. Pad Thai / Biff"
        result = self._resolve(
            "Jag vill ha nummer arton",
            menu=[self.menu[0], self.menu[1], chicken, beef],
            aliases=[],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_menu_number_18_protein_follow_up_resolves_pad_thai(self) -> None:
        chicken = _item(
            PAD_THAI_ID,
            "Pad Thai - Kyckling",
            "Pad Thai med kyckling",
        )
        chicken["kitchen_display_name"] = "18. Pad Thai / Kyckling"
        beef = _item(
            COLA_ID,
            "Pad Thai - Biff",
            "Pad Thai med biff",
        )
        beef["kitchen_display_name"] = "18. Pad Thai / Biff"
        result = self._resolve_history(
            [
                {"role": "user", "message": "Jag vill ha nummer 18"},
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling"},
            ],
            menu=[self.menu[0], self.menu[1], chicken, beef],
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai - Kyckling",
        )

    def test_menu_number_15_protein_follow_up_resolves_specialwok(self) -> None:
        chicken = _item(
            PAD_THAI_ID,
            "Specialwok - grönsaker och ostronsås - Kyckling",
        )
        chicken["kitchen_display_name"] = (
            "15. Specialwok / Kyckling"
        )
        beef = _item(
            COLA_ID,
            "Specialwok - grönsaker och ostronsås - Biff",
        )
        beef["kitchen_display_name"] = "15. Specialwok / Biff"
        result = self._resolve_history(
            [
                {"role": "user", "message": "Jag vill ha rätt femton"},
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Biff"},
            ],
            menu=[self.menu[0], self.menu[1], chicken, beef],
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Specialwok - grönsaker och ostronsås - Biff",
        )

    def test_unique_menu_number_resolves_the_matching_item(self) -> None:
        satay = _item(
            SATAY_ID,
            "23. Satay Gai",
            "Kycklingspett med jordnötssås",
        )
        result = self._resolve(
            "Jag vill ha nummer 23",
            menu=[self.menu[0], satay, self.menu[2]],
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "23. Satay Gai",
        )

    def test_unique_menu_number_never_fuzzes_to_a_protein_family(self) -> None:
        soup = _item(
            UUID("00000000-0000-0000-0000-000000000005"),
            "5. Tom Kha Gai",
            "Tom Kha Gai",
        )
        result = self._resolve(
            "Jag vill ha nummer fem",
            menu=[self.menu[0], self.menu[1], soup, self.menu[2]],
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "5. Tom Kha Gai",
        )

    def test_menu_number_32_asks_for_spring_roll_size(self) -> None:
        six_rolls = _item(
            UUID("00000000-0000-0000-0000-000000000032"),
            "32. Vårrullar - 6 stycken",
        )
        twelve_rolls = _item(
            UUID("00000000-0000-0000-0000-000000000033"),
            "32. Vårrullar - 12 stycken",
        )
        result = self._resolve(
            "Jag vill ha nummer 32",
            menu=[self.menu[0], self.menu[1], six_rolls, twelve_rolls],
            aliases=[],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vill du ha sex eller tolv vårrullar?",
        )
        self.assertEqual(result["matches"], [])

    def test_bare_red_curry_asks_for_protein(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Gaeng Ped – Kyckling",
                "Gaeng Ped – Kyckling",
            ),
            _item(
                COLA_ID,
                "Gaeng Ped – Räkor",
                "Gaeng Ped – Räkor",
            ),
        ]
        result = self._resolve("Jag vill ha en röd curry", menu=menu)
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_red_curry_with_protein_maps_to_gaeng_ped(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Gaeng Ped – Kyckling",
                "Gaeng Ped – Kyckling",
            ),
            _item(
                COLA_ID,
                "Gaeng Ped – Räkor",
                "Gaeng Ped – Räkor",
            ),
        ]
        result = self._resolve(
            "Jag vill ha en röd curry med kyckling",
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Gaeng Ped – Kyckling",
        )

    def test_recent_real_variant_families_ask_for_protein(self) -> None:
        cases = (
            ("En Gaeng Panang", "Gaeng Panang"),
            ("En Gaeng Keowan", "Gaeng Keowan"),
            ("En grön curry", "Gaeng Keowan"),
            ("En Massamang Curry", "Massamang Curry"),
            ("En Massaman Curry", "Massamang Curry"),
            ("En Pad Krapow", "Pad Krapow"),
            ("En Pad Priawan", "Pad Priawan"),
            ("En pad privan", "Pad Priawan"),
        )
        for index, (utterance, family) in enumerate(cases, start=1):
            menu = [
                *self.menu,
                _item(
                    UUID(f"10000000-0000-4000-8000-{index:012d}"),
                    f"{family} – Kyckling",
                ),
                _item(
                    UUID(f"20000000-0000-4000-8000-{index:012d}"),
                    f"{family} – Fläsk",
                ),
            ]
            with self.subTest(utterance=utterance):
                _clear_resolver_catalog_cache()
                result = self._resolve(
                    utterance,
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(
                    result["status"],
                    "AMBIGUOUS",
                    msg=utterance,
                )
                self.assertEqual(result["action"], "clarify")
                self.assertEqual(
                    result["customer_message"],
                    "Vilket protein vill du ha?",
                )
                self.assertEqual(result["unresolved_attempt"], 0)

    def test_approved_family_aliases_ask_for_protein(self) -> None:
        alias_groups = {
            "Gaeng Ped": (
                "röd curry",
                "rad curry",
                "red curry",
            ),
            "Gaeng Panang": (
                "panang",
                "panang curry",
                "penang",
                "nummer 3",
            ),
            "Gaeng Keowan": (
                "grön curry",
                "gron curry",
                "gran curry",
                "grand curry",
                "grann curry",
                "gren curry",
                "green curry",
                "gäng keowan",
                "keowan",
                "nummer 2",
            ),
            "Massamang Curry": (
                "massaman",
                "massamang",
                "massaman curry",
                "matsaman",
                "nummer 4",
            ),
            "Pad Krapow": (
                "krapow",
                "kra pow",
                "pad kaprao",
                "basilika stark",
                "nummer 9",
            ),
            "Pad Priawan": (
                "pad privan",
                "priawan",
                "priewan",
                "sötsur wok",
                "sotsur wok",
                "sweet and sour",
                "nummer 11",
            ),
        }
        for family_index, (family, aliases) in enumerate(
            alias_groups.items(),
            start=1,
        ):
            menu = [
                *self.menu,
                _item(
                    UUID(
                        "50000000-0000-4000-8000-"
                        f"{family_index:012d}"
                    ),
                    f"{family} – Kyckling",
                ),
                _item(
                    UUID(
                        "60000000-0000-4000-8000-"
                        f"{family_index:012d}"
                    ),
                    f"{family} – Fläsk",
                ),
            ]
            for alias in aliases:
                with self.subTest(alias=alias):
                    _clear_resolver_catalog_cache()
                    result = self._resolve(
                        f"Jag vill ha {alias}",
                        menu=menu,
                        aliases=[],
                    )
                    self.assertEqual(result["status"], "AMBIGUOUS")
                    self.assertEqual(result["action"], "clarify")
                    self.assertEqual(
                        result["customer_message"],
                        "Vilket protein vill du ha?",
                    )
                    self.assertEqual(result["unresolved_attempt"], 0)

    def test_live_asr_curry_aliases_resolve_with_every_protein(
        self,
    ) -> None:
        proteins = (
            "Anka",
            "Biff",
            "Bläckfisk",
            "Fläsk",
            "Kyckling",
            "Räkor",
            "Tofu",
        )
        for index, protein in enumerate(proteins, start=1):
            menu = [
                *self.menu,
                _item(
                    UUID(f"71000000-0000-4000-8000-{index:012d}"),
                    f"Gaeng Ped – {protein}",
                ),
                _item(
                    UUID(f"72000000-0000-4000-8000-{index:012d}"),
                    f"Gaeng Keowan – {protein}",
                ),
            ]
            with self.subTest(protein=protein):
                _clear_resolver_catalog_cache()
                result = self._resolve_history(
                    [
                        {
                            "role": "user",
                            "message": (
                                "Jag vill beställa en rad curry och "
                                "en grand curry."
                            ),
                        },
                        {
                            "role": "agent",
                            "message": "Vilket protein vill du ha?",
                        },
                        {
                            "role": "user",
                            "message": f"{protein} på båda.",
                        },
                    ],
                    menu=menu,
                    aliases=[],
                )

                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(result["unresolved_attempt"], 0)
                self.assertEqual(
                    {match["official_name"] for match in result["matches"]},
                    {
                        f"Gaeng Ped – {protein}",
                        f"Gaeng Keowan – {protein}",
                    },
                )

    def test_live_asr_hesitation_inside_green_curry_is_resolved(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("73000000-0000-4000-8000-000000000001"),
                "Gaeng Keowan – Kyckling",
            ),
        ]
        result = self._resolve(
            "Nej, jag vill beställa en gran, eh, curry.",
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_fuzzy_green_curry_with_protein_resolves_only_to_green_curry(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("74000000-0000-4000-8000-000000000001"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("74000000-0000-4000-8000-000000000002"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve(
            "Jag vill ha gran curri med kyckling",
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Gaeng Keowan – Kyckling",
        )

    def test_overlapping_family_aliases_do_not_duplicate_matches(
        self,
    ) -> None:
        cases = (
            ("panang curry med kyckling", "Gaeng Panang"),
            ("massaman curry med kyckling", "Massamang Curry"),
            ("pad krapow med kyckling", "Pad Krapow"),
        )
        for index, (utterance, family) in enumerate(cases, start=1):
            menu = [
                *self.menu,
                _item(
                    UUID(f"70000000-0000-4000-8000-{index:012d}"),
                    f"{family} – Kyckling",
                ),
            ]
            with self.subTest(utterance=utterance):
                _clear_resolver_catalog_cache()
                result = self._resolve(
                    utterance,
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(
                    result["matches"][0]["official_name"],
                    f"{family} – Kyckling",
                )

    def test_gris_maps_exactly_to_flask_variant(self) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("30000000-0000-4000-8000-000000000001"),
                "Gaeng Ped – Fläsk",
            ),
        ]
        result = self._resolve(
            "Jag vill ha en röd curry med gris",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Gaeng Ped – Fläsk",
        )

    def test_recent_real_families_resolve_after_protein_follow_up(
        self,
    ) -> None:
        cases = (
            ("Gaeng Panang", "Gaeng Panang", "kyckling", "Kyckling"),
            ("grön curry", "Gaeng Keowan", "biff", "Biff"),
            ("Massamang Curry", "Massamang Curry", "gris", "Fläsk"),
            ("Pad Krapow", "Pad Krapow", "tofu", "Tofu"),
            ("pad privan", "Pad Priawan", "räkor", "Räkor"),
        )
        for index, (
            spoken_family,
            menu_family,
            spoken_protein,
            menu_protein,
        ) in enumerate(cases, start=1):
            menu = [
                *self.menu,
                _item(
                    UUID(f"40000000-0000-4000-8000-{index:012d}"),
                    f"{menu_family} – {menu_protein}",
                ),
            ]
            with self.subTest(spoken_family=spoken_family):
                _clear_resolver_catalog_cache()
                result = self._resolve_history(
                    [
                        {
                            "role": "user",
                            "message": f"En {spoken_family}",
                        },
                        {
                            "role": "agent",
                            "message": "Vilket protein vill du ha?",
                        },
                        {"role": "user", "message": spoken_protein},
                    ],
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(result["action"], "continue")
                self.assertEqual(result["unresolved_attempt"], 0)
                self.assertEqual(
                    result["matches"][0]["official_name"],
                    f"{menu_family} – {menu_protein}",
                )

    def test_one_protein_follow_up_resolves_every_pending_family(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000001"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000002"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En grön curry och en röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "kycklingpapadah."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan – Kyckling", "Gaeng Ped – Kyckling"},
        )

    def test_repeated_protein_question_keeps_original_order_context(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000003"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000004"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En grön curry och en röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "oklart protein"},
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan – Kyckling", "Gaeng Ped – Kyckling"},
        )

    def test_real_call_tool_history_keeps_every_pending_family(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000013"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000014"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        ambiguous_result = {
            "role": "agent",
            "tool_results": [
                {
                    "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                    "result_value": json.dumps(
                        {"status": "AMBIGUOUS"}
                    ),
                }
            ],
        }
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": (
                        "Jag vill beställa en grön curry och en röd curry."
                    ),
                },
                {
                    "role": "agent",
                    "tool_calls": [
                        {"tool_name": YZ_MENU_RESOLVER_TOOL_NAME}
                    ],
                },
                ambiguous_result,
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "kycklingpapadah"},
                {
                    "role": "agent",
                    "tool_calls": [
                        {"tool_name": YZ_MENU_RESOLVER_TOOL_NAME}
                    ],
                },
                ambiguous_result,
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan – Kyckling", "Gaeng Ped – Kyckling"},
        )

    def test_ambiguous_tool_result_keeps_order_after_generic_retry(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000015"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000016"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En grön curry och en röd curry.",
                },
                {
                    "role": "agent",
                    "tool_calls": [
                        {"tool_name": YZ_MENU_RESOLVER_TOOL_NAME}
                    ],
                },
                {
                    "role": "agent",
                    "tool_results": [
                        {
                            "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                            "result_value": json.dumps(
                                {"status": "AMBIGUOUS"}
                            ),
                        }
                    ],
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "oklart protein"},
                {
                    "role": "agent",
                    "message": "Ursäkta, kan du repetera?",
                },
                {"role": "user", "message": "Kyckling."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["unresolved_attempt"], 0)
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan – Kyckling", "Gaeng Ped – Kyckling"},
        )

    def test_completed_match_blocks_stale_ambiguous_variant_context(
        self,
    ) -> None:
        result = self._resolve_history(
            [
                {"role": "user", "message": "En Pad Thai."},
                {
                    "role": "agent",
                    "tool_results": [
                        {
                            "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                            "result_value": json.dumps(
                                {"status": "AMBIGUOUS"}
                            ),
                        }
                    ],
                },
                {"role": "user", "message": "Kyckling."},
                {
                    "role": "agent",
                    "tool_results": [
                        {
                            "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                            "result_value": json.dumps(
                                {"status": "MATCH"}
                            ),
                        }
                    ],
                },
                {"role": "agent", "message": "Något annat."},
                {"role": "user", "message": "helt okänd rätt"},
            ],
            aliases=[],
        )

        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["matches"], [])

    def test_sequential_protein_answers_keep_every_pending_family(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000005"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000006"),
                "Gaeng Keowan – Biff",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000007"),
                "Gaeng Ped – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000008"),
                "Gaeng Ped – Biff",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En grön curry och en röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {
                    "role": "user",
                    "message": "Grön curry med kyckling.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Biff."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan – Kyckling", "Gaeng Ped – Biff"},
        )

    def test_pending_families_preserve_already_matched_dishes(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000009"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000010"),
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": (
                        "En Yakiniku, en grön curry och en röd curry."
                    ),
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling på båda."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {
                "24. Yakiniku",
                "Gaeng Keowan – Kyckling",
                "Gaeng Ped – Kyckling",
            },
        )

    def test_conflicting_generic_proteins_do_not_guess_or_fallback(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("31000000-0000-4000-8000-000000000011"),
                "Gaeng Keowan – Kyckling",
            ),
            _item(
                UUID("31000000-0000-4000-8000-000000000012"),
                "Gaeng Ped – Biff",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En grön curry och en röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling och biff."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unresolved_attempt"], 0)

    def test_one_protein_can_resolve_every_approved_family(self) -> None:
        families = (
            ("pad thai", "Pad Thai"),
            ("röd curry", "Gaeng Ped"),
            ("grön curry", "Gaeng Keowan"),
            ("panang", "Gaeng Panang"),
            ("massaman", "Massamang Curry"),
            ("pad krapow", "Pad Krapow"),
            ("pad privan", "Pad Priawan"),
            ("cashewnötter", "Pad Med Mamuang"),
        )
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(
                SATAY_ID,
                "23. Satay Gai",
                "Kycklingspett med jordnötssås",
            ),
            *(
                _item(
                    UUID(
                        "32000000-0000-4000-8000-"
                        f"{index:012d}"
                    ),
                    f"{menu_family} – Kyckling",
                )
                for index, (_, menu_family) in enumerate(
                    families,
                    start=1,
                )
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "Jag vill ha "
                    + ", ".join(family for family, _ in families),
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling på alla."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {
                f"{menu_family} – Kyckling"
                for _, menu_family in families
            },
        )

    def test_gris_question_resolves_pending_red_curry_and_keeps_side(
        self,
    ) -> None:
        spring_roll_id = UUID("30000000-0000-4000-8000-000000000002")
        menu = [
            *self.menu,
            _item(
                UUID("30000000-0000-4000-8000-000000000003"),
                "Gaeng Ped – Fläsk",
            ),
            _item(spring_roll_id, "Vårrullar"),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En röd curry med vårrullar.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Har ni gris?"},
            ],
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Ped – Fläsk", "Vårrullar"},
        )

    def test_yellow_curry_is_not_mapped(self) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("30000000-0000-4000-8000-000000000004"),
                "Massamang Curry – Kyckling",
            ),
        ]
        result = self._resolve(
            "Jag vill ha en gul curry",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "NO_MATCH")

    def test_cashew_family_resolves_every_verified_protein(self) -> None:
        menu = [
            *self.menu,
            *(
                _item(
                    item_id,
                    f"Pad Med Mamuang – {protein}",
                    f"Pad Med Mamuang – {protein}",
                )
                for protein, item_id in CASHEW_IDS.items()
            ),
        ]
        phrases = {
            "Kyckling": "Jag vill ha kyckling med cashewnötter",
            "Biff": "Jag tar biff med cashewnötter",
            "Fläsk": "En fläsk med cashewnötter",
            "Tofu": "Jag vill ha tofu med cashewnötter",
            "Räkor": "Jag tar räkor med cashewnötter",
            "Bläckfisk": "En bläckfisk med cashewnötter",
            "Anka": "Jag vill ha anka med cashewnötter",
        }

        for protein, utterance in phrases.items():
            with self.subTest(protein=protein):
                result = self._resolve(
                    utterance,
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(result["action"], "continue")
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(
                    result["matches"][0]["official_name"],
                    f"Pad Med Mamuang – {protein}",
                )
                self.assertEqual(
                    result["customer_message"],
                    "Okej perfekt, "
                    f"{protein.casefold()} med cashewnötter. "
                    "Har jag fått med allting?",
                )

    def test_cashew_family_accepts_common_word_orders(self) -> None:
        menu = [
            *self.menu,
            _item(
                CASHEW_IDS["Kyckling"],
                "Pad Med Mamuang – Kyckling",
            )
        ]
        phrases = (
            "kyckling cashewnötter",
            "kyckling med cashewnötter",
            "cashewnötter kyckling",
            "cashewnötter med kyckling",
            "kyckling cashew",
            "cashew kyckling",
        )

        for utterance in phrases:
            with self.subTest(utterance=utterance):
                result = self._resolve(
                    utterance,
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(result["status"], "MATCH")
                self.assertEqual(
                    result["matches"][0]["official_name"],
                    "Pad Med Mamuang – Kyckling",
                )

    def test_bare_cashew_family_asks_for_protein(self) -> None:
        menu = [
            *self.menu,
            _item(
                CASHEW_IDS["Kyckling"],
                "Pad Med Mamuang – Kyckling",
            ),
            _item(
                CASHEW_IDS["Biff"],
                "Pad Med Mamuang – Biff",
            ),
        ]
        result = self._resolve(
            "Jag vill ha cashewnötter",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_extra_cashews_resolves_without_protein_question(self) -> None:
        menu = [
            *self.menu,
            _item(EXTRA_CASHEW_ID, "Extra cashewnötter"),
            _item(
                CASHEW_IDS["Kyckling"],
                "Pad Med Mamuang – Kyckling",
            ),
        ]
        result = self._resolve(
            "Jag vill ha extra cashewnötter",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["action"], "continue")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Extra cashewnötter",
        )

    def test_cashew_sushi_combo_does_not_add_wok_variant(self) -> None:
        menu = [
            *self.menu,
            _item(
                CASHEW_SUSHI_COMBO_ID,
                "Kyckling Cashew med 5 sushi-bitar",
            ),
            _item(
                CASHEW_IDS["Kyckling"],
                "Pad Med Mamuang – Kyckling",
            ),
        ]
        result = self._resolve(
            "Jag vill ha Kyckling Cashew med 5 sushi-bitar",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Kyckling Cashew med 5 sushi-bitar",
        )

    def test_spoken_fem_matches_verified_menu_digit_five(self) -> None:
        menu = [
            *self.menu,
            _item(
                CASHEW_SUSHI_COMBO_ID,
                "Kyckling Cashew med 5 sushi-bitar",
            ),
            _item(
                CASHEW_IDS["Kyckling"],
                "Pad Med Mamuang – Kyckling",
            ),
        ]
        result = self._resolve(
            "Jag vill ha Kyckling Cashew med fem sushi-bitar",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Kyckling Cashew med 5 sushi-bitar",
        )

    def test_bare_fifteen_piece_sushi_asks_regular_or_custom(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "Jag vill ha en femton bitars sushi",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(
            result["customer_message"],
            "Vill du ha vanlig femtonbitars sushi eller blanda egen?",
        )
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {
                "Extra Stor Sushi – 15 bitar",
                "Egenkomponerad sushi – 15 bitar",
            },
        )

    def test_compound_fifteen_piece_sushi_is_understood(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "Jag vill ha en femtonbitars sushi",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vill du ha vanlig femtonbitars sushi eller blanda egen?",
        )

    def test_every_supported_sushi_size_uses_exact_active_items(self) -> None:
        regular_names = {
            8: "Liten Sushi – 8 bitar",
            10: "Mellan Sushi – 10 bitar",
            12: "Stor Sushi – 12 bitar",
            15: "Extra Stor Sushi – 15 bitar",
            20: "Super Sushi – 20 bitar",
            30: "Familje Sushi – 30 bitar",
            50: "Stor Familje Sushi – 50 bitar",
        }
        spoken_sizes = {
            8: "åtta",
            10: "tio",
            12: "tolv",
            15: "femton",
            20: "tjugo",
            30: "trettio",
            50: "femtio",
        }
        menu = [*self.menu]
        for index, (size, regular_name) in enumerate(
            regular_names.items(),
            start=100,
        ):
            menu.extend(
                [
                    _item(UUID(int=index), regular_name),
                    _item(
                        UUID(int=index + 100),
                        f"Egenkomponerad sushi – {size} bitar",
                    ),
                ]
            )

        for size, spoken_size in spoken_sizes.items():
            with self.subTest(size=size):
                result = self._resolve(
                    f"Jag vill ha {spoken_size} bitars sushi",
                    menu=menu,
                    aliases=[],
                )
                self.assertEqual(result["status"], "AMBIGUOUS")
                self.assertEqual(
                    {match["official_name"] for match in result["matches"]},
                    {
                        regular_names[size],
                        f"Egenkomponerad sushi – {size} bitar",
                    },
                )

    def test_generic_sushi_asks_only_for_piece_count(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "Jag vill ha sushi",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Hur många bitar sushi vill du ha?",
        )

    def test_sushi_size_follow_up_asks_regular_or_custom(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve_history(
            [
                {"role": "user", "message": "Jag vill ha sushi"},
                {
                    "role": "agent",
                    "message": "Hur många bitar sushi vill du ha?",
                },
                {"role": "user", "message": "Femton bitar"},
            ],
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vill du ha vanlig femtonbitars sushi eller blanda egen?",
        )

    def test_regular_sushi_follow_up_selects_exact_regular_item(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "Jag vill ha femton bitars sushi",
                },
                {
                    "role": "agent",
                    "message": (
                        "Vill du ha vanlig femtonbitars sushi "
                        "eller blanda egen?"
                    ),
                },
                {"role": "user", "message": "Vanlig"},
            ],
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Extra Stor Sushi – 15 bitar",
        )

    def test_custom_sushi_follow_up_preserves_custom_notes_flow(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "Jag vill ha femton bitars sushi",
                },
                {
                    "role": "agent",
                    "message": (
                        "Vill du ha vanlig femtonbitars sushi "
                        "eller blanda egen?"
                    ),
                },
                {"role": "user", "message": "Blanda egen"},
            ],
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Egenkomponerad sushi – 15 bitar",
        )
        self.assertIsNone(result["customer_message"])

    def test_pad_thai_and_sushi_does_not_silently_drop_sushi(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "En Pad Thai med kyckling och femton bitars sushi",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {
                "Pad Thai – Kyckling",
                "Extra Stor Sushi – 15 bitar",
                "Egenkomponerad sushi – 15 bitar",
            },
        )

    def test_bare_pad_thai_is_clarified_before_sushi_type(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "En Pad Thai och femton bitars sushi",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_pad_thai_follow_up_then_clarifies_sushi_type(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "En Pad Thai och femton bitars sushi",
                },
                {"role": "agent", "message": "Vilket protein vill du ha?"},
                {"role": "user", "message": "Äh, kyckling."},
            ],
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vill du ha vanlig femtonbitars sushi eller blanda egen?",
        )
        self.assertIn(
            "Pad Thai – Kyckling",
            {match["official_name"] for match in result["matches"]},
        )

    def test_exact_regular_sushi_name_keeps_existing_match_path(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "Jag vill ha Extra Stor Sushi 15 bitar",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Extra Stor Sushi – 15 bitar",
        )

    def test_exact_custom_sushi_does_not_finish_before_notes(self) -> None:
        menu = [
            *self.menu,
            _item(REGULAR_SUSHI_15_ID, "Extra Stor Sushi – 15 bitar"),
            _item(
                CUSTOM_SUSHI_15_ID,
                "Egenkomponerad sushi – 15 bitar",
            ),
        ]
        result = self._resolve(
            "Jag vill ha Egenkomponerad sushi 15 bitar",
            menu=menu,
            aliases=[],
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Egenkomponerad sushi – 15 bitar",
        )
        self.assertIsNone(result["customer_message"])

    def test_explicit_pad_thai_protein_continues_normally(self) -> None:
        result = self._resolve("En Pad Thai med kyckling")
        validated = ResolveMenuItemsV2Response.model_validate(result)
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["action"], "continue")
        self.assertEqual(
            validated.required_agent_action,
            "say_customer_message_exactly",
        )
        self.assertTrue(validated.all_required_variants_resolved)
        self.assertEqual(
            result["customer_message"],
            "Okej perfekt, en Pad Thai med kyckling. "
            "Har jag fått med allting?",
        )

    def test_real_menu_dash_variant_matches_spoken_protein(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
        ]
        result = self._resolve(
            "En Pad Thai med kyckling",
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["action"], "continue")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai – Kyckling",
        )

    def test_extra_protein_modifier_does_not_replace_base_variant(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
            _item(
                COLA_ID,
                "Pad Thai – Räkor",
                "Pad Thai – Räkor",
            ),
        ]
        result = self._resolve(
            "En Pad Thai med kyckling och extra räkor",
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai – Kyckling",
        )

    def test_pad_thai_protein_follow_up_keeps_extra_shrimp_as_modifier(self) -> None:
        menu = [
            *self.menu,
            _item(
                COLA_ID,
                "Pad Thai – Räkor",
                "Pad Thai – Räkor",
            ),
        ]
        result = self._resolve_history(
            [
                {"role": "user", "message": "Jag vill ha en Pad Thai"},
                {"role": "agent", "message": "Vilket protein vill du ha?"},
                {
                    "role": "user",
                    "message": "Kyckling, och lägg till räkor också",
                },
            ],
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai - Kyckling",
        )
        self.assertEqual(
            result["customer_message"],
            "Okej perfekt, en Pad Thai med kyckling och extra räkor. "
            "Har jag fått med allting?",
        )

    def test_padthai_without_space_still_requires_protein(self) -> None:
        result = self._resolve("Jag vill ha en padthai")

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_padthai_without_space_keeps_chicken_as_primary_protein(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                COLA_ID,
                "Pad Thai - Räkor",
                "Pad Thai med räkor",
            ),
        ]
        result = self._resolve(
            "Hej jag vill ha en padthai med kyckling och räkor",
            menu=menu,
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai - Kyckling",
        )
        self.assertEqual(
            result["customer_message"],
            "Okej perfekt, en Pad Thai med kyckling och extra räkor. "
            "Har jag fått med allting?",
        )

    def test_red_curry_protein_follow_up_resolves_pending_family(self) -> None:
        menu = [
            *self.menu,
            _item(
                COLA_ID,
                "Gaeng Ped – Kyckling",
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {"role": "user", "message": "Jag vill ha en röd curry"},
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha till din röda curry?",
                },
                {"role": "user", "message": "Äh, kyckling."},
            ],
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Gaeng Ped – Kyckling",
        )
        self.assertEqual(
            result["customer_message"],
            "Okej perfekt, en röd curry med kyckling. "
            "Har jag fått med allting?",
        )

    def test_referenced_two_curry_proteins_resolve_to_correct_dishes(
        self,
    ) -> None:
        green_curry_chicken_id = UUID(
            "00000000-0000-0000-0000-000000000007"
        )
        red_curry_beef_id = UUID(
            "00000000-0000-0000-0000-000000000008"
        )
        menu = [
            *self.menu,
            _item(
                green_curry_chicken_id,
                "Gaeng Keowan - Kyckling",
                "Gaeng Keowan - Kyckling",
            ),
            _item(
                red_curry_beef_id,
                "Gaeng Ped - Biff",
                "Gaeng Ped - Biff",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "Jag vill beställa en grön och röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {
                    "role": "user",
                    "message": (
                        "Äh, på den gröna kringeln vill jag ha kyckling "
                        "och på den röda kringeln vill jag ha biff."
                    ),
                },
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertTrue(result["all_required_variants_resolved"])
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"Gaeng Keowan - Kyckling", "Gaeng Ped - Biff"},
        )

    def test_two_unreferenced_proteins_do_not_guess_curry_assignment(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                UUID("00000000-0000-0000-0000-000000000007"),
                "Gaeng Keowan - Kyckling",
            ),
            _item(
                UUID("00000000-0000-0000-0000-000000000008"),
                "Gaeng Ped - Biff",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": "Jag vill beställa en grön och röd curry.",
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha?",
                },
                {"role": "user", "message": "Kyckling och biff."},
            ],
            menu=menu,
            aliases=[],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_mixed_order_keeps_matches_while_red_curry_needs_protein(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                COLA_ID,
                "Pad Med Mamuang – Kyckling",
                "Pad Med Mamuang – Kyckling",
            ),
            _item(
                UUID("00000000-0000-0000-0000-000000000007"),
                "Gaeng Ped – Kyckling",
                "Gaeng Ped – Kyckling",
            ),
            _item(
                UUID("00000000-0000-0000-0000-000000000008"),
                "Gaeng Ped – Räkor",
                "Gaeng Ped – Räkor",
            ),
        ]
        result = self._resolve(
            "En yakniki, kyckling cashewnötter och en röd curry",
            menu=menu,
            aliases=[_alias(COLA_ID, "kyckling cashewnötter")],
        )

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["customer_message"], "Vilket protein vill du ha?")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {"24. Yakiniku", "Pad Med Mamuang – Kyckling"},
        )

    def test_mixed_order_protein_follow_up_returns_every_resolved_item(
        self,
    ) -> None:
        menu = [
            *self.menu,
            _item(
                COLA_ID,
                "Pad Med Mamuang – Kyckling",
                "Pad Med Mamuang – Kyckling",
            ),
            _item(
                UUID("00000000-0000-0000-0000-000000000007"),
                "Gaeng Ped – Kyckling",
                "Gaeng Ped – Kyckling",
            ),
        ]
        result = self._resolve_history(
            [
                {
                    "role": "user",
                    "message": (
                        "En yakniki, kyckling cashewnötter och en röd curry"
                    ),
                },
                {
                    "role": "agent",
                    "message": "Vilket protein vill du ha i din röda curry?",
                },
                {"role": "user", "message": "Kyckling"},
            ],
            menu=menu,
            aliases=[_alias(COLA_ID, "kyckling cashewnötter")],
        )

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            {match["official_name"] for match in result["matches"]},
            {
                "24. Yakiniku",
                "Pad Med Mamuang – Kyckling",
                "Gaeng Ped – Kyckling",
            },
        )

    def test_spoken_chicken_and_shrimp_keeps_chicken_variant(self) -> None:
        menu = [
            _item(YAKINIKU_ID, "24. Yakiniku", "Yakiniku"),
            _item(SATAY_ID, "23. Satay Gai"),
            _item(
                PAD_THAI_ID,
                "Pad Thai – Kyckling",
                "Pad Thai – Kyckling",
            ),
            _item(
                COLA_ID,
                "Pad Thai – Räkor",
                "Pad Thai – Räkor",
            ),
        ]
        result = self._resolve(
            "Hej, jag vill ha en Pad Thai med kyckling och räkor",
            menu=menu,
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["official_name"],
            "Pad Thai – Kyckling",
        )

    def test_large_order_with_bare_pad_thai_requests_protein(self) -> None:
        result = self._resolve("En Pad Thai och en Yakiniku")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["action"], "clarify")
        self.assertEqual(
            result["customer_message"],
            "Vilket protein vill du ha?",
        )

    def test_catalog_is_reused_during_one_conversation_window(self) -> None:
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ) as load_menu, patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=self.aliases,
        ) as load_aliases:
            first = resolve_restaurant_menu_items(
                context=self.context,
                request=self._request("En Yakiniku"),
            )
            second = resolve_restaurant_menu_items(
                context=self.context,
                request=self._request("En Pad Thai med kyckling"),
            )

        self.assertEqual(first["status"], "MATCH")
        self.assertEqual(second["status"], "MATCH")
        load_menu.assert_called_once()
        load_aliases.assert_called_once()

    def test_concurrent_cold_requests_share_one_catalog_load(self) -> None:
        workers = 8
        barrier = Barrier(workers)

        def load_menu(_restaurant_id: UUID):
            sleep(0.05)
            return self.menu

        def resolve_once(_index: int) -> str:
            barrier.wait()
            result = resolve_restaurant_menu_items(
                context=self.context,
                request=self._request("En Yakiniku"),
            )
            return str(result["status"])

        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            side_effect=load_menu,
        ) as load_menu_mock, patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=self.aliases,
        ) as load_aliases:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                statuses = list(executor.map(resolve_once, range(workers)))

        self.assertEqual(statuses, ["MATCH"] * workers)
        load_menu_mock.assert_called_once()
        load_aliases.assert_called_once()

    def test_phrase_index_is_reused_during_cache_window(self) -> None:
        original_build_phrases = resolver_module._build_phrases
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=self.aliases,
        ), patch(
            "app.services.restaurant_menu_resolver._build_phrases",
            wraps=original_build_phrases,
        ) as build_phrases:
            resolve_restaurant_menu_items(
                context=self.context,
                request=self._request("En Yakiniku"),
            )
            resolve_restaurant_menu_items(
                context=self.context,
                request=self._request("En Pad Thai med kyckling"),
            )

        build_phrases.assert_called_once()

    def test_alias_loader_reads_every_page(self) -> None:
        active_item_id = str(YAKINIKU_ID)
        rows = [
            {
                "id": f"alias-{index:04d}",
                "restaurant_id": str(RESTAURANT_ID),
                "menu_item_id": active_item_id,
                "alias": f"alias {index}",
                "normalized_alias": f"alias {index}",
                "alias_type": "spoken",
                "priority": 100,
            }
            for index in range(1002)
        ]

        class FakeAliasQuery:
            def __init__(self) -> None:
                self.requested_ranges: list[tuple[int, int]] = []
                self.current_range = (0, 999)

            def select(self, *_args: object) -> FakeAliasQuery:
                return self

            def eq(self, *_args: object) -> FakeAliasQuery:
                return self

            def order(self, *_args: object) -> FakeAliasQuery:
                return self

            def range(self, start: int, end: int) -> FakeAliasQuery:
                self.current_range = (start, end)
                self.requested_ranges.append(self.current_range)
                return self

            def execute(self) -> SimpleNamespace:
                start, end = self.current_range
                return SimpleNamespace(data=rows[start : end + 1])

        query = FakeAliasQuery()
        client = SimpleNamespace(table=lambda _name: query)
        with patch(
            "app.services.restaurant_menu_resolver.get_client",
            return_value=client,
        ):
            aliases = _load_menu_item_aliases(
                RESTAURANT_ID,
                {active_item_id},
            )

        self.assertEqual(len(aliases), 1002)
        self.assertEqual(
            query.requested_ranges,
            [(0, 999), (1000, 1999)],
        )

    def test_unknown_similar_word_is_not_fuzzy_matched(self) -> None:
        result = self._resolve("Jag tar en yakunaka")
        self.assertEqual(result["status"], "NO_MATCH")

    def test_longest_approved_phrase_wins(self) -> None:
        menu = [
            *self.menu,
            _item(COLA_ID, "Coca Cola"),
            _item(COLA_ZERO_ID, "Coca Cola Zero"),
        ]
        aliases = [
            _alias(COLA_ID, "cola"),
            _alias(COLA_ZERO_ID, "cola zero"),
        ]
        result = self._resolve(
            "En cola zero tack",
            menu=menu,
            aliases=aliases,
        )
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["menu_item_id"],
            COLA_ZERO_ID,
        )

    def test_multiple_valid_items_are_resolved_in_speech_order(self) -> None:
        result = self._resolve(
            "En Yakiniku och en Pad Thai med kyckling"
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            [match["menu_item_id"] for match in result["matches"]],
            [YAKINIKU_ID, PAD_THAI_ID],
        )

    def test_real_call_pad_thai_and_yakiniki_are_both_resolved(self) -> None:
        result = self._resolve(
            "Jag vill beställa en Pad Thai med kyckling och en Yakiniki"
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(
            [match["menu_item_id"] for match in result["matches"]],
            [PAD_THAI_ID, YAKINIKU_ID],
        )

    def test_one_alias_for_two_items_enters_recovery(self) -> None:
        aliases = [
            _alias(YAKINIKU_ID, "specialen"),
            _alias(PAD_THAI_ID, "specialen"),
        ]
        result = self._resolve(
            "Jag tar specialen",
            aliases=aliases,
        )
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["action"], "repeat")
        self.assertEqual(result["matches"], [])

    def test_json_stringified_history_is_accepted(self) -> None:
        request = ResolveMenuItemsV2Request(
            conversation_history=json.dumps(
                [{"role": "user", "message": "Yakiniku"}]
            )
        )
        self.assertIsInstance(request.conversation_history, list)

    def test_official_elevenlabs_history_object_is_accepted(self) -> None:
        request = ResolveMenuItemsV2Request(
            conversation_history=json.dumps(
                {
                    "x-elevenlabs-history": True,
                    "entries": [
                        {"role": "user", "message": "Yakiniku"}
                    ],
                }
            )
        )
        self.assertEqual(
            request.conversation_history,
            [{"role": "user", "message": "Yakiniku"}],
        )

    def test_official_nested_tool_results_increment_recovery(self) -> None:
        request = ResolveMenuItemsV2Request(
            conversation_history={
                "x-elevenlabs-history": True,
                "entries": [
                    {
                        "role": "tool",
                        "tool_results": [
                            {
                                "tool_name": YZ_MENU_RESOLVER_TOOL_NAME,
                                "result_value": json.dumps(
                                    {"status": "NO_MATCH"}
                                ),
                            }
                        ],
                    },
                    {"role": "user", "message": "månpizza"},
                ],
            }
        )
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=self.aliases,
        ):
            result = resolve_restaurant_menu_items(
                context=self.context,
                request=request,
            )
        self.assertEqual(result["unresolved_attempt"], 2)
        self.assertEqual(result["action"], "not_on_menu")

    def test_production_tool_results_increment_recovery(self) -> None:
        request = ResolveMenuItemsV2Request(
            conversation_history={
                "x-elevenlabs-history": True,
                "entries": [
                    {
                        "role": "tool",
                        "tool_results": [
                            {
                                "tool_name": YZ_MENU_RESOLVER_V2_TOOL_NAME,
                                "result_value": json.dumps(
                                    {"status": "NO_MATCH"}
                                ),
                            }
                        ],
                    },
                    {"role": "user", "message": "månpizza"},
                ],
            }
        )
        with patch(
            "app.services.restaurant_menu_resolver."
            "_load_active_menu_items",
            return_value=self.menu,
        ), patch(
            "app.services.restaurant_menu_resolver."
            "_load_menu_item_aliases",
            return_value=self.aliases,
        ):
            result = resolve_restaurant_menu_items(
                context=self.context,
                request=request,
            )
        self.assertEqual(result["unresolved_attempt"], 2)
        self.assertEqual(result["action"], "not_on_menu")

    def test_elevenlabs_tool_accepts_only_raw_system_history(self) -> None:
        config = build_yz_test_menu_resolver_v2_tool_config(
            "svir_tool_test",
            "https://resolver-test.example/v2/resolve-menu-items",
        )
        self.assertEqual(
            config["name"],
            YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
        )
        body = config["api_schema"]["request_body_schema"]
        self.assertEqual(
            set(body["properties"]),
            {"conversation_history"},
        )
        self.assertEqual(
            body["properties"]["conversation_history"][
                "dynamic_variable"
            ],
            "system__conversation_history",
        )
        self.assertFalse(body["additionalProperties"])
        self.assertIn(
            "If the result action is clarify",
            config["description"],
        )

    def test_elevenlabs_tool_rejects_non_https_or_wrong_path(self) -> None:
        for url in (
            "http://resolver-test.example/v2/resolve-menu-items",
            "https://resolver-test.example/v2/submit-order",
            "https://resolver-test.example/v2/resolve-menu-items?x=1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                build_yz_test_menu_resolver_v2_tool_config(
                    "svir_tool_test",
                    url,
                )


if __name__ == "__main__":
    unittest.main()
