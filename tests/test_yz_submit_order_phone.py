import os


os.environ.setdefault("RESTAURANT_TIMEZONE", "Europe/Stockholm")
os.environ.setdefault("ELEVENLABS_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault(
    "SVIR_INTERNAL_API_SECRET",
    "test-internal-secret-with-at-least-thirty-two-characters",
)
os.environ.setdefault("ELEVENLABS_API_KEY", "test-api-key")
os.environ.setdefault("ELEVENLABS_TEMPLATE_AGENT_ID", "test-agent")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from app.schemas.restaurant_tools_v2 import SubmitOrderV2Request
from app.services.elevenlabs_tool_definitions import (
    build_yz_submit_order_v2_tool_config,
)
from app.services.restaurant_order_submitter import (
    _normalize_customer_phone,
)


def test_submit_order_accepts_a_hidden_or_missing_caller_id() -> None:
    request = SubmitOrderV2Request.model_validate(
        {
            "conversation_id": "conv-hidden-caller-id",
            "order_items": [{"name": "Bibimbap", "quantity": 1}],
        }
    )

    assert request.customer_phone is None
    assert _normalize_customer_phone(request.customer_phone) == ""
    assert _normalize_customer_phone("unknown") == ""
    assert _normalize_customer_phone("+46701234567") == "46701234567"


def test_submit_tool_does_not_require_a_caller_id() -> None:
    config = build_yz_submit_order_v2_tool_config(
        "svir_tool_" + "a" * 64
    )
    body = config["api_schema"]["request_body_schema"]

    assert body["required"] == ["conversation_id", "order_items"]
    assert (
        body["properties"]["customer_phone"]["dynamic_variable"]
        == "system__caller_id"
    )
