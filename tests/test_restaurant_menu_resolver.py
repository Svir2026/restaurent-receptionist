from __future__ import annotations

import json
import os
import unittest
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
from app.schemas.restaurant_tools_v2 import ResolveMenuItemsV2Request
from app.services.restaurant_menu_resolver import (
    YZ_MENU_RESOLVER_TOOL_NAME,
    resolve_restaurant_menu_items,
)
from app.services.elevenlabs_tool_definitions import (
    YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
    build_yz_test_menu_resolver_v2_tool_config,
)


RESTAURANT_ID = UUID("11111111-1111-4111-8111-111111111111")
YAKINIKU_ID = UUID("22222222-2222-4222-8222-222222222222")
PAD_THAI_ID = UUID("33333333-3333-4333-8333-333333333333")
COLA_ID = UUID("44444444-4444-4444-8444-444444444444")
COLA_ZERO_ID = UUID("55555555-5555-4555-8555-555555555555")


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
                PAD_THAI_ID,
                "Pad Thai - Kyckling",
                "Pad Thai med kyckling",
            ),
        ]
        self.aliases = [
            _alias(YAKINIKU_ID, "yakisoba"),
            _alias(YAKINIKU_ID, "yakinaki"),
        ]

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

    def test_yakinaki_is_an_approved_yakiniku_alias(self) -> None:
        result = self._resolve(
            "Jag tar en yakinaki",
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
