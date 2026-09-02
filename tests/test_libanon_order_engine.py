from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException


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

from app.api.routes.libanon_order_engine import (
    _state_repository,
    libanon_agent_order_turn,
    libanon_order_turn,
    require_libanon_order_engine_context,
)
from app.core.tool_auth import ToolRestaurantContext
from app.schemas.libanon_order_engine import LibanonOrderTurnRequest
from app.services.elevenlabs_tool_definitions import (
    LIBANON_ORDER_TURN_TEST_TOOL_NAME,
    build_libanon_order_turn_test_tool_config,
)
from app.services.libanon_menu_catalog import (
    LIBANON_RESTAURANT_ID,
    LIBANON_RESTAURANT_SLUG,
    get_libanon_catalog,
)
from app.services.libanon_order_engine import UNKNOWN_MESSAGES, process_libanon_order_turn
from app.services.voice_order_state import (
    InMemoryVoiceOrderStateRepository,
    SQLiteVoiceOrderStateRepository,
    VoiceOrderStateError,
)


class EngineConversation:
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.history: list[dict[str, str]] = []
        self.repository = InMemoryVoiceOrderStateRepository()

    def turn(self, utterance: str):
        self.history.append({"role": "user", "message": utterance})
        result = process_libanon_order_turn(
            request=LibanonOrderTurnRequest(
                conversation_id=self.conversation_id,
                conversation_history=self.history,
            ),
            repository=self.repository,
        )
        self.history.append({"role": "agent", "message": result.say})
        return result


def _al_forno_context() -> ToolRestaurantContext:
    return ToolRestaurantContext(
        credential_id=UUID("10000000-0000-0000-0000-000000000001"),
        restaurant_id=UUID(LIBANON_RESTAURANT_ID),
        restaurant_name="Restaurang Al Forno",
        restaurant_slug=LIBANON_RESTAURANT_SLUG,
        restaurant_is_active=True,
        provisioning_job_id=None,
        provisioning_job_status=None,
        provisioning_current_step=None,
    )


class AlFornoCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = get_libanon_catalog()

    def test_catalog_identity_and_complete_section_counts(self) -> None:
        self.assertEqual(LIBANON_RESTAURANT_ID, "162089e6-09b0-5928-944b-2906df01f10e")
        self.assertEqual(LIBANON_RESTAURANT_SLUG, "restaurang-al-forno")
        self.assertEqual(len(self.catalog.items), 113)
        self.assertEqual(
            {
                category: sum(item.category_name == category for item in self.catalog.items)
                for category in {item.category_name for item in self.catalog.items}
            },
            {
                "Pizza": 57,
                "Pan Pizza": 5,
                "Pasta": 17,
                "Sallad": 9,
                "Kebab": 16,
                "Hamburgare": 2,
                "À la Carte": 7,
            },
        )

    def test_all_prices_use_sek_integer_minor_units(self) -> None:
        self.assertTrue(all(item.currency == "SEK" for item in self.catalog.items))
        self.assertTrue(all(isinstance(item.base_price_minor, int) for item in self.catalog.items))
        self.assertTrue(all(item.base_price_minor >= 10_000 for item in self.catalog.items))

    def test_every_canonical_name_resolves_to_exactly_itself(self) -> None:
        for item in self.catalog.items:
            with self.subTest(item=item.official_name):
                matches, ambiguities = self.catalog.find_exact_mentions(item.official_name)
                self.assertFalse(ambiguities)
                self.assertEqual([match.item.source_key for match in matches], [item.source_key])

    def test_every_approved_alias_maps_to_one_item(self) -> None:
        for item in self.catalog.items:
            for alias in item.aliases:
                with self.subTest(item=item.official_name, alias=alias):
                    matches, ambiguities = self.catalog.find_exact_mentions(alias)
                    self.assertFalse(ambiguities)
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0].item.source_key, item.source_key)
                    self.assertEqual(matches[0].match_source, "alias")

    def test_capricciosa_pronunciation_alias(self) -> None:
        matches, _ = self.catalog.find_exact_mentions("Jag vill ha en kaprichosa")
        self.assertEqual(matches[0].item.official_name, "Capricciosa")

    def test_unknown_food_is_not_silently_mapped(self) -> None:
        matches, ambiguities = self.catalog.find_exact_mentions("En månpizza med stjärnsås")
        self.assertFalse(matches)
        self.assertFalse(ambiguities)

    def test_standard_pizzas_have_no_size_group(self) -> None:
        for item in self.catalog.items:
            if item.category_name != "Pizza":
                continue
            with self.subTest(item=item.official_name):
                self.assertFalse(any(group.group_type == "size" for group in item.option_groups))

    def test_only_pan_pizzas_have_required_sizes(self) -> None:
        pan_pizzas = [item for item in self.catalog.items if item.category_name == "Pan Pizza"]
        self.assertEqual(len(pan_pizzas), 5)
        for item in pan_pizzas:
            size_groups = [group for group in item.option_groups if group.group_type == "size"]
            self.assertEqual(len(size_groups), 1)
            self.assertTrue(size_groups[0].is_required)
            self.assertEqual([option.name for option in size_groups[0].options], ["Small", "Medium", "Large"])
            self.assertEqual([option.price_delta_minor for option in size_groups[0].options], [0, 2000, 10000])

    def test_side_choice_exists_only_where_menu_requires_it(self) -> None:
        expected = {
            "Kebabtallrik",
            "Kycklingkebabtallrik",
            "Mixkebabtallrik",
            "Gyrostallrik",
            "Falafeltallrik",
            "Vegansk Kebabtallrik",
            "Fish and Chips",
            "Grillbiff",
            "Fläskfilé Black & White",
            "Fläskfilé Oscar",
            "Laxtallrik",
            "Chicken Bits",
        }
        actual = {
            item.official_name
            for item in self.catalog.items
            if any(group.name == "Tillbehör" and group.is_required for group in item.option_groups)
        }
        self.assertEqual(actual, expected)


class AlFornoOrderEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = get_libanon_catalog()

    def test_regular_pizza_is_accepted_without_size_question(self) -> None:
        result = EngineConversation("conv-alforno-pizza").turn("Jag vill ha en kebabpizza")
        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual(result.cart[0].official_name, "Kebabpizza")
        self.assertFalse(result.pending_questions)
        self.assertNotIn("storlek", result.say.casefold())

    def test_pan_pizza_without_size_asks_exactly_once(self) -> None:
        conversation = EngineConversation("conv-alforno-pan-size")
        first = conversation.turn("En pan pizza Rio")
        self.assertEqual(first.action, "ask_question")
        self.assertEqual(first.say, "Vill du ha small, medium eller large?")
        second = conversation.turn("Medium")
        self.assertEqual(second.action, "confirm_delta")
        self.assertEqual([option.name for option in second.cart[0].selected_options], ["Medium"])
        self.assertIn("medium", second.say.casefold())

    def test_pan_pizza_explicit_large_skips_question_and_prices_variant(self) -> None:
        result = EngineConversation("conv-alforno-pan-large").turn("En large pan pizza Palermo")
        self.assertEqual(result.action, "confirm_delta")
        self.assertFalse(result.pending_questions)
        self.assertEqual(result.cart[0].base_price_minor, 16000)
        self.assertEqual(result.cart[0].selected_options[0].price_delta_minor, 10000)

    def test_pan_pizza_compound_pronunciation_alias_asks_for_size(self) -> None:
        result = EngineConversation("conv-alforno-pan-alias").turn("En pannpizza katania")
        self.assertEqual(result.action, "ask_question")
        self.assertEqual(result.cart[0].official_name, "Pan Pizza Catania")
        self.assertEqual(result.say, "Vill du ha small, medium eller large?")

    def test_spoken_carbonara_alias_is_accepted_without_fuzzy_confirmation(self) -> None:
        result = EngineConversation("conv-alforno-carbonara-alias").turn(
            "En spagetti karbonara"
        )
        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual(result.cart[0].official_name, "Spaghetti Carbonara")

    def test_required_side_is_asked_and_saved(self) -> None:
        conversation = EngineConversation("conv-alforno-side")
        first = conversation.turn("En kycklingkebabtallrik")
        self.assertEqual(first.say, "Vill du ha pommes, ris eller bulgur?")
        second = conversation.turn("Bulgur")
        self.assertEqual(second.action, "confirm_delta")
        self.assertEqual([option.name for option in second.cart[0].selected_options], ["Bulgur"])

    def test_multiple_required_choices_name_each_order_item(self) -> None:
        conversation = EngineConversation("conv-alforno-two-sides")
        first = conversation.turn("En kebabtallrik och en kycklingkebabtallrik")
        self.assertEqual(
            first.say,
            "Till Kebabtallrik, vill du ha pommes, ris eller bulgur?",
        )
        second = conversation.turn("Pommes")
        self.assertEqual(
            second.say,
            "Till Kycklingkebabtallrik, vill du ha pommes, ris eller bulgur?",
        )
        third = conversation.turn("Ris")
        self.assertEqual(third.action, "confirm_delta")
        self.assertEqual(
            [value.name for value in third.cart[0].selected_options],
            ["Pommes"],
        )
        self.assertEqual(
            [value.name for value in third.cart[1].selected_options],
            ["Ris"],
        )

    def test_different_required_choices_are_resolved_in_order(self) -> None:
        conversation = EngineConversation("conv-alforno-burger-pan")
        first = conversation.turn("En hamburgare och en pan pizza Rio")
        self.assertEqual(
            first.say,
            "Till Hamburgare, vill du ha 90 gram eller 150 gram?",
        )
        second = conversation.turn("150 gram")
        self.assertEqual(
            second.say,
            "Till Pan Pizza Rio, vill du ha small, medium eller large?",
        )
        third = conversation.turn("Large")
        self.assertEqual(third.action, "confirm_delta")
        self.assertEqual(third.cart[0].selected_options[0].name, "150g")
        self.assertEqual(third.cart[1].selected_options[0].name, "Large")

    def test_multiple_items_and_removals_are_preserved(self) -> None:
        result = EngineConversation("conv-alforno-multi").turn(
            "En kebabpizza utan lök och en kycklingpizza utan ananas"
        )
        self.assertEqual([item.official_name for item in result.cart], ["Kebabpizza", "Kycklingpizza"])
        self.assertEqual(result.cart[0].notes[0].text, "Utan lök")
        self.assertEqual(result.cart[1].notes[0].text, "Utan ananas")

    def test_extra_topping_is_customer_note_without_invented_price(self) -> None:
        result = EngineConversation("conv-alforno-extra").turn("En Hawaii med extra skinka")
        self.assertEqual(result.cart[0].notes[0].text, "Extra skinka")
        self.assertFalse(result.cart[0].pricing_complete)

    def test_half_chicken_half_kebab_creates_one_reviewable_pizza(self) -> None:
        result = EngineConversation("conv-alforno-half-short").turn(
            "En pizza hälften kyckling hälften kebab"
        )
        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual(len(result.cart), 1)
        self.assertEqual(
            result.cart[0].official_name,
            "Halva Kycklingpizza / halva Kebabpizza",
        )
        self.assertEqual(
            result.cart[0].notes[0].text,
            "Halva Kycklingpizza, halva Kebabpizza",
        )
        self.assertFalse(result.cart[0].pricing_complete)
        self.assertIn("halva kycklingpizza och halva kebabpizza", result.say.casefold())

    def test_half_named_pizzas_do_not_become_two_full_pizzas(self) -> None:
        result = EngineConversation("conv-alforno-half-named").turn(
            "En pizza halva Hawaii och halva Capricciosa"
        )
        self.assertEqual(len(result.cart), 1)
        self.assertEqual(result.cart[0].quantity, 1)
        self.assertIn("Halva Hawaii", result.cart[0].kitchen_display_name)
        self.assertIn("halva Capricciosa", result.cart[0].kitchen_display_name)

    def test_modifier_only_follow_up_changes_last_item(self) -> None:
        conversation = EngineConversation("conv-alforno-followup")
        conversation.turn("En Capricciosa")
        result = conversation.turn("Utan champinjoner")
        self.assertEqual(len(result.cart), 1)
        self.assertEqual(result.cart[0].notes[0].text, "Utan champinjoner")

    def test_remove_then_extra_does_not_leak_connector_words(self) -> None:
        result = EngineConversation("conv-alforno-clean-modifiers").turn(
            "En kebabrulle utan sallad med extra kebab"
        )
        self.assertEqual(
            [(note.kind, note.text) for note in result.cart[0].notes],
            [("remove", "Utan sallad"), ("extra", "Extra kebab")],
        )
        self.assertIn("utan sallad och med extra kebab", result.say.casefold())

    def test_colloquial_add_on_does_not_store_trailing_preposition(self) -> None:
        result = EngineConversation("conv-alforno-pommes-on").turn(
            "En kycklingpizza med pommes på"
        )
        self.assertEqual(result.cart[0].notes[0].text, "Extra pommes")

    def test_unspecified_hamburger_asks_which_weight(self) -> None:
        result = EngineConversation("conv-alforno-burger-ambiguous").turn("En hamburgare")
        self.assertEqual(result.action, "ask_question")
        self.assertEqual(result.say, "Vill du ha 90 gram eller 150 gram?")

    def test_spoken_hamburger_weight_resolves_directly(self) -> None:
        result = EngineConversation("conv-alforno-burger-weight").turn(
            "En hamburgare 150 gram utan lök"
        )
        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual(result.cart[0].official_name, "Hamburgare")
        self.assertEqual(result.cart[0].selected_options[0].name, "150g")
        self.assertEqual(result.cart[0].notes[0].text, "Utan lök")

    def test_later_item_confirmation_does_not_repeat_existing_cart(self) -> None:
        conversation = EngineConversation("conv-alforno-delta")
        conversation.turn("En Vesuvio")
        result = conversation.turn("En Pasta Romana också")
        self.assertIn("Pasta Romana", result.say)
        self.assertNotIn("Vesuvio", result.say)
        self.assertEqual(len(result.cart), 2)

    def test_final_yes_confirms_full_cart_once(self) -> None:
        conversation = EngineConversation("conv-alforno-final")
        conversation.turn("En Vesuvio")
        conversation.turn("En Pasta Romana också")
        result = conversation.turn("Ja")
        self.assertEqual(result.action, "confirm_full_order")
        self.assertIn("Vesuvio", result.say)
        self.assertIn("Pasta Romana", result.say)
        self.assertEqual(result.say.count("Vesuvio"), 1)
        self.assertFalse(result.submission_allowed)

    def test_fuzzy_match_requires_explicit_confirmation(self) -> None:
        conversation = EngineConversation("conv-alforno-fuzzy")
        first = conversation.turn("En capricosa")
        self.assertEqual(first.action, "ask_question")
        self.assertEqual(first.say, "Menar du Capricciosa?")
        self.assertFalse(first.cart)
        second = conversation.turn("Ja")
        self.assertEqual(second.cart[0].official_name, "Capricciosa")

    def test_unknown_item_uses_three_step_fallback_and_never_enters_cart(self) -> None:
        conversation = EngineConversation("conv-alforno-fallback")
        for index, expected in enumerate(UNKNOWN_MESSAGES):
            result = conversation.turn(f"Månpizza variant {index}")
            self.assertEqual(result.say, expected)
            self.assertFalse(result.cart)
        self.assertEqual(result.action, "technical_stop")

    def test_valid_item_resets_unknown_counter(self) -> None:
        conversation = EngineConversation("conv-alforno-reset")
        conversation.turn("Månpizza")
        valid = conversation.turn("En Funghi")
        self.assertEqual(valid.cart[0].official_name, "Funghi")
        self.assertEqual(valid.action, "confirm_delta")

    def test_idempotent_replay_does_not_duplicate_item(self) -> None:
        repository = InMemoryVoiceOrderStateRepository()
        request = LibanonOrderTurnRequest(
            conversation_id="conv-alforno-replay",
            conversation_history=[{"role": "user", "message": "En Margherita"}],
        )
        first = process_libanon_order_turn(request=request, repository=repository)
        replay = process_libanon_order_turn(request=request, repository=repository)
        self.assertFalse(first.idempotent_replay)
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(len(replay.cart), 1)

    def test_every_menu_item_can_enter_order_state(self) -> None:
        for index, item in enumerate(self.catalog.items):
            with self.subTest(item=item.official_name):
                conversation = EngineConversation(f"conv-alforno-all-{index:03d}")
                result = conversation.turn(f"En {item.official_name}")
                self.assertTrue(result.cart)
                self.assertEqual(result.cart[0].official_name, item.official_name)
                while result.pending_questions:
                    pending = result.pending_questions[0]
                    line = next(value for value in result.cart if value.line_id == pending.line_id)
                    catalog_item = self.catalog.get_item(line.item_source_key)
                    group = next(value for value in catalog_item.option_groups if value.source_key == pending.group_source_key)
                    result = conversation.turn(group.options[0].name)
                self.assertEqual(result.action, "confirm_delta")


class AlFornoToolAndRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = LibanonOrderTurnRequest(
            conversation_id="conv-alforno-route",
            conversation_history=[{"role": "user", "message": "En kebabpizza"}],
        )
        self.context = _al_forno_context()

    def test_tool_uses_compact_dynamic_conversation_state(self) -> None:
        config = build_libanon_order_turn_test_tool_config(
            "preview-token-that-is-longer-than-thirty-two-characters",
            "https://al-forno-test.example/v2/al-forno/order-turn-agent",
        )
        body = config["api_schema"]["request_body_schema"]
        self.assertEqual(config["name"], "svir_al_forno_order_turn_test")
        self.assertEqual(body["properties"]["conversation_id"]["dynamic_variable"], "system__conversation_id")
        self.assertEqual(body["properties"]["conversation_history"]["dynamic_variable"], "system__conversation_history")
        self.assertIn("say only `say` verbatim", config["description"])
        self.assertIn("cannot submit a real restaurant order", config["description"])

    def test_prompt_is_short_and_routes_order_state_to_tool(self) -> None:
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "app", "data", "libanon_test_agent_prompt.txt")
        with open(prompt_path, encoding="utf-8") as prompt_file:
            prompt = prompt_file.read()
        self.assertLess(len(prompt), 3_500)
        self.assertIn("Restaurang Al Forno", prompt)
        self.assertIn("svir_al_forno_order_turn_test", prompt)
        self.assertIn("Vanliga pizzor har ingen storleksfråga", prompt)
        self.assertIn("end_call", prompt)

    def test_route_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AL_FORNO_ORDER_ENGINE_TEST_ENABLED", None)
            with self.assertRaises(HTTPException) as caught:
                libanon_order_turn(self.payload, self.context)
        self.assertEqual(caught.exception.status_code, 404)

    def test_route_rejects_another_tenant(self) -> None:
        other = ToolRestaurantContext(
            credential_id=self.context.credential_id,
            restaurant_id=UUID("20000000-0000-0000-0000-000000000002"),
            restaurant_name="Other Restaurant",
            restaurant_slug="other-restaurant",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )
        with patch.dict(os.environ, {"AL_FORNO_ORDER_ENGINE_TEST_ENABLED": "true"}, clear=False):
            with self.assertRaises(HTTPException) as caught:
                libanon_order_turn(self.payload, other)
        self.assertEqual(caught.exception.status_code, 403)

    def test_preview_token_resolves_only_in_isolated_sqlite_mode(self) -> None:
        preview_token = "preview-token-that-is-longer-than-thirty-two-characters"
        with patch.dict(
            os.environ,
            {
                "AL_FORNO_ORDER_ENGINE_TEST_ENABLED": "true",
                "AL_FORNO_ORDER_STATE_BACKEND": "sqlite",
                "AL_FORNO_PREVIEW_TOOL_TOKEN": preview_token,
            },
            clear=False,
        ):
            context = require_libanon_order_engine_context(preview_token)
            with self.assertRaises(HTTPException) as caught:
                require_libanon_order_engine_context("wrong-token")
        self.assertEqual(context.restaurant_slug, "restaurang-al-forno")
        self.assertEqual(context.restaurant_name, "Restaurang Al Forno")
        self.assertEqual(caught.exception.status_code, 401)

    def test_agent_route_is_compact_and_cannot_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state_repository.cache_clear()
            with patch.dict(
                os.environ,
                {
                    "AL_FORNO_ORDER_ENGINE_TEST_ENABLED": "true",
                    "AL_FORNO_ORDER_STATE_BACKEND": "sqlite",
                    "AL_FORNO_ORDER_SQLITE_PATH": os.path.join(directory, "state.sqlite3"),
                },
                clear=False,
            ):
                response = libanon_agent_order_turn(self.payload, self.context)
        _state_repository.cache_clear()
        self.assertEqual(
            set(response.model_dump()),
            {"success", "action", "say", "idempotent_replay", "state_revision", "order_ready", "submission_allowed"},
        )
        self.assertFalse(response.submission_allowed)


class AlFornoSQLiteStateTests(unittest.TestCase):
    def test_state_and_idempotency_survive_repository_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "voice-state.sqlite3")
            request = LibanonOrderTurnRequest(
                conversation_id="conv-alforno-sqlite",
                conversation_history=[{"role": "user", "message": "En kebabpizza"}],
            )
            first = process_libanon_order_turn(request=request, repository=SQLiteVoiceOrderStateRepository(database_path))
            replay = process_libanon_order_turn(request=request, repository=SQLiteVoiceOrderStateRepository(database_path))
            self.assertFalse(first.idempotent_replay)
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(len(replay.cart), 1)

    def test_sqlite_path_must_be_absolute(self) -> None:
        with self.assertRaises(VoiceOrderStateError):
            SQLiteVoiceOrderStateRepository("relative/state.sqlite3")


if __name__ == "__main__":
    unittest.main()
