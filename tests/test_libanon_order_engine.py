from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

from app.schemas.libanon_order_engine import LibanonOrderTurnRequest
from app.api.routes.libanon_order_engine import (
    _state_repository,
    libanon_agent_order_turn,
    libanon_order_turn,
    require_libanon_order_engine_context,
)
from app.services.elevenlabs_tool_definitions import (
    LIBANON_ORDER_TURN_TEST_TOOL_NAME,
    build_libanon_order_turn_test_tool_config,
)
from app.core.tool_auth import ToolRestaurantContext
from app.services.libanon_menu_catalog import (
    LIBANON_RESTAURANT_ID,
    get_libanon_catalog,
)
from app.services.libanon_order_engine import process_libanon_order_turn
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


class LibanonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = get_libanon_catalog()

    def test_snapshot_is_tenant_scoped_and_complete(self) -> None:
        self.assertEqual(len(self.catalog.items), 112)
        self.assertEqual(self.catalog.verification_status, "needs_review")
        self.assertTrue(all(value.currency == "SEK" for value in self.catalog.items))
        self.assertTrue(
            all(isinstance(value.base_price_minor, int) for value in self.catalog.items)
        )
        self.assertEqual(LIBANON_RESTAURANT_ID, "613079d4-7680-40b0-a5cc-465e813a5267")

    def test_every_active_item_name_is_resolvable_or_explicitly_ambiguous(
        self,
    ) -> None:
        for item in self.catalog.items:
            with self.subTest(item=item.official_name):
                matches, ambiguities = self.catalog.find_exact_mentions(
                    item.official_name
                )
                resolved_ids = {value.item.source_key for value in matches}
                ambiguous_ids = {
                    candidate.source_key
                    for ambiguity in ambiguities
                    for candidate in ambiguity.items
                }
                self.assertIn(
                    item.source_key,
                    resolved_ids | ambiguous_ids,
                )

    def test_duplicate_names_are_not_silently_guessed(self) -> None:
        matches, ambiguities = self.catalog.find_exact_mentions("Mixspett 1")
        self.assertEqual(matches, ())
        self.assertTrue(ambiguities)
        self.assertEqual(len(ambiguities[0].items), 2)

    def test_approved_alias_maps_to_one_item(self) -> None:
        matches, ambiguities = self.catalog.find_exact_mentions(
            "Jag vill ha en carpacciosa"
        )
        self.assertFalse(ambiguities)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].item.official_name, "Capricciosa")
        self.assertEqual(matches[0].match_source, "alias")

    def test_unknown_text_does_not_resolve(self) -> None:
        matches, ambiguities = self.catalog.find_exact_mentions(
            "Jag vill ha en månpizza"
        )
        self.assertFalse(matches)
        self.assertFalse(ambiguities)

    def test_fuzzy_result_is_only_a_suggestion(self) -> None:
        suggestion = self.catalog.suggest_unique_fuzzy("capricosa")
        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.item.official_name, "Capricciosa")

    def test_elevenlabs_test_tool_accepts_only_raw_system_state(self) -> None:
        config = build_libanon_order_turn_test_tool_config(
            "preview-token-that-is-longer-than-thirty-two-characters",
            "https://libanon-test.example/v2/libanon/order-turn-agent",
        )
        body = config["api_schema"]["request_body_schema"]

        self.assertEqual(config["name"], LIBANON_ORDER_TURN_TEST_TOOL_NAME)
        self.assertEqual(
            body["properties"]["conversation_id"]["dynamic_variable"],
            "system__conversation_id",
        )
        self.assertEqual(
            body["properties"]["conversation_history"]["dynamic_variable"],
            "system__conversation_history",
        )
        self.assertNotIn("additionalProperties", body)
        self.assertIn("say only `say` verbatim", config["description"])
        self.assertIn("unknown or invented", config["description"])
        self.assertIn("Never use the knowledge base", config["description"])
        self.assertIn("must be the first action", config["description"])

    def test_test_prompt_routes_every_order_attempt_to_tool(self) -> None:
        prompt_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "data",
            "libanon_test_agent_prompt.txt",
        )
        with open(prompt_path, encoding="utf-8") as prompt_file:
            prompt = prompt_file.read()

        self.assertIn("ett okänt produktnamn", prompt)
        self.assertIn("ovanlig ändring som halva-halva", prompt)
        self.assertIn(
            "Använd aldrig kunskapsbasen för att själv godkänna",
            prompt,
        )
        self.assertIn("Verktygsanropet måste vara din allra första handling", prompt)
        self.assertIn("Fråga aldrig om något mer då", prompt)
        self.assertIn("tredje tekniska stopp", prompt)
        self.assertIn("orderdialogen stängd för resten av", prompt)

    def test_elevenlabs_test_tool_rejects_wrong_endpoint(self) -> None:
        with self.assertRaises(ValueError):
            build_libanon_order_turn_test_tool_config(
                "preview-token-that-is-longer-than-thirty-two-characters",
                "https://libanon-test.example/v2/libanon/order-turn",
            )


class LibanonOrderEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = get_libanon_catalog()

    def test_pizza_defaults_without_size_question(self) -> None:
        conversation = EngineConversation("conv-libanon-001")
        result = conversation.turn("Jag vill ha en kebabpizza")

        self.assertEqual(result.action, "confirm_delta")
        self.assertNotIn("storlek", result.say.casefold())
        self.assertNotIn("large", result.say.casefold())
        self.assertEqual(result.cart[0].official_name, "Kebab Pizza")
        self.assertEqual(
            [value.name for value in result.cart[0].selected_options],
            ["Standard"],
        )

    def test_explicit_family_pizza_selects_family_option(self) -> None:
        conversation = EngineConversation("conv-libanon-002")
        result = conversation.turn("En familjepizza Capricciosa")

        self.assertEqual(result.action, "confirm_delta")
        self.assertIn(
            "Familjepizza",
            [value.name for value in result.cart[0].selected_options],
        )

    def test_one_size_pizza_never_asks_for_size(self) -> None:
        conversation = EngineConversation("conv-libanon-003")
        result = conversation.turn("En Calzone")
        self.assertEqual(result.action, "confirm_delta")
        self.assertFalse(result.pending_questions)

    def test_multiple_items_from_one_utterance_are_all_preserved(self) -> None:
        conversation = EngineConversation("conv-libanon-004")
        result = conversation.turn("En kebabpizza utan lök och en kycklingpizza")

        self.assertEqual(
            [value.official_name for value in result.cart],
            ["Kebab Pizza", "Favorite"],
        )
        self.assertEqual(result.cart[0].notes[0].text, "Utan lök")
        self.assertEqual(result.cart[1].notes, [])

    def test_later_addition_confirms_only_new_item(self) -> None:
        conversation = EngineConversation("conv-libanon-005")
        conversation.turn("En kebabpizza")
        result = conversation.turn("En Cola Zero också")

        self.assertIn("Cola Zero", result.say)
        self.assertNotIn("kebabpizza", result.say.casefold())
        self.assertEqual(len(result.cart), 2)

    def test_final_yes_reads_full_cart_once(self) -> None:
        conversation = EngineConversation("conv-libanon-006")
        conversation.turn("En kebabpizza")
        conversation.turn("En Cola Zero också")
        result = conversation.turn("Ja")

        self.assertEqual(result.action, "confirm_full_order")
        self.assertIn("kebabpizza", result.say.casefold())
        self.assertIn("Cola Zero", result.say)
        self.assertTrue(result.order_ready)
        self.assertFalse(result.submission_allowed)
        self.assertIn("inte restaurangverifierade", result.submission_blocked_reason)

    def test_natural_affirmative_does_not_enter_unknown_fallback(self) -> None:
        conversation = EngineConversation("conv-libanon-natural-yes")
        conversation.turn("En kebabpizza")
        result = conversation.turn("Ja tack")

        self.assertEqual(result.action, "confirm_full_order")
        self.assertTrue(result.order_ready)

    def test_remove_ingredient_is_item_scoped_note(self) -> None:
        conversation = EngineConversation("conv-libanon-007")
        result = conversation.turn(
            "En Hawaii utan ananas och en Capricciosa utan champinjoner"
        )
        self.assertEqual(
            [[note.text for note in line.notes] for line in result.cart],
            [["Utan ananas"], ["Utan champinjoner"]],
        )

    def test_multiple_removals_on_one_item_are_all_preserved(self) -> None:
        conversation = EngineConversation("conv-libanon-multiple-removals")
        result = conversation.turn("En kebabpizza utan sallad utan pepperoni")

        self.assertEqual(
            [note.text for note in result.cart[0].notes],
            ["Utan sallad", "Utan pepperoni"],
        )

    def test_priced_extra_ingredient_uses_catalog_option(self) -> None:
        conversation = EngineConversation("conv-libanon-008")
        result = conversation.turn("En kebabpizza med extra ost")
        line = result.cart[0]

        self.assertIn("Ost", [value.name for value in line.selected_options])
        self.assertNotIn("Extra ost", [value.text for value in line.notes])
        self.assertTrue(line.pricing_complete)
        self.assertIn("med extra ost", result.say)

    def test_unpriced_extra_is_preserved_but_blocks_pricing(self) -> None:
        conversation = EngineConversation("conv-libanon-009")
        result = conversation.turn("En kebabpizza med pommes")
        line = result.cart[0]

        self.assertIn("Extra pommes", [value.text for value in line.notes])
        self.assertFalse(line.pricing_complete)

    def test_grill_item_asks_only_for_required_side(self) -> None:
        conversation = EngineConversation("conv-libanon-010")
        result = conversation.turn("En shish taouk")
        self.assertEqual(result.action, "ask_question")
        self.assertEqual(
            result.say,
            "Vill du ha ris, bulgur, pommes eller klyftpotatis?",
        )

    def test_option_answer_and_new_item_in_same_turn_are_both_kept(self) -> None:
        conversation = EngineConversation("conv-libanon-011")
        conversation.turn("En shish taouk")
        result = conversation.turn("Ris och en Cola")

        self.assertEqual(
            [value.official_name for value in result.cart],
            ["Shish Taouk", "Coca-Cola Original Taste 33 cl"],
        )
        self.assertIn("Ris", [value.name for value in result.cart[0].selected_options])

    def test_two_required_choices_are_queued_not_overwritten(self) -> None:
        conversation = EngineConversation("conv-libanon-012")
        result = conversation.turn("En shish taouk och en kebabtallrik")
        self.assertEqual(len(result.pending_questions), 2)

        result = conversation.turn("Ris")
        self.assertEqual(len(result.pending_questions), 1)
        result = conversation.turn("Pommes")
        self.assertFalse(result.pending_questions)
        self.assertEqual(len(result.cart), 2)

    def test_invalid_required_option_repeats_question_without_menu_fallback(
        self,
    ) -> None:
        conversation = EngineConversation("conv-libanon-required-option-retry")
        first = conversation.turn("En shish taouk")
        retried = conversation.turn("Ja")

        self.assertEqual(retried.action, "ask_question")
        self.assertEqual(retried.say, first.say)
        self.assertEqual(retried.pending_questions, first.pending_questions)
        self.assertEqual(retried.state_revision, 2)

    def test_child_menu_item_is_not_confused_with_adult_item(self) -> None:
        conversation = EngineConversation("conv-libanon-013")
        result = conversation.turn("En kebabtallrik för barn")
        self.assertEqual(result.cart[0].official_name, "Kebabtallrik Barn")
        self.assertNotEqual(result.cart[0].official_name, "Kebabtallrik")

    def test_kebab_and_kebabpizza_are_distinct(self) -> None:
        conversation = EngineConversation("conv-libanon-014")
        kebab = conversation.turn("En kebab med bröd")
        self.assertEqual(kebab.cart[0].official_name, "Kebab Med Bröd")

        pizza = conversation.turn("En kebabpizza också")
        self.assertEqual(pizza.cart[-1].official_name, "Kebab Pizza")

    def test_fuzzy_candidate_requires_explicit_confirmation(self) -> None:
        conversation = EngineConversation("conv-libanon-015")
        first = conversation.turn("En capricosa")
        self.assertEqual(first.action, "ask_question")
        self.assertEqual(first.say, "Menar du Capricciosa?")
        self.assertFalse(first.cart)

        confirmed = conversation.turn("Ja")
        self.assertEqual(confirmed.cart[0].official_name, "Capricciosa")

    def test_valid_dish_replaces_stale_fuzzy_clarification(self) -> None:
        conversation = EngineConversation("conv-libanon-fuzzy-replaced")
        conversation.turn("En capricosa")
        result = conversation.turn("Nej, jag menar Hawaii")

        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual([line.official_name for line in result.cart], ["Hawaii"])
        self.assertFalse(result.pending_questions)

    def test_unknown_item_has_bounded_three_step_recovery(self) -> None:
        conversation = EngineConversation("conv-libanon-016")
        responses = [conversation.turn("månpizza") for _ in range(3)]
        self.assertEqual(
            [value.action for value in responses],
            [
                "repeat_unknown_item",
                "reject_unknown_item",
                "technical_stop",
            ],
        )
        self.assertFalse(responses[-1].cart)

    def test_valid_item_resets_unknown_counter(self) -> None:
        conversation = EngineConversation("conv-libanon-017")
        conversation.turn("månpizza")
        result = conversation.turn("En Hawaii")
        self.assertEqual(result.action, "confirm_delta")

        result = conversation.turn("månpizza")
        self.assertEqual(result.action, "repeat_unknown_item")

    def test_duplicate_tool_event_is_idempotent(self) -> None:
        repository = InMemoryVoiceOrderStateRepository()
        request = LibanonOrderTurnRequest(
            conversation_id="conv-libanon-018",
            conversation_history=[{"role": "user", "message": "En kebabpizza"}],
        )
        first = process_libanon_order_turn(request=request, repository=repository)
        second = process_libanon_order_turn(request=request, repository=repository)

        self.assertFalse(first.idempotent_replay)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(second.cart), 1)
        self.assertEqual(second.cart[0].quantity, 1)

    def test_concurrent_duplicate_event_cannot_duplicate_cart_line(self) -> None:
        class SynchronizedLoadRepository(InMemoryVoiceOrderStateRepository):
            def __init__(self) -> None:
                super().__init__()
                self.barrier = threading.Barrier(2)
                self.load_count = 0
                self.load_count_lock = threading.Lock()

            def load(self, *, restaurant_id: str, conversation_id: str):
                state = super().load(
                    restaurant_id=restaurant_id,
                    conversation_id=conversation_id,
                )
                with self.load_count_lock:
                    should_wait = self.load_count < 2
                    self.load_count += 1
                if should_wait:
                    self.barrier.wait(timeout=5)
                return state

        repository = SynchronizedLoadRepository()
        request = LibanonOrderTurnRequest(
            conversation_id="conv-libanon-concurrent",
            conversation_history=[{"role": "user", "message": "En kebabpizza"}],
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    process_libanon_order_turn,
                    request=request,
                    repository=repository,
                )
                for _ in range(2)
            ]
            results = [future.result(timeout=5) for future in futures]

        self.assertEqual(
            sorted(value.idempotent_replay for value in results),
            [False, True],
        )
        stored = repository.load(
            restaurant_id=LIBANON_RESTAURANT_ID,
            conversation_id=request.conversation_id,
        )
        assert stored is not None
        self.assertEqual(len(stored.items), 1)
        self.assertEqual(stored.items[0].quantity, 1)

    def test_half_and_half_is_preserved_and_not_auto_priced(self) -> None:
        conversation = EngineConversation("conv-libanon-019")
        result = conversation.turn("En kebabpizza med halva kyckling och halva kebab")
        line = result.cart[0]
        self.assertIn(
            "Halva kyckling, halva kebab",
            [value.text for value in line.notes],
        )
        self.assertEqual(len(line.notes), 1)
        self.assertEqual(
            result.say.count("halva kyckling och halva kebab"),
            1,
        )
        self.assertFalse(line.pricing_complete)

    def test_modifier_only_follow_up_updates_last_line(self) -> None:
        conversation = EngineConversation("conv-libanon-020")
        conversation.turn("En Hawaii")
        result = conversation.turn("Utan ananas")
        self.assertEqual(len(result.cart), 1)
        self.assertEqual(result.cart[0].notes[0].text, "Utan ananas")

    def test_named_modifier_updates_existing_line_without_duplicate(self) -> None:
        conversation = EngineConversation("conv-libanon-021")
        conversation.turn("En kebabpizza")
        result = conversation.turn("Kebabpizzan utan lök")
        self.assertEqual(len(result.cart), 1)
        self.assertEqual(result.cart[0].notes[0].text, "Utan lök")

    def test_large_mixed_order_keeps_every_line(self) -> None:
        conversation = EngineConversation("conv-libanon-022")
        result = conversation.turn(
            "Två Margherita, en Hawaii utan ananas, en kebabpizza "
            "med extra ost och en Cola Zero"
        )
        self.assertEqual(len(result.cart), 4)
        self.assertEqual(result.cart[0].quantity, 2)
        self.assertEqual(result.cart[1].notes[0].text, "Utan ananas")
        self.assertIn("Ost", [value.name for value in result.cart[2].selected_options])

    def test_quantity_with_stycken_is_preserved(self) -> None:
        conversation = EngineConversation("conv-libanon-quantity-stycken")
        result = conversation.turn("Två stycken kebabpizza")
        self.assertEqual(result.cart[0].quantity, 2)

    def test_repeated_identical_item_keeps_line_identity_and_delta_quantity(
        self,
    ) -> None:
        conversation = EngineConversation("conv-libanon-repeated-item")
        conversation.turn("En kebabpizza")
        result = conversation.turn("En kebabpizza till")

        self.assertEqual(len(result.cart), 2)
        self.assertEqual([line.quantity for line in result.cart], [1, 1])
        self.assertIn("en kebabpizza också", result.say.casefold())
        self.assertNotIn("2 kebabpizza", result.say.casefold())

    def test_no_phone_number_is_needed_to_build_test_order(self) -> None:
        conversation = EngineConversation("conv-libanon-023")
        result = conversation.turn("En Margherita")
        self.assertEqual(result.action, "confirm_delta")
        self.assertEqual(len(result.cart), 1)

    def test_duplicate_mixspett_is_disambiguated_by_category(self) -> None:
        conversation = EngineConversation("conv-libanon-024")
        first = conversation.turn("En Mixspett 1")
        self.assertIn("med dryck", first.say)
        self.assertIn("ordinarie", first.say)

        selected = conversation.turn("Den ordinarie")
        self.assertEqual(selected.cart[0].category_name, "Mixspett")

    def test_duplicate_creme_toum_is_disambiguated_by_use(self) -> None:
        conversation = EngineConversation("conv-libanon-025")
        first = conversation.turn("En Crème Toum")
        self.assertIn("som meze", first.say)
        self.assertIn("som tillbehör", first.say)

        selected = conversation.turn("Som tillbehör")
        self.assertEqual(selected.cart[0].category_name, "Tillbehör & Sås")

    def test_every_unique_catalog_item_can_reach_confirmation(self) -> None:
        duplicated_phrases = {
            phrase
            for phrase, matches in self.catalog.phrase_index.items()
            if len({item.source_key for item, _ in matches}) > 1
        }

        for index, item in enumerate(self.catalog.items):
            if any(phrase in duplicated_phrases for phrase, _ in item.phrases):
                continue

            with self.subTest(item=item.official_name):
                conversation = EngineConversation(f"conv-menu-{index:04d}")
                result = conversation.turn(item.official_name)
                safety_counter = 0

                while result.pending_questions:
                    safety_counter += 1
                    self.assertLess(safety_counter, 12)
                    pending = result.pending_questions[0]
                    self.assertEqual(pending.kind, "required_option")
                    line = next(
                        value
                        for value in result.cart
                        if value.line_id == pending.line_id
                    )
                    catalog_item = self.catalog.get_item(line.item_source_key)
                    group = next(
                        value
                        for value in catalog_item.option_groups
                        if value.source_key == pending.group_source_key
                    )
                    answer = " och ".join(
                        value.name for value in group.options[: group.min_select]
                    )
                    result = conversation.turn(answer)

                self.assertEqual(result.action, "confirm_delta")
                self.assertTrue(result.cart)


class LibanonOrderEngineRouteIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = LibanonOrderTurnRequest(
            conversation_id="conv-route-isolation",
            conversation_history=[{"role": "user", "message": "En kebabpizza"}],
        )
        self.libanon_context = ToolRestaurantContext(
            credential_id=UUID("10000000-0000-0000-0000-000000000001"),
            restaurant_id=UUID(LIBANON_RESTAURANT_ID),
            restaurant_name="Libanon Kolgrill",
            restaurant_slug="lebanon-kolgrill",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )

    def test_route_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIBANON_ORDER_ENGINE_TEST_ENABLED", None)
            with self.assertRaises(HTTPException) as caught:
                libanon_order_turn(self.payload, self.libanon_context)

        self.assertEqual(caught.exception.status_code, 404)

    def test_route_rejects_other_tenant_before_state_access(self) -> None:
        other_context = ToolRestaurantContext(
            credential_id=UUID("10000000-0000-0000-0000-000000000002"),
            restaurant_id=UUID("20000000-0000-0000-0000-000000000002"),
            restaurant_name="Other Restaurant",
            restaurant_slug="other-restaurant",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )

        with patch.dict(
            os.environ,
            {"LIBANON_ORDER_ENGINE_TEST_ENABLED": "true"},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as caught:
                libanon_order_turn(self.payload, other_context)

        self.assertEqual(caught.exception.status_code, 403)

    def test_isolated_preview_token_resolves_only_in_sqlite_test_mode(self) -> None:
        preview_token = "preview-token-that-is-longer-than-thirty-two-characters"
        with patch.dict(
            os.environ,
            {
                "LIBANON_ORDER_ENGINE_TEST_ENABLED": "true",
                "LIBANON_ORDER_STATE_BACKEND": "sqlite",
                "LIBANON_PREVIEW_TOOL_TOKEN": preview_token,
            },
            clear=False,
        ):
            context = require_libanon_order_engine_context(preview_token)
            with self.assertRaises(HTTPException) as caught:
                require_libanon_order_engine_context("wrong-token")

        self.assertEqual(str(context.restaurant_id), LIBANON_RESTAURANT_ID)
        self.assertEqual(context.restaurant_slug, "lebanon-kolgrill")
        self.assertEqual(caught.exception.status_code, 401)

    def test_preview_auth_fails_closed_when_token_is_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIBANON_ORDER_ENGINE_TEST_ENABLED": "true",
                "LIBANON_ORDER_STATE_BACKEND": "sqlite",
                "LIBANON_PREVIEW_TOOL_TOKEN": "",
            },
            clear=False,
        ):
            with self.assertRaises(HTTPException) as caught:
                require_libanon_order_engine_context("any-token")

        self.assertEqual(caught.exception.status_code, 503)

    def test_agent_route_returns_only_compact_non_submitting_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state_repository.cache_clear()
            with patch.dict(
                os.environ,
                {
                    "LIBANON_ORDER_ENGINE_TEST_ENABLED": "true",
                    "LIBANON_ORDER_STATE_BACKEND": "sqlite",
                    "LIBANON_ORDER_SQLITE_PATH": os.path.join(
                        directory,
                        "agent-route.sqlite3",
                    ),
                },
                clear=False,
            ):
                response = libanon_agent_order_turn(
                    self.payload,
                    self.libanon_context,
                )

        _state_repository.cache_clear()
        self.assertEqual(
            set(response.model_dump()),
            {
                "success",
                "action",
                "say",
                "idempotent_replay",
                "state_revision",
                "order_ready",
                "submission_allowed",
            },
        )
        self.assertFalse(response.submission_allowed)


class SQLiteVoiceOrderStateTests(unittest.TestCase):
    def test_state_and_idempotency_survive_repository_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "voice-state.sqlite3")
            request = LibanonOrderTurnRequest(
                conversation_id="conv-sqlite-persistence",
                conversation_history=[{"role": "user", "message": "En kebabpizza"}],
            )

            first = process_libanon_order_turn(
                request=request,
                repository=SQLiteVoiceOrderStateRepository(database_path),
            )
            replay = process_libanon_order_turn(
                request=request,
                repository=SQLiteVoiceOrderStateRepository(database_path),
            )

            self.assertFalse(first.idempotent_replay)
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(len(replay.cart), 1)
            self.assertEqual(replay.cart[0].quantity, 1)

    def test_sqlite_path_must_be_absolute(self) -> None:
        with self.assertRaises(VoiceOrderStateError) as caught:
            SQLiteVoiceOrderStateRepository("relative/state.sqlite3")
        self.assertEqual(caught.exception.code, "INVALID_SQLITE_STATE_PATH")

    def test_enabled_route_can_use_isolated_sqlite_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "route-state.sqlite3")
            _state_repository.cache_clear()
            with patch.dict(
                os.environ,
                {
                    "LIBANON_ORDER_ENGINE_TEST_ENABLED": "true",
                    "LIBANON_ORDER_STATE_BACKEND": "sqlite",
                    "LIBANON_ORDER_SQLITE_PATH": database_path,
                },
                clear=False,
            ):
                context = ToolRestaurantContext(
                    credential_id=UUID("10000000-0000-0000-0000-000000000003"),
                    restaurant_id=UUID(LIBANON_RESTAURANT_ID),
                    restaurant_name="Libanon Kolgrill",
                    restaurant_slug="lebanon-kolgrill",
                    restaurant_is_active=True,
                    provisioning_job_id=None,
                    provisioning_job_status=None,
                    provisioning_current_step=None,
                )
                response = libanon_order_turn(
                    LibanonOrderTurnRequest(
                        conversation_id="conv-route-sqlite",
                        conversation_history=[
                            {"role": "user", "message": "En kebabpizza"}
                        ],
                    ),
                    context,
                )

            _state_repository.cache_clear()
            self.assertEqual(response.action, "confirm_delta")
            self.assertEqual(response.cart[0].official_name, "Kebab Pizza")


if __name__ == "__main__":
    unittest.main()
