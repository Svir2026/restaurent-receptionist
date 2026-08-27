from __future__ import annotations

import os
from datetime import datetime


os.environ.setdefault("RESTAURANT_TIMEZONE", "Europe/Stockholm")
os.environ.setdefault("ELEVENLABS_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault(
    "SVIR_INTERNAL_API_SECRET",
    "test-internal-secret-with-at-least-thirty-two-characters",
)
os.environ.setdefault(
    "ELEVENLABS_TOOL_SHARED_SECRET",
    "test-tool-secret",
)
os.environ.setdefault("ELEVENLABS_API_KEY", "test-api-key")
os.environ.setdefault("ELEVENLABS_TEMPLATE_AGENT_ID", "test-agent")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from app.api.routes.restaurant_tools_v2 import (  # noqa: E402
    YZ_AFTER_HOURS_TRAINING_MODE_ENV_NAME,
    YZ_CLOSED_FIRST_MESSAGE,
    _build_yz_conversation_initiation_data,
)


def test_after_hours_calls_remain_closed_without_training_override(
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        YZ_AFTER_HOURS_TRAINING_MODE_ENV_NAME,
        raising=False,
    )

    payload = _build_yz_conversation_initiation_data(
        local_datetime=datetime.fromisoformat(
            "2026-08-27T21:15:00+02:00"
        )
    )

    dynamic_variables = payload["dynamic_variables"]
    assert dynamic_variables["restaurant_is_open"] is False
    assert dynamic_variables["restaurant_training_mode"] is False
    assert payload["conversation_config_override"] == {
        "agent": {"first_message": YZ_CLOSED_FIRST_MESSAGE},
    }


def test_training_override_allows_yz_call_after_hours(monkeypatch) -> None:
    monkeypatch.setenv(
        YZ_AFTER_HOURS_TRAINING_MODE_ENV_NAME,
        "true",
    )

    payload = _build_yz_conversation_initiation_data(
        local_datetime=datetime.fromisoformat(
            "2026-08-27T21:15:00+02:00"
        )
    )

    dynamic_variables = payload["dynamic_variables"]
    assert dynamic_variables["restaurant_is_open"] is True
    assert dynamic_variables["restaurant_opening_status"] == "open"
    assert dynamic_variables["restaurant_training_mode"] is True
    assert "conversation_config_override" not in payload
