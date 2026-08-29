from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
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

from fastapi import BackgroundTasks

from app.api.routes.restaurant_tools_v2 import submit_order_v2
from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import SubmitOrderV2Request
from app.services.telnyx_order_sms import (
    YZ_RESTAURANT_ID,
    build_yz_order_confirmation_text,
    normalize_swedish_sms_recipient,
    prepare_yz_order_confirmation_sms,
    send_yz_order_confirmation_sms,
)


PROFILE_ID = "11111111-1111-4111-8111-111111111111"
MENU_ITEM_ID = UUID("22222222-2222-4222-8222-222222222222")


class TelnyxOrderSmsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "YZ_ORDER_SMS_ENABLED": "true",
                "TELNYX_SMS_API_KEY": "test-telnyx-key",
                "TELNYX_MESSAGING_PROFILE_ID": PROFILE_ID,
                "TELNYX_SMS_FROM": "YZ THAIWOK",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    @staticmethod
    def _context(
        restaurant_id: str = YZ_RESTAURANT_ID,
    ) -> ToolRestaurantContext:
        return ToolRestaurantContext(
            credential_id=UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            restaurant_id=UUID(restaurant_id),
            restaurant_name="YZ Thai Wok & Sushi",
            restaurant_slug="yz-thai-wok-sushi",
            restaurant_is_active=True,
            provisioning_job_id=None,
            provisioning_job_status=None,
            provisioning_current_step=None,
        )

    @staticmethod
    def _request(
        phone: str = "+46701234567",
    ) -> SubmitOrderV2Request:
        return SubmitOrderV2Request.model_validate(
            {
                "conversation_id": "conversation-123",
                "customer_name": "Anna",
                "customer_phone": phone,
                "order_type": "takeaway",
                "pickup_time": "2026-08-25T12:30:00+02:00",
                "order_items": [
                    {
                        "name": "Pad Thai med kyckling",
                        "quantity": 1,
                        "notes": "extra räkor",
                    }
                ],
            }
        )

    @staticmethod
    def _result(
        *,
        replay: bool = False,
        restaurant_id: str = YZ_RESTAURANT_ID,
        phone: str = "46701234567",
    ) -> dict[str, object]:
        return {
            "success": True,
            "idempotent_replay": replay,
            "restaurant_id": UUID(restaurant_id),
            "restaurant_name": "YZ Thai Wok & Sushi",
            "order_id": "v2_actual_order_123",
            "order_status": "new order",
            "order_type": "takeaway",
            "customer_name": "Anna",
            "created_at": datetime.now(timezone.utc),
            "dine_in_time": None,
            "pickup_time": datetime.now(timezone.utc),
            "currency": "SEK",
            "total": 145.0,
            "items": [
                {
                    "menu_item_id": MENU_ITEM_ID,
                    "requested_name": "Pad Thai med kyckling",
                    "official_name": "Pad Thai med kyckling",
                    "quantity": 1,
                    "unit_price": 145.0,
                    "line_total": 145.0,
                    "currency": "SEK",
                }
            ],
            "_sms_context": {
                "customer_phone": phone,
                "items": [
                    {
                        "official_name": "Pad Thai med kyckling",
                        "quantity": 1,
                        "notes": "extra räkor",
                    }
                ],
            },
        }

    def _call_route(
        self,
        result: dict[str, object],
        *,
        context: ToolRestaurantContext | None = None,
        request: SubmitOrderV2Request | None = None,
        caller_id: str | None = None,
    ) -> tuple[object, BackgroundTasks]:
        tasks = BackgroundTasks()
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=result,
        ):
            response = submit_order_v2(
                request or self._request(),
                tasks,
                context or self._context(),
                caller_id,
            )
        return response, tasks

    def test_yz_caller_id_header_fills_omitted_body_phone(self) -> None:
        request = self._request(phone=None)
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=self._result(phone="46701234567"),
        ) as submit_order:
            response = submit_order_v2(
                request,
                BackgroundTasks(),
                self._context(),
                "0701234567",
            )

        self.assertTrue(response.success)
        submitted_request = submit_order.call_args.kwargs["request"]
        self.assertEqual(
            submitted_request.customer_phone,
            "0701234567",
        )

    def test_blank_caller_id_keeps_hidden_number_orders_valid(self) -> None:
        request = self._request(phone=None)
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=self._result(phone=""),
        ) as submit_order:
            response = submit_order_v2(
                request,
                BackgroundTasks(),
                self._context(),
                "",
            )

        self.assertTrue(response.success)
        submitted_request = submit_order.call_args.kwargs["request"]
        self.assertIsNone(submitted_request.customer_phone)

    def test_yz_caller_id_header_never_overwrites_a_body_phone(self) -> None:
        request = self._request(phone="0701111111")
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=self._result(phone="46701111111"),
        ) as submit_order:
            submit_order_v2(
                request,
                BackgroundTasks(),
                self._context(),
                "0702222222",
            )

        submitted_request = submit_order.call_args.kwargs["request"]
        self.assertEqual(
            submitted_request.customer_phone,
            "0701111111",
        )

    def test_other_restaurants_ignore_the_yz_caller_id_header(self) -> None:
        other_id = "44444444-4444-4444-8444-444444444444"
        request = self._request(phone=None)
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=self._result(
                restaurant_id=other_id,
                phone="",
            ),
        ) as submit_order:
            submit_order_v2(
                request,
                BackgroundTasks(),
                self._context(other_id),
                "0701234567",
            )

        submitted_request = submit_order.call_args.kwargs["request"]
        self.assertIsNone(submitted_request.customer_phone)

    def test_new_successful_yz_order_schedules_one_sms(self) -> None:
        response, tasks = self._call_route(self._result())
        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 1)

    def test_idempotent_replay_schedules_no_sms(self) -> None:
        response, tasks = self._call_route(
            self._result(replay=True)
        )
        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)

    def test_different_restaurant_schedules_no_sms(self) -> None:
        other_id = "44444444-4444-4444-8444-444444444444"
        response, tasks = self._call_route(
            self._result(restaurant_id=other_id),
            context=self._context(other_id),
        )
        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)

    def test_disabled_feature_makes_no_telnyx_request(self) -> None:
        with patch.dict(
            os.environ,
            {"YZ_ORDER_SMS_ENABLED": "false"},
        ), patch(
            "app.services.telnyx_order_sms._post_telnyx_message"
        ) as post_message:
            response, tasks = self._call_route(self._result())

        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)
        post_message.assert_not_called()

    def test_missing_api_key_is_safe_and_logged(self) -> None:
        with patch.dict(
            os.environ,
            {"TELNYX_SMS_API_KEY": ""},
        ), self.assertLogs(
            "app.services.telnyx_order_sms",
            level="WARNING",
        ) as captured:
            response, tasks = self._call_route(self._result())

        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)
        self.assertIn(
            "reason=missing_telnyx_sms_api_key",
            " ".join(captured.output),
        )

    def test_invalid_phone_is_safe(self) -> None:
        response, tasks = self._call_route(
            self._result(phone="0812345678")
        )
        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)

    def test_telnyx_timeout_does_not_propagate(self) -> None:
        candidate = prepare_yz_order_confirmation_sms(
            success=True,
            idempotent_replay=False,
            restaurant_id=YZ_RESTAURANT_ID,
            order_id="v2_actual_order_123",
            customer_phone="0701234567",
            items=[
                {
                    "official_name": "Yakiniku",
                    "quantity": 1,
                    "notes": None,
                }
            ],
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.order_id, "v2_actual_order_123")
        self.assertNotIn("v2_actual_order_123", candidate.text)

        with patch(
            "app.services.telnyx_order_sms.urllib_request.urlopen",
            side_effect=TimeoutError,
        ), self.assertLogs(
            "app.services.telnyx_order_sms",
            level="WARNING",
        ) as captured:
            self.assertFalse(send_yz_order_confirmation_sms(candidate))

        self.assertIn(
            "reason=telnyx_timeout",
            " ".join(captured.output),
        )

    def test_telnyx_http_error_does_not_propagate(self) -> None:
        candidate = prepare_yz_order_confirmation_sms(
            success=True,
            idempotent_replay=False,
            restaurant_id=YZ_RESTAURANT_ID,
            order_id="v2_actual_order_123",
            customer_phone="46701234567",
            items=[
                {
                    "official_name": "Yakiniku",
                    "quantity": 1,
                    "notes": None,
                }
            ],
        )
        self.assertIsNotNone(candidate)

        http_error = HTTPError(
            "https://api.telnyx.com/v2/messages",
            503,
            "unavailable",
            None,
            None,
        )
        with patch(
            "app.services.telnyx_order_sms.urllib_request.urlopen",
            side_effect=http_error,
        ), self.assertLogs(
            "app.services.telnyx_order_sms",
            level="WARNING",
        ) as captured:
            self.assertFalse(send_yz_order_confirmation_sms(candidate))

        self.assertIn(
            "reason=telnyx_http_503",
            " ".join(captured.output),
        )

    def test_sms_content_has_no_price_or_currency(self) -> None:
        text = build_yz_order_confirmation_text(
            order_id="v2_actual_order_123",
            items=[
                {
                    "official_name": "Pad Thai - Kyckling",
                    "quantity": 1,
                    "notes": "extra räkor",
                    "unit_price": 145,
                    "line_total": 145,
                    "currency": "SEK",
                },
                {
                    "official_name": "24. Yakiniku",
                    "quantity": 2,
                    "notes": None,
                },
            ],
        )

        self.assertEqual(
            text,
            "Tack för din beställning hos Thai Wok & Sushi.\n\n"
            "Din order är registrerad:\n\n"
            "1x Pad Thai med kyckling och extra räkor\n"
            "2x Yakiniku\n\n"
            "Välkommen!",
        )
        self.assertIn(
            "1x Pad Thai med kyckling och extra räkor\n"
            "2x Yakiniku",
            text,
        )
        self.assertNotIn("(extra räkor)", text)
        self.assertNotIn(", 2x Yakiniku", text)
        self.assertNotIn("24. ", text)
        self.assertNotIn("v2_actual_order_123", text)
        self.assertEqual(text.splitlines()[-1], "Välkommen!")
        self.assertNotIn("145", text)
        self.assertNotIn("SEK", text)
        self.assertNotIn("total", text.lower())
        self.assertNotIn("pris", text.lower())

    def test_sms_preserves_hyphenated_non_protein_name(self) -> None:
        text = build_yz_order_confirmation_text(
            order_id="v2_actual_order_123",
            items=[
                {
                    "official_name": "Sweet-and-sour Special",
                    "quantity": 1,
                    "notes": None,
                }
            ],
        )

        self.assertIn("1x Sweet-and-sour Special", text)

    def test_retry_pair_schedules_only_once(self) -> None:
        first_response, first_tasks = self._call_route(self._result())
        replay_response, replay_tasks = self._call_route(
            self._result(replay=True)
        )

        self.assertTrue(first_response.success)
        self.assertTrue(replay_response.success)
        self.assertEqual(
            len(first_tasks.tasks) + len(replay_tasks.tasks),
            1,
        )

    def test_unexpected_scheduler_error_keeps_order_success(self) -> None:
        tasks = BackgroundTasks()
        with patch(
            "app.api.routes.restaurant_tools_v2."
            "submit_restaurant_order",
            return_value=self._result(),
        ), patch(
            "app.api.routes.restaurant_tools_v2."
            "prepare_yz_order_confirmation_sms",
            side_effect=RuntimeError("test"),
        ):
            response = submit_order_v2(
                self._request(),
                tasks,
                self._context(),
            )

        self.assertTrue(response.success)
        self.assertEqual(len(tasks.tasks), 0)

    def test_phone_normalization_is_strict(self) -> None:
        self.assertEqual(
            normalize_swedish_sms_recipient("+46701234567"),
            "+46701234567",
        )
        self.assertEqual(
            normalize_swedish_sms_recipient("46701234567"),
            "+46701234567",
        )
        self.assertEqual(
            normalize_swedish_sms_recipient("0701234567"),
            "+46701234567",
        )
        self.assertIsNone(
            normalize_swedish_sms_recipient("08-123 45 67")
        )


if __name__ == "__main__":
    unittest.main()
